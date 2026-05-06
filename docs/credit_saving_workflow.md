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

Narrow Apollo search
  -> U.S. location filters
  -> relevant recruiter/data/hiring titles
  -> relevant company size filters
  -> relevant job posting title filters

Qualification gate
  -> normalize company/contact identity
  -> skip duplicates and already-contacted companies
  -> score company/contact fit
  -> try free/local email fallbacks

Budgeted Apollo enrichment
  -> only if score >= threshold
  -> only if no reliable email exists
  -> only while daily credit budget remains

Outreach queue
  -> pending leads only
  -> preview file
  -> Gmail API sender at 8:00 AM
```

## Step-by-Step Workflow

1. Load `.env`, local SQLite, CSV export, and blocklists.
2. Backfill normalized keys for older rows.
3. Run narrow Apollo search with existing filters. This returns candidate people, but the workflow does not enrich them yet.
4. Normalize identity fields:
   - company name
   - company domain
   - LinkedIn URL
   - email
5. Skip immediately if the company/contact matches:
   - local SQLite history
   - CSV export or manual sheet dump
   - `do_not_contact.txt`
   - `already_contacted.txt`
   - `suppression_list.txt`
   - sent, rejected, bounced, not relevant, or unsubscribed rows
6. Score remaining candidates from 0 to 100.
7. Reject low-fit candidates before any enrichment.
8. If the lead already has a reliable email from search or local data, queue it as `pending`.
9. If the lead has no email, use Apollo enrichment only when:
   - the company/contact score passes the threshold
   - the company has not been contacted
   - the role title is relevant
   - the daily Apollo credit budget is not exhausted
10. Save fresh previews.
11. The morning sender sends only `pending` leads with emails through Gmail API.

## Python Implementation Approach

Key modules:

- `lead.py`: lead model plus normalization helpers.
- `lead_scoring.py`: scoring model and score breakdown JSON.
- `db.py`: SQLite schema, migrations, dedupe checks, blocklists, CSV identity checks, and Apollo usage tracking.
- `apollo_client.py`: narrow Apollo search and enrichment calls.
- `workflow.py`: orchestrates the credit-saving pipeline.
- `gmail_client.py`: sends email through Gmail API.

The main change is in `workflow.fetch_leads()`:

- Apollo search results are treated as candidates.
- Dedupe and score checks happen before `client.enrich_lead()`.
- `db.count_apollo_credits_today()` guards enrichment.
- `db.record_apollo_usage()` logs each enrichment attempt.

## SQLite Schema

The `leads` table includes:

```text
company_name
company_domain
normalized_company_name
normalized_domain
contact_name through first_name, last_name, full_name
title
email
email_lower
email_source
linkedin_url
normalized_linkedin_url
company_industry
company_size
city
state
country
lead_score
score_breakdown
apollo_used
apollo_credits_used
status
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

Useful statuses:

```text
pending
sent
failed
skipped
rejected
needs_email
needs_enrichment
bounced
not_relevant
unsubscribed
```

## Lead Scoring Model

Default threshold:

```bash
LEAD_SCORE_THRESHOLD=70
```

Score breakdown:

```text
Industry fit: 0-25
Role relevance: 0-25
Location fit: 0-15
Company size fit: 0-10
Hiring signal: 0-15
Contact quality: 0-10
```

Apollo enrichment is skipped unless the total score is at or above the threshold.

## Apollo Credit-Saving Logic

Apollo enrichment is allowed only when all are true:

- The lead is not in local SQLite.
- The lead is not in the CSV identity index.
- The company/contact is not in any blocklist.
- The company has not already been emailed.
- The lead is U.S.-based or inferred U.S.-based from the configured filters.
- The lead score is at or above `LEAD_SCORE_THRESHOLD`.
- The lead has no reliable email from cheaper sources.
- The daily Apollo credit budget has remaining capacity.

Apollo enrichment is skipped for:

- duplicate companies
- duplicate contacts
- already-contacted companies
- rejected or not-relevant companies
- bounced or unsubscribed records
- low-score leads
- non-U.S. contacts
- contacts with irrelevant titles

## Daily Credit Budgeting

Default:

```bash
APOLLO_DAILY_CREDIT_LIMIT=25
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
raw_candidates = apollo.search_people_with_strict_filters()
credits_used = db.count_apollo_credits_today()

for candidate in raw_candidates:
    lead = normalize(candidate)

    if not is_us_lead(lead):
        continue

    if matches_blocklist(lead):
        continue

    if exists_in_db_or_csv(lead):
        continue

    score = score_lead(lead)
    if score < LEAD_SCORE_THRESHOLD:
        save_as_rejected(lead)
        continue

    if lead.email:
        save_as_pending(lead)
        continue

    try_free_email_fallbacks(lead)
    if lead.email:
        save_as_pending(lead)
        continue

    if credits_used >= APOLLO_DAILY_CREDIT_LIMIT:
        save_as_needs_enrichment(lead)
        continue

    enriched = apollo.enrich(lead)
    credits_used += 1
    record_apollo_usage(lead)

    if enriched.email:
        save_as_pending(enriched)
    else:
        save_as_needs_email(enriched)
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
  lead_scoring.py
  db.py
  apollo_client.py
  gmail_client.py
  email_template.py
  workflow.py
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

- searches with strict Apollo filters
- skips duplicates and blocked companies
- scores leads
- spends at most `APOLLO_DAILY_CREDIT_LIMIT`
- writes previews
- does not send email

The 8:00 AM job:

- sends only `pending` leads
- respects Gmail settings and delay controls
- attaches the configured resume
- logs each result

Recommended conservative settings:

```bash
APOLLO_DAILY_CREDIT_LIMIT=25
LEAD_SCORE_THRESHOLD=70
DAILY_SEND_LIMIT=25
DELAY_BETWEEN_EMAILS_SECONDS=45
ALLOW_UNVERIFIED_EMAIL_PATTERNS=false
```

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
