# Python Cold Email Workflow for macOS

This project finds U.S.-based, remote, and DMV-preferred recruiter, founder, hiring manager, and data/AI team contacts, stores leads locally, renders personalized full-time job outreach, and sends messages through the Gmail API.

It is designed for a MacBook and a beginner-friendly Python setup. The default config in `.env.example` is safe: `DRY_RUN=true`, so no real outreach is sent until you explicitly switch to live sending.

The workflow is Apollo-credit conscious: it checks local history, CSV exports, blocklists, company/contact duplicates, and lead score before using Apollo enrichment.

## What Is Not Committed

Keep these files local only, even in a private GitHub repo:

- `.env`
- `credentials.json`
- `token.json`
- `data/leads.sqlite`
- `data/leads_export.csv`
- `data/email_previews.txt`
- `data/suppression_list.txt`
- `logs/*`
- resume PDFs and other attachments

The `.gitignore` is set up to protect those files.

## Project Structure

```text
emails/
  .env.example
  .gitignore
  README.md
  requirements.txt
  main.py
  config.py
  logging_setup.py
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
  migrate_sqlite_to_cloud.py
  dashboard.py
  docs/
    credit_saving_workflow.md
  data/
    .gitkeep
  logs/
    .gitkeep
  templates/
    internship_outreach.txt
  scripts/
    run_daily.sh
    run_discover_nightly.sh
    run_send_morning.sh
  launchd/
    com.sai.cold-email-discover.plist
    com.sai.cold-email-send.plist
```

Generated data, previews, logs, credentials, and OAuth tokens are created locally when you run the workflow.

## Features

- Uses Python on macOS.
- Stores the Apollo API key in `.env`.
- Uses Gmail API OAuth with `credentials.json` and `token.json`.
- Searches Apollo by hiring/contact title, U.S./remote/DMV-preferred location, company size, keywords, and industry/company filters.
- Filters every candidate to U.S., remote U.S., DMV, or target-hub locations before enrichment or sending.
- Scores leads before enrichment and only enriches high-fit net-new leads.
- Applies a daily Apollo enrichment credit budget.
- Checks SQLite, CSV export, suppression, do-not-contact, and already-contacted lists before paid enrichment.
- Stores leads in SQLite and exports CSV.
- Avoids duplicate contacts by email and Apollo ID.
- Skips contacts without usable email addresses.
- Uses tiered Apollo fallback search when strict full-time filters return too few candidates.
- Tracks each lead as `raw`, `rejected`, `enriched`, `send_ready`, `sent`, `failed`, or `skipped`.
- Records `automation_runs`, `email_events`, and Apollo tier debug logs in SQLite.
- Includes a local Streamlit dashboard for full-time outreach monitoring.
- Generates personalized emails using first name, company, role, industry, and company-specific reason.
- Includes sender identity in every email.
- Supports resume PDF attachments.
- Applies daily limits, delays, retries, error handling, and logging.
- Supports split scheduling: discover leads at 9:00 PM and dry-run the sender at 8:00 AM.

## Install

