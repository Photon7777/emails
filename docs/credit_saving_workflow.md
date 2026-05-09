# Apollo Credit-Saving Workflow

This workflow prioritizes quality over volume. Apollo enrichment is treated as the expensive final step, not the first step.

## Updated Architecture

```text
Local history and blocklists
  -> SQLite leads table
  -> CSV export or imported sheet dump
  -> do_not_contact.txt
  -> already_contacted.txt
  -> suppression_list.txt

Tiered Apollo search
  -> strict DMV + remote internships
  -> broader DMV data/AI/company signals
  -> remote U.S. internships
  -> warm company-first search

Qualification gate
  -> reject non-DMV/non-remote candidates
  -> normalize company/contact identity
  -> skip duplicates and already-contacted companies
  -> score company/contact fit
  -> try free/local email fallbacks

Budgeted Apollo enrichment
  -> only if score >= threshold
  -> only if no reliable email exists
  -> only while daily credit budget remains

Outreach queue
  -> send_ready leads only
  -> preview file
  -> dry-run Gmail API sender at 8:00 AM unless live sending is explicitly confirmed
```

## Step-by-Step Workflow

1. Load `.env`, local SQLite, CSV export, and blocklists.
2. Backfill normalized keys for older rows.
3. Run tiered Apollo search. This returns candidate people, but the workflow does not enrich them yet.
4. Normalize identity fields:
   - company name
   - company domain
   - LinkedIn URL
   - email
5. Skip immediately if the company or role is outside Washington DC, Maryland, Virginia, or remote eligibility.
6. Skip immediately if the company/contact matches:
   - local SQLite history
   - CSV export or manual sheet dump
   - `do_not_contact.txt`
   - `already_contacted.txt`
   - `suppression_list.txt`
   - sent, rejected, bounced, not relevant, or unsubscribed rows
7. Enforce `MAX_CONTACTS_PER_COMPANY_PER_WEEK`.
8. Score remaining candidates from 0 to 100.
9. Reject candidates below `MIN_SCORE_TO_ENRICH` before any enrichment.
10. If the lead already has a reliable email from search or local data, confirm the work email domain matches the company domain, then queue it as `send_ready` only if `score >= MIN_SCORE_TO_SEND`.
11. If the lead has no email, use Apollo enrichment only when:
   - the lead is DMV-based or remote-eligible
   - the company/contact score is at least `MIN_SCORE_TO_ENRICH`
   - the company has not been contacted
   - the role title is relevant
   - the daily Apollo credit budget is not exhausted
12. Rescore after enrichment. Only `score >= MIN_SCORE_TO_SEND` becomes `send_ready`.
13. Save fresh previews and structured run logs.
14. The morning sender drafts/logs only by default.

When scoring rules change, run:

```bash
python main.py rescore
```

This applies the current score threshold, duplicate-company rules, and blocklists to older queued leads before the next sender run.

## Python Implementation Approach

Key modules:

- `lead.py`: lead model plus normalization helpers.
- `lead_scoring.py`: scoring model and score breakdown JSON.
- `dmv_location.py`: DMV/remote normalization and hard location gate.
- `search_tiers.py`: Apollo fallback tiers.
- `db.py`: SQLite schema, migrations, dedupe checks, blocklists, CSV identity checks, and Apollo usage tracking.
- `apollo_client.py`: narrow Apollo search and enrichment calls.
- `workflow.py`: orchestrates the credit-saving pipeline.
- `gmail_client.py`: sends email through Gmail API.
- `dashboard.py`: Streamlit monitoring UI.

The main change is in `workflow.fetch_leads()`:

- Apollo search results are treated as candidates.
- Dedupe and score checks happen before `client.enrich_lead()`.
- `db.count_apollo_credits_today()` guards enrichment.
- `db.record_apollo_usage()` logs each enrichment attempt.

## SQLite Schema

The `leads` table includes:

