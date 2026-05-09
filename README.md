# Python Cold Email Workflow for macOS

This project finds DMV-area and remote recruiter, founder, hiring manager, and company contacts, stores leads locally, renders personalized internship outreach, and sends messages through the Gmail API.

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
- Searches Apollo by job title, DMV/remote location, company size, keywords, and industry/company filters.
- Filters every candidate to Washington DC, Maryland, Virginia, or remote roles before enrichment or sending.
- Scores leads before enrichment and only enriches high-fit net-new leads.
- Applies a daily Apollo enrichment credit budget.
- Checks SQLite, CSV export, suppression, do-not-contact, and already-contacted lists before paid enrichment.
- Stores leads in SQLite and exports CSV.
- Avoids duplicate contacts by email and Apollo ID.
- Skips contacts without usable email addresses.
- Uses tiered Apollo fallback search when strict DMV filters return too few candidates.
- Tracks each lead as `raw`, `rejected`, `enriched`, `send_ready`, `sent`, `failed`, or `skipped`.
- Records `automation_runs`, `email_events`, and Apollo tier debug logs in SQLite.
- Includes a local Streamlit dashboard: InternReach AI Dashboard.
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

## DMV Internship Lead Filters

The current workflow is intended for DMV-only internship outreach plus remote roles. Use these settings in `.env`:

```bash
APOLLO_FILTER_JOB_TITLES=University Recruiter,Campus Recruiter,Early Talent Recruiter,Technical Recruiter,Recruiter,Talent Acquisition Specialist,Founder,Co-Founder,Hiring Manager,Data Analytics Manager,Data Science Manager,Machine Learning Manager,AI Engineering Manager,Head of Data,Director of Analytics
APOLLO_TARGET_JOB_TITLES=Data Analyst Intern,Data Scientist Intern,Data Engineer Intern,Business Analyst Intern,Analytics Intern,BI Intern,AI/ML Intern,Cloud/Data Intern
APOLLO_TARGET_JOB_LOCATIONS=Washington DC,District of Columbia,DC,Maryland,MD,Virginia,VA,Remote
APOLLO_FILTER_PERSON_LOCATIONS=Washington DC,Maryland,Virginia
APOLLO_FILTER_LOCATIONS=Washington DC,District of Columbia,Maryland,Virginia,Arlington,Alexandria,Fairfax,Tysons,Reston,Rockville,Bethesda,College Park,Silver Spring,Baltimore,Gaithersburg,Richmond
APOLLO_FILTER_INDUSTRIES=computer software,financial services,healthcare,analytics
APOLLO_FILTER_KEYWORDS=summer 2026 internship,data analyst,business analyst,analytics,AI,ML,cloud,data engineering
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

`APOLLO_FILTER_JOB_TITLES` describes the people to contact. `APOLLO_TARGET_JOB_TITLES` describes the internship/job roles you are looking for at their companies.

Local DMV filtering also normalizes common variants such as `DC`, `District of Columbia`, `MD`, `Maryland`, `VA`, `Virginia`, `Arlington`, `Alexandria`, `Fairfax`, `Tysons`, `Reston`, `Rockville`, `Bethesda`, `College Park`, `Silver Spring`, `Baltimore`, `Gaithersburg`, and `Richmond`. Non-DMV leads are skipped before Apollo enrichment and before Gmail sending.

Company size ranges use semicolons because each Apollo range contains a comma.

For stricter company matching, set:

```bash
APOLLO_USE_ORGANIZATION_PREFILTER=true
```

That calls Apollo Organization Search first, then searches people at matching companies. Apollo says Organization Search may consume credits.

Credit-saving rules:

- Apollo enrichment happens only after duplicate checks and scoring.
- Apollo enrichment is allowed only for DMV or remote-eligible leads.
- Companies already sent, rejected, bounced, unsubscribed, or marked not relevant are skipped.
- Work-email domains must match the company domain before sending.
- The sender only sends `send_ready` leads with emails.
- Run `python main.py rescore` after changing scoring rules to clean an older queue.
- `raw` can mean the lead passed early checks but enrichment was deferred by the daily credit budget.
- `rejected` records why a lead was held back, such as low score or missing email after enrichment.
- The morning sender is tuned for 25-30 emails/day when enough qualified DMV leads are send-ready. It will not bypass DMV, duplicate, score, suppression, or email-domain checks just to hit the target.

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

It is written for data analytics, data science, data engineering, and AI internship/job outreach.

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

1. Strict DMV + remote internship wording.
2. DMV companies with broader data, AI, BI, Python, SQL, cloud, and automation signals.
3. Remote U.S. internships with the same internship/data/AI keyword set.
4. Company-first warm search for fintech, healthcare tech, SaaS, AI, consulting, analytics, edtech, and cloud companies.

Apollo enrichment is never automatic for every search result. The workflow first dedupes locally, checks blocklists and company weekly limits, scores the raw lead, and only enriches missing emails when `score >= MIN_SCORE_TO_ENRICH`.

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