```bash
git clone git@github.com:YOUR_GITHUB_USERNAME/emails.git
cd emails

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

Open `.env` and fill in your real values:

- `APOLLO_API_KEY`
- `SENDER_NAME`
- `SENDER_EMAIL`
- `SENDER_ROLE`
- `SENDER_LINKEDIN` or `SENDER_PORTFOLIO`
- search filters
- resume path
- daily send settings

Keep this value until previews look right:

```bash
DRY_RUN=true
```

## Gmail API OAuth Setup

Official Google docs:

- [Gmail Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python)
- [Gmail users.messages.send](https://developers.google.com/gmail/api/reference/rest/v1/users.messages/send)

Steps:

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create or select a Google Cloud project.
3. Enable the Gmail API.
4. Configure the OAuth consent screen.
5. Add your Gmail address as a test user if the app is in testing mode.
6. Create an OAuth Client ID.
7. Choose `Desktop app`.
8. Download the OAuth JSON file.
9. Save it in the project root as `credentials.json`.

Then authorize Gmail once:

```bash
source .venv/bin/activate
python main.py gmail-auth
```

This opens a browser, asks you to approve Gmail sending access, and creates `token.json`.

If you change Gmail scopes later, delete `token.json` and run:

```bash
python main.py gmail-auth
```

## Apollo API Setup

Official Apollo docs:

- [Create API Keys](https://docs.apollo.io/docs/create-api-key)
- [Test API Key](https://docs.apollo.io/docs/test-api-key)
- [People API Search](https://docs.apollo.io/reference/people-api-search)
- [People Enrichment](https://docs.apollo.io/reference/people-enrichment)
- [Organization Search](https://docs.apollo.io/reference/organization-search)

Steps:

1. In Apollo, go to Settings > Integrations.
2. Connect Apollo API.
3. Create an API key.
4. Give it access to People Search and People Enrichment.
5. Add the key to `.env` as `APOLLO_API_KEY`.
6. Leave `APOLLO_AUTH_MODE=x-api-key` unless your Apollo account requires bearer auth.

Test Apollo:

```bash
source .venv/bin/activate
python main.py apollo-test
```

Note: Apollo People Search may not return email addresses directly for every contact. This project can call People Enrichment for missing emails when `APOLLO_ENRICH_MISSING_EMAILS=true`. Enrichment may consume Apollo credits.

For the full credit-saving design, schema, pseudocode, and automation plan, see:

```text
docs/credit_saving_workflow.md
```

## Full-Time Job Lead Filters

The main workflow is intended for full-time Data Analyst, Data Engineer, AI Engineer, ML Engineer, BI, analytics, and related early-career roles. It prefers DMV and remote U.S. opportunities, but also allows strong U.S. hubs. Use these settings in `.env`:

```bash
APOLLO_FILTER_JOB_TITLES=Technical Recruiter,Recruiter,Talent Acquisition Specialist,Early Career Recruiter,New Grad Recruiter,Founder,Co-Founder,Hiring Manager,Data Analytics Manager,Business Intelligence Manager,Data Engineering Manager,Data Science Manager,Machine Learning Manager,AI Engineering Manager,Analytics Engineering Manager,Head of Data,Director of Analytics,Data Lead,AI Manager,ML Manager
APOLLO_TARGET_JOB_TITLES=Data Analyst,Business Analyst,BI Analyst,Product Analyst,Data Engineer,Analytics Engineer,AI Engineer,Machine Learning Engineer,Junior Data Scientist,Associate Data Scientist,Data Science Analyst,Cloud Data Engineer,Python SQL Analyst,Entry-Level AI Engineer,New Grad Data Analyst,Early Career Data Engineer
APOLLO_TARGET_JOB_LOCATIONS=United States,Remote,Remote United States,Washington DC,District of Columbia,DC,Maryland,MD,Virginia,VA,New York,Boston,San Francisco Bay Area,Seattle,Austin,Chicago,Atlanta,Dallas,Denver
APOLLO_FILTER_PERSON_LOCATIONS=United States,Washington DC,Maryland,Virginia
APOLLO_FILTER_LOCATIONS=United States,Remote,Remote United States,Washington DC,District of Columbia,Maryland,Virginia,Arlington,Alexandria,Fairfax,Tysons,Reston,Rockville,Bethesda,College Park,Silver Spring,Baltimore,Gaithersburg,Richmond,New York,Boston,San Francisco Bay Area,Seattle,Austin,Chicago,Atlanta,Dallas,Denver
APOLLO_FILTER_INDUSTRIES=computer software,financial services,healthcare,analytics,artificial intelligence,information technology,consulting,cloud,saas,fintech
APOLLO_FILTER_KEYWORDS=full-time,data analyst,business analyst,BI analyst,product analyst,data engineer,analytics engineer,AI engineer,machine learning engineer,new grad,entry level,early career,Python,SQL,ETL,data pipelines,dashboards,cloud
APOLLO_FILTER_COMPANY_SIZE_RANGES=1,10;11,50;51,200;201,500;501,1000;1001,5000;5001,10000;10001,20000
APOLLO_DAILY_CREDIT_LIMIT=25
DAILY_ENRICH_LIMIT=25
LEAD_SCORE_THRESHOLD=70
MIN_SCORE_TO_ENRICH=55
MIN_SCORE_TO_SEND=70
MAX_CONTACTS_PER_COMPANY_PER_WEEK=2
ALLOW_UNVERIFIED_EMAIL_PATTERNS=false
DAILY_SEND_LIMIT=30
DAILY_SEND_TARGET_MIN=25
PENDING_INVENTORY_TARGET=40
```

`APOLLO_FILTER_JOB_TITLES` describes the people to contact. `APOLLO_TARGET_JOB_TITLES` describes the full-time roles you are looking for at their companies.

Local location filtering normalizes common DMV variants plus U.S. hubs such as New York, Boston, San Francisco Bay Area, Seattle, Austin, Chicago, Atlanta, Dallas, and Denver. Non-U.S. and non-target leads are skipped before Apollo enrichment and before Gmail sending.

Company size ranges use semicolons because each Apollo range contains a comma.

For stricter company matching, set:

```bash
APOLLO_USE_ORGANIZATION_PREFILTER=true
```

That calls Apollo Organization Search first, then searches people at matching companies. Apollo says Organization Search may consume credits.

Credit-saving rules:

- Apollo enrichment happens only after duplicate checks and scoring.
- Apollo enrichment is allowed only for U.S., remote U.S., DMV, or target-hub leads.
- Companies already sent, rejected, bounced, unsubscribed, or marked not relevant are skipped.
- Work-email domains must match the company domain before sending.
- The sender only sends `send_ready` leads with emails.
- Run `python main.py rescore` after changing scoring rules to clean an older queue.
- `raw` can mean the lead passed early checks but enrichment was deferred by the daily credit budget.
- `rejected` records why a lead was held back, such as low score or missing email after enrichment.
- The morning sender is tuned for 25-30 emails/day when enough qualified full-time leads are send-ready. It will not bypass location, duplicate, score, suppression, full-time wording, or email-domain checks just to hit the target.

## Resume Attachment

Set these values in `.env`:

```bash
ATTACH_RESUME=true
RESUME_FILE=/absolute/path/to/Resume_SaiPraneeth.pdf
```

When `ATTACH_RESUME=true`, every live Gmail API send attaches the PDF. Dry runs only log the attachment count.

## Email Template

The template lives here:

```text
templates/internship_outreach.txt
```

It is written for full-time data analytics, data science, data engineering, and AI job outreach.

Available placeholders:

```text
{first_name}
{last_name}
{full_name}
{company_name}
{company_domain}
{role}
{industry}
{company_size}
{reason_for_outreach}
{company_specific_reason}
{company_fit_area}
{sender_name}
{sender_email}
{sender_role}
{sender_linkedin}
{sender_portfolio}
{sender_background}
```

The code automatically appends sender identity to every email.

## Tiered Discovery

Discovery runs four Apollo search tiers and stops once enough unique candidates are found:

1. DMV plus remote U.S. full-time data, analytics, and AI roles.
2. U.S. and remote companies with broader data, AI, BI, Python, SQL, cloud, ETL, and automation signals.
3. Remote U.S. full-time roles with the same data/AI keyword set.
4. Company-first warm search for fintech, healthcare tech, SaaS, AI, consulting, analytics, edtech, and cloud companies.

Apollo enrichment is never automatic for every search result. The workflow first dedupes locally, checks blocklists and company weekly limits, scores the raw lead, and only enriches missing emails when `score >= MIN_SCORE_TO_ENRICH`.

## Apollo Credit Dashboard

The Streamlit sidebar has a **Credits** page for Apollo accounting:

- Overview cards show base credits, top-ups, total available, used this month, used today, remaining credits, estimated credits saved, and average daily usage.
- Manual top-ups add `top_up` events to `apollo_credit_events`.
- Manual corrections add `adjustment` events and can be positive or negative.
- Usage history shows every `search`, `enrich`, `email_lookup`, `top_up`, and `adjustment` event for the selected month.
- Charts show daily usage, remaining credits over time, credits by event type, and top-ups over time.
- Forecast cards estimate monthly usage and whether current usage is sustainable.

Credit guardrails run before Apollo enrichment:

- More than 500 remaining credits: normal enrichment flow.
- 100 to 500 remaining credits: enrich only leads scoring at least 80.
- 100 or fewer remaining credits: pause enrichment and mark leads as `pending_credit_limit`.

Only Apollo actions that are expected to consume credits are logged as credit usage. Local scoring, filtering, deduplication, and rejected leads do not count as used credits. Rejected-before-enrichment leads are counted only in the estimated credits saved metric.

## UMD TA/RA Outreach

The project also includes a separate **UMD TA/RA Outreach** workflow for Teaching Assistant, Research Assistant, Grader, Course Support, Lab Assistant, and Faculty Assistant opportunities at the University of Maryland, College Park.

This workflow is intentionally separate from the main Apollo full-time workflow:

- It uses separate tables: `umd_ta_ra_contacts`, `umd_ta_ra_email_drafts`, `umd_ta_ra_outreach_logs`, and `umd_ta_ra_workflow_runs`.
- It does not use Apollo credits.
- It does not touch `leads`, `send_queue`, or the 8:00 AM main sender.
- It drafts emails for review and requires manual approval before anything can be sent.

Configuration:

```bash
UMD_TA_RA_MAX_PAGES=30
UMD_TA_RA_SEARCH_RESULTS_PER_QUERY=5
UMD_TA_RA_REQUEST_DELAY_SECONDS=1
UMD_TA_RA_MIN_FIT_SCORE=55
UMD_TA_RA_HIGH_FIT_SCORE=70
UMD_TA_RA_SEND_ENABLED=false
UMD_TA_RA_TARGET_CONTACTS=75
UMD_TA_RA_MAX_CONTACTS=100
UMD_TA_RA_DEFAULT_DAILY_LIMIT=40
UMD_TA_RA_MIN_SEND_DELAY_SECONDS=90
UMD_TA_RA_MAX_SEND_DELAY_SECONDS=240
```

Manual commands:

```bash
python main.py umd-discover --search-depth expanded --target-contacts 75 --max-contacts 100 --max-pages 120 --min-score 50
python main.py umd-create-campaign --name "UMD TA/RA Summer/Fall 2026 Outreach" --semester Both --min-score 65 --max-emails 40
python main.py umd-send-campaign --campaign-id 1 --dry-run --min-delay 90 --max-delay 240 --daily-limit 40
```

For live UMD TA/RA campaign sending, set `UMD_TA_RA_SEND_ENABLED=true`, approve drafts in the dashboard, and run:

```bash
python main.py umd-send-campaign --campaign-id 1 --live --min-delay 90 --max-delay 240 --daily-limit 40
```

The Streamlit dashboard has a sidebar page called **UMD TA/RA Outreach** where you can run expanded discovery, filter contacts, review/edit drafts, bulk generate drafts, bulk approve reviewed drafts, create campaigns, dry-run randomized send schedules, pause/resume/stop campaigns, and view UMD-specific logs. A separate optional launchd plist is provided at `launchd/com.sai.umd-ta-ra-discover.plist`; load it only if you want this UMD discovery workflow scheduled independently.

UMD fit buckets:

- `High Fit`: 80-100
- `Good Fit`: 65-79
- `Medium Fit`: 50-64
- `Low Fit`: below 50

Default bulk campaigns use only approved `High Fit` and `Good Fit` drafts unless you manually override the selection. Dry-run mode is the default and simulates the order, randomized delay schedule, skipped duplicates, and validation failures without sending Gmail messages.

UMD draft quality guardrails:

- Scraped faculty profile data is split into structured fields such as title, department, phone, office, research interests, courses, lab name, and profile URL.
- Email personalization is selected in this order: clean course title, clean research interest, clean department, then a generic fallback.
- Raw faculty card text, phone numbers, office locations, room numbers, and labels like `Contact`, `Phone`, `Email`, or `Office` are never used directly in email copy.
- Drafts are validated before approval. If a draft contains directory metadata or messy personalization, it is marked `needs_review` and must be regenerated or edited before approval.

## Manual Test Commands

Run these before scheduling anything:

```bash
cd /path/to/emails
source .venv/bin/activate