```text
company_name
role_title
company_domain
normalized_company_name
normalized_domain
contact_name
contact_title
first_name, last_name, full_name
title
email
email_lower
email_source
email_status
source_tier
linkedin_url
normalized_linkedin_url
company_industry
company_size
city
state
country
location_match
is_dmv
remote_dmv_eligible
internship_type
lead_score
score_breakdown
apollo_used
apollo_credits_used
status
rejection_reason
last_contacted_date
email_sent
reply_received
bounced
notes
created_at
updated_at
sent_at
skipped_at
raw_json
```

The `apollo_usage` table includes:

```text
used_at
operation
credits
lead_id
company_name
contact_name
notes
```

The `automation_runs`, `email_events`, and `apollo_search_logs` tables track daily run health, send/draft/skip/failure events, and Apollo tier/query performance for the dashboard.

Useful statuses:

```text
raw
enriched
send_ready
sent
failed
skipped
rejected
bounced
not_relevant
unsubscribed
```

## Lead Scoring Model

Default thresholds:

```bash
MIN_SCORE_TO_ENRICH=55
MIN_SCORE_TO_SEND=70
```

Score breakdown:

```text
DMV or remote fit: 0-25
Hiring/contact title relevance: 0-20
Company/role keyword fit: 0-20
Hiring signal: 0-15
Company size fit: 0-10
Email quality: 0-10
Penalties: -20 outside DMV/non-remote, -20 irrelevant title, -30 missing email after enrichment
```

Apollo enrichment is skipped unless the total score is at or above `MIN_SCORE_TO_ENRICH`. Send-ready status requires `MIN_SCORE_TO_SEND`.

## Apollo Credit-Saving Logic

Apollo enrichment is allowed only when all are true:

- The lead is not in local SQLite.
- The lead is not in the CSV identity index.
- The company/contact is not in any blocklist.
- The company has not already been emailed.
- The lead is in Washington DC, Maryland, Virginia, or is a remote U.S. role.
- The lead score is at or above `MIN_SCORE_TO_ENRICH`.
- The lead has no reliable email from cheaper sources.
- The daily Apollo credit budget has remaining capacity.

Apollo enrichment is skipped for:

- duplicate companies
- duplicate contacts
- already-contacted companies
- rejected or not-relevant companies
- bounced or unsubscribed records
- low-score leads
- non-DMV/non-remote contacts
- contacts with irrelevant titles
- work emails that do not match the company domain

## Daily Credit Budgeting

Default:

```bash
DAILY_ENRICH_LIMIT=25
```

Each `people/match` enrichment attempt records one estimated credit in `apollo_usage`.

Apollo account billing can vary by plan and endpoint, so this workflow logs an internal conservative estimate. Treat the Apollo dashboard as the billing source of truth.

Check usage:

```bash
python main.py status
sqlite3 data/leads.sqlite "select date(used_at,'localtime'), sum(credits) from apollo_usage group by date(used_at,'localtime');"
```

## Email Discovery Fallbacks Before Apollo

Implemented:

- SQLite duplicate/history checks.
- CSV identity checks.
- Suppression, do-not-contact, and already-contacted files.
- Apollo search email, when already returned without enrichment.
- Optional pattern guessing, disabled by default.

Configured but intentionally conservative:

```bash
ALLOW_UNVERIFIED_EMAIL_PATTERNS=false
```

If enabled, the workflow may create `first.last@companydomain.com` guesses and mark `email_source=unverified_pattern_guess`. Keep this disabled unless you verify those addresses before live sending.

Recommended future low-credit additions:

- Export Google Sheet to CSV before discovery and point `LEADS_CSV_PATH` or an import path to that file.
- Add a careers-page scanner that records hiring signals but never scrapes personal emails.
- Add a Gmail Sent sync that records already-contacted domains in `already_contacted.txt`.

## Pseudocode