python main.py init-db
python main.py gmail-auth
python main.py apollo-test
python run_discovery.py
python main.py status
python main.py preview --limit 3
python main.py rescore
python run_sender.py
```

Preview output is saved locally at:

```text
data/email_previews.txt
```

Send one test email to yourself:

```bash
python main.py send-test --to your.email@gmail.com --dry-run
```

Send live outreach only after reviewing previews:

```bash
LIVE_SEND_CONFIRM=I_UNDERSTAND_SEND_LIVE_EMAILS python run_sender.py --live
```

Run nightly discovery manually:

```bash
python main.py discover --max-pages 1 --per-page 5 --preview-limit 10
```

Run the full workflow as a dry run:

```bash
python main.py run --dry-run --limit 2
```

Run the full workflow live:

```bash
python main.py run --live --limit 2
```

Open the dashboard:

```bash
streamlit run dashboard.py
```

Use the dashboard sidebar item `Daily Review` after the 9 PM discovery job runs. It shows contacts found that day, the explicit 8 AM send queue, email previews, rejection reasons, Apollo tier metadata, and safe review buttons that only update the local database.

## Deploying the Dashboard

The dashboard entry point is:

```text
dashboard.py
```

For Streamlit Community Cloud:

1. Push this repo to GitHub.
2. Create a new Streamlit app from the repo.
3. Set the main file path to `dashboard.py`.
4. Add required secrets/settings in the Streamlit secrets manager instead of committing `.env`.
5. Keep `credentials.json`, `token.json`, `.env`, `data/`, `logs/`, and resume PDFs private.

Important: the current dashboard reads from the local SQLite database at `DATABASE_PATH`. A cloud-hosted dashboard will not automatically see the SQLite database on your MacBook. To view live production analytics from anywhere, use one of these options:

- Host Streamlit on your Mac and access it through a private tunnel such as Tailscale or ngrok.
- Move workflow storage from local SQLite to a cloud database, then point both the Mac automation and deployed dashboard at that database.
- Periodically sync a sanitized SQLite/CSV snapshot to the deployment.

The safest current setup is local Streamlit plus private tunnel because it keeps Gmail tokens, Apollo keys, and lead data on your machine.

## Shared Cloud Database

To let the Mac automation and deployed dashboard read the same data in near real time, use a free Postgres-compatible cloud database such as Neon or Supabase.

Set this in `.env` on your Mac and in your Streamlit deployment secrets:

```bash
DATABASE_URL=postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require
```

When `DATABASE_URL` is blank, the workflow falls back to local SQLite at `DATABASE_PATH`. When `DATABASE_URL` is set, discovery, sender, dashboard, queue review, and automation run logs all use the cloud Postgres database.

One-time migration from the current local SQLite database:

```bash
python migrate_sqlite_to_cloud.py
```

Recommended setup:

1. Create a free Neon or Supabase Postgres database.
2. Copy the pooled/session connection string from the provider dashboard.
3. Add it to local `.env` as `DATABASE_URL`.
4. Run `pip install -r requirements.txt`.
5. Run `python migrate_sqlite_to_cloud.py`.
6. Run `python main.py status` to confirm the Mac is reading the cloud DB.
7. In Streamlit Community Cloud, add `DATABASE_URL` and the other non-file settings as secrets.
8. Deploy `dashboard.py`.

Apollo credit tracking settings:

```bash
BASE_MONTHLY_APOLLO_CREDITS=2630
APOLLO_CREDIT_RESET_DAY=1
ESTIMATED_CREDIT_COST_PER_ENRICHMENT=1
ENABLE_CREDIT_GUARDRAILS=true
MIN_APOLLO_CREDITS_RESERVE=100
```

The dashboard does not call Apollo just to check plan balance. It uses the workflow's `apollo_credit_events` ledger plus manual top-ups or adjustments entered on the **Credits** page.

Monthly available credits are calculated as:

```text
2630 base monthly credits + current-month top-ups + current-month adjustments
```

Monthly remaining credits are calculated as:

```text
available credits - workflow Apollo credit events for the selected month
```

Use the **Credits** page to add a top-up when you buy extra Apollo credits, or an adjustment when Apollo's official dashboard differs from the local ledger. Usage resets each month and unused base credits do not carry forward unless you enter a top-up or adjustment for that month.

Do not upload Gmail OAuth files, `.env`, the local SQLite file, logs, or resume PDFs to the dashboard host.

## Sending Controls

In `.env`:

```bash
DRY_RUN=true
DAILY_SEND_LIMIT=30
DAILY_SEND_TARGET_MIN=25
PENDING_INVENTORY_TARGET=40
DELAY_BETWEEN_EMAILS_SECONDS=10
MAX_RETRIES=3
APOLLO_DAILY_CREDIT_LIMIT=25
DAILY_ENRICH_LIMIT=25
BASE_MONTHLY_APOLLO_CREDITS=2630
ENABLE_CREDIT_GUARDRAILS=true
MIN_APOLLO_CREDITS_RESERVE=100
MIN_SCORE_TO_ENRICH=55
MIN_SCORE_TO_SEND=70
MAX_CONTACTS_PER_COMPANY_PER_WEEK=2
```

Useful values:

- `DRY_RUN=true`: preview/log only.
- `DRY_RUN=false`: allows live sending only when the sender is explicitly run live.
- `DAILY_SEND_LIMIT=30`: caps live sends per day.
- `DAILY_SEND_TARGET_MIN=25`: logs a warning if too few send-ready leads exist.
- `DELAY_BETWEEN_EMAILS_SECONDS=45`: waits between sends.
- `MAX_RETRIES=3`: retries transient Gmail/API failures.
- `DAILY_ENRICH_LIMIT=25`: caps daily Apollo enrichment attempts.
- `BASE_MONTHLY_APOLLO_CREDITS=2630`: default Apollo monthly credit allowance before top-ups.
- `ENABLE_CREDIT_GUARDRAILS=true`: pauses or narrows enrichment when credits are low.
- `MIN_APOLLO_CREDITS_RESERVE=100`: stops paid enrichment at the reserve threshold.
- `MIN_SCORE_TO_ENRICH=55`: prevents Apollo credits being spent on weak leads.
- `MIN_SCORE_TO_SEND=70`: only makes high-fit leads send-ready.
- `MAX_CONTACTS_PER_COMPANY_PER_WEEK=2`: avoids over-contacting the same company.

## Suppression List

When someone opts out, add their email or company domain to:

```text
data/suppression_list.txt
```

Examples:

```text
jane@example.com
example.com
```

The workflow skips suppressed emails and domains before sending.

Use these local files to save credits and avoid repeat outreach:

```text
data/do_not_contact.txt
data/already_contacted.txt
```

Each file accepts emails, domains, normalized company names, or LinkedIn URLs, one per line. Any match is skipped before Apollo enrichment.

## macOS launchd Setup

The current schedule uses two launchd jobs:

- `com.sai.cold-email-discover`: runs every night at 9:00 PM.
- `com.sai.cold-email-send`: dry-runs the sender every morning at 8:00 AM by default.

The discovery job fetches Apollo leads, exports CSV, and writes previews. The send job now uses `run_sender.py`, which defaults to dry-run. Live sending requires explicit confirmation.

For this Mac, the checked-in plists point to:

```text
/Users/saipraneethkmg/Library/Application Support/cold_email_workflow
```

That runtime location avoids macOS privacy restrictions that can block launchd jobs from reading files inside `Documents`.

Create or refresh the runtime copy:

```bash
SOURCE_DIR="/path/to/emails"
RUNTIME_DIR="$HOME/Library/Application Support/cold_email_workflow"

mkdir -p "$RUNTIME_DIR"
rsync -av \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude ".env" \
  --exclude "credentials.json" \
  --exclude "token.json" \
  --exclude "data/*" \
  --exclude "logs/*" \
  "$SOURCE_DIR/" "$RUNTIME_DIR/"

cp "$SOURCE_DIR/.env" "$RUNTIME_DIR/.env"
cp "$SOURCE_DIR/credentials.json" "$RUNTIME_DIR/credentials.json"
cp "$SOURCE_DIR/token.json" "$RUNTIME_DIR/token.json"

mkdir -p "$RUNTIME_DIR/data" "$RUNTIME_DIR/logs" "$RUNTIME_DIR/assets"
```

Create the runtime virtual environment:

```bash
cd "$RUNTIME_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x scripts/run_discover_nightly.sh scripts/run_send_morning.sh
```

Set `RESUME_FILE` in the runtime `.env` to a resume path launchd can read, for example:

```bash
RESUME_FILE=/Users/saipraneethkmg/Library/Application Support/cold_email_workflow/assets/Resume_SaiPraneeth.pdf
```

Install the jobs:

```bash
mkdir -p ~/Library/LaunchAgents
cp "$RUNTIME_DIR/launchd/com.sai.cold-email-discover.plist" ~/Library/LaunchAgents/
cp "$RUNTIME_DIR/launchd/com.sai.cold-email-send.plist" ~/Library/LaunchAgents/

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.sai.cold-email-discover.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.sai.cold-email-send.plist
launchctl enable gui/$(id -u)/com.sai.cold-email-discover
launchctl enable gui/$(id -u)/com.sai.cold-email-send
```

Run discovery immediately for a test:

```bash
launchctl kickstart -k gui/$(id -u)/com.sai.cold-email-discover
```

Run the sender immediately for a test:

```bash
launchctl kickstart -k gui/$(id -u)/com.sai.cold-email-send
```

Because the launchd sender uses dry-run by default, this test will not send live email unless you intentionally edit the runtime sender command and set live confirmation.

Check registration:

```bash
launchctl print gui/$(id -u)/com.sai.cold-email-discover
launchctl print gui/$(id -u)/com.sai.cold-email-send
```

Uninstall:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.sai.cold-email-discover.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.sai.cold-email-send.plist
rm ~/Library/LaunchAgents/com.sai.cold-email-discover.plist
rm ~/Library/LaunchAgents/com.sai.cold-email-send.plist
```