```python
raw_candidates = apollo.search_people_with_fallback_tiers()
credits_used = db.count_apollo_credits_today()

for candidate in raw_candidates:
    lead = normalize(candidate)

    apply_dmv_location(lead)
    if not lead.is_dmv:
        continue

    if matches_blocklist(lead):
        continue

    if exists_in_db_or_csv(lead):
        continue

    score = score_lead(lead)
    if score < MIN_SCORE_TO_ENRICH:
        save_as_rejected(lead)
        continue

    if lead.email and score >= MIN_SCORE_TO_SEND:
        save_as_send_ready(lead)
        continue

    try_free_email_fallbacks(lead)
    if lead.email and score >= MIN_SCORE_TO_SEND:
        save_as_send_ready(lead)
        continue

    if credits_used >= DAILY_ENRICH_LIMIT:
        save_as_raw_deferred(lead)
        continue

    enriched = apollo.enrich(lead)
    credits_used += 1
    record_apollo_usage(lead)
    score = score_lead(enriched)

    if enriched.email and score >= MIN_SCORE_TO_SEND:
        save_as_send_ready(enriched)
    else:
        save_as_rejected_or_enriched_hold(enriched)
```

## Recommended Folder Structure

```text
emails/
  README.md
  .env.example
  requirements.txt
  main.py
  config.py
  lead.py
  dmv_location.py
  search_tiers.py
  lead_scoring.py
  db.py
  apollo_client.py
  gmail_client.py
  email_template.py
  workflow.py
  run_discovery.py
  run_sender.py
  dashboard.py
  docs/
    credit_saving_workflow.md
  templates/
    internship_outreach.txt
  data/
    .gitkeep
    leads.sqlite              # local only
    leads_export.csv          # local only
    suppression_list.txt      # local only
    do_not_contact.txt        # local only
    already_contacted.txt     # local only
  logs/
    .gitkeep
  scripts/
    run_discover_nightly.sh
    run_send_morning.sh
  launchd/
    com.sai.cold-email-discover.plist
    com.sai.cold-email-send.plist
```

## Safe Daily Automation Plan for macOS

Use two launchd jobs:

- 9:00 PM: discovery only
- 8:00 AM: Gmail API sender only

The 9:00 PM job:

- searches with tiered Apollo fallback filters
- falls back across four Apollo search tiers
- skips duplicates and blocked companies
- scores leads
- spends at most `DAILY_ENRICH_LIMIT`
- writes previews
- does not send email

The 8:00 AM job:

- drafts/logs only by default through `run_sender.py`
- sends only `send_ready` leads if live sending is explicitly confirmed
- assumes older queues were cleaned with `python main.py rescore` after scoring changes
- respects Gmail settings and delay controls
- attaches the configured resume
- logs each result

Recommended conservative settings:

```bash
DAILY_ENRICH_LIMIT=25
MIN_SCORE_TO_ENRICH=55
MIN_SCORE_TO_SEND=70
MAX_CONTACTS_PER_COMPANY_PER_WEEK=2
DAILY_SEND_LIMIT=30
DAILY_SEND_TARGET_MIN=25
PENDING_INVENTORY_TARGET=40
DELAY_BETWEEN_EMAILS_SECONDS=45
ALLOW_UNVERIFIED_EMAIL_PATTERNS=false
```

The sender aims for 25-30 emails per day when enough qualified DMV/remote leads are queued. If fewer than 25 send-ready leads exist, the workflow logs a warning instead of relaxing location, dedupe, score, suppression, or email-domain safeguards.

## Error Handling and Retry Logic

Apollo:

- transient rate-limit/server errors are retried with exponential backoff
- enrichment failures are logged and do not stop the full run
- daily budget stops enrichment cleanly

Gmail:

- OAuth token refresh is handled by `token.json`
- transient send socket errors are retried
- send failures mark the row as `failed`
- successful sends mark `email_sent=1` and `status=sent`

Database:

- schema migrations run during `init-db` and every workflow start
- normalized keys are backfilled for older rows
- duplicate checks run before enrichment and before sending

Logs:

- `logs/discover.log`
- `logs/send.log`
- `logs/cold_email_workflow.log`
- launchd stdout/stderr logs