Important: launchd will not run a missed job if your MacBook is asleep at 9:00 PM or 8:00 AM. Keep the Mac awake, plugged in, or run the workflow manually when needed.

## Logging

Application log:

```bash
tail -f "$RUNTIME_DIR/logs/cold_email_workflow.log"
```

Nightly discovery log:

```bash
tail -f "$RUNTIME_DIR/logs/discover.log"
```

Morning send log:

```bash
tail -f "$RUNTIME_DIR/logs/send.log"
```

launchd stdout/stderr logs:

```bash
tail -f "$RUNTIME_DIR/logs/discover.launchd.out.log"
tail -f "$RUNTIME_DIR/logs/discover.launchd.err.log"
tail -f "$RUNTIME_DIR/logs/send.launchd.out.log"
tail -f "$RUNTIME_DIR/logs/send.launchd.err.log"
```

Check workflow status:

```bash
cd "$RUNTIME_DIR"
source .venv/bin/activate
python main.py status
```

SQLite checks:

```bash
sqlite3 "$RUNTIME_DIR/data/leads.sqlite" "select status, count(*) from leads group by status;"
sqlite3 "$RUNTIME_DIR/data/leads.sqlite" "select sent_at, email, company_name from leads where date(sent_at)=date('now','localtime') order by sent_at desc;"
```

Open generated files:

```bash
open "$RUNTIME_DIR/data/leads_export.csv"
open "$RUNTIME_DIR/data/email_previews.txt"
```

## Practical Sending Notes

- Use `preview` before live sending.
- Start with a small daily limit while testing.
- Keep a delay between emails.
- Make the message truthful and specific.
- Respect unsubscribe requests immediately.
- Check Gmail and Apollo account limits.
- For U.S. commercial outreach, review CAN-SPAM requirements, including accurate identity, non-deceptive subject lines, and a valid postal address when required.
