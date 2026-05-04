# Python Cold Email Workflow for macOS

This project finds U.S.-based recruiter, founder, hiring manager, and company contacts through Apollo, stores leads locally, renders personalized internship/job outreach, and sends messages through the Gmail API.

It is designed for a MacBook and a beginner-friendly Python setup. The default config in `.env.example` is safe: `DRY_RUN=true`, so no real outreach is sent until you explicitly switch to live sending.

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
  db.py
  apollo_client.py
  gmail_client.py
  email_template.py
  workflow.py
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
- Searches Apollo by job title, U.S. location, company size, keywords, and industry/company filters.
- Stores leads in SQLite and exports CSV.
- Avoids duplicate contacts by email and Apollo ID.
- Skips contacts without usable email addresses.
- Tracks each lead as `pending`, `sent`, `failed`, or `skipped`.
- Generates personalized emails using first name, company, role, industry, and company-specific reason.
- Includes sender identity and unsubscribe text in every email.
- Supports resume PDF attachments.
- Applies daily limits, delays, retries, error handling, and logging.
- Supports split scheduling: discover leads at 9:00 PM and send pending emails at 8:00 AM.

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

## Lead Filters

The current workflow is intended for U.S.-only internship/job outreach. Use these settings in `.env`:

```bash
APOLLO_FILTER_JOB_TITLES=University Recruiter,Campus Recruiter,Early Talent Recruiter,Technical Recruiter,Recruiter,Talent Acquisition Specialist,Founder,Co-Founder,Hiring Manager,Data Analytics Manager,Data Science Manager,Machine Learning Manager,AI Engineering Manager,Head of Data,Director of Analytics
APOLLO_TARGET_JOB_TITLES=data analyst intern,AI engineer intern,data analytics intern,machine learning intern,data science intern
APOLLO_TARGET_JOB_LOCATIONS=United States
APOLLO_FILTER_PERSON_LOCATIONS=United States
APOLLO_FILTER_LOCATIONS=United States
APOLLO_FILTER_INDUSTRIES=computer software,financial services,healthcare,analytics
APOLLO_FILTER_COMPANY_SIZE_RANGES=1,10;11,50;51,200;201,500;501,1000;1001,5000;5001,10000;10001,20000
```

`APOLLO_FILTER_JOB_TITLES` describes the people to contact. `APOLLO_TARGET_JOB_TITLES` describes the internship/job roles you are looking for at their companies.

Company size ranges use semicolons because each Apollo range contains a comma.

For stricter company matching, set:

```bash
APOLLO_USE_ORGANIZATION_PREFILTER=true
```

That calls Apollo Organization Search first, then searches people at matching companies. Apollo says Organization Search may consume credits.

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
{sender_location}
{sender_linkedin}
{sender_portfolio}
{sender_background}
```

The code automatically appends sender identity and unsubscribe text to every email.

## Manual Test Commands

Run these before scheduling anything:

```bash
cd /path/to/emails
source .venv/bin/activate

python main.py init-db
python main.py gmail-auth
python main.py apollo-test
python main.py fetch-leads --max-pages 1 --per-page 5
python main.py status
python main.py preview --limit 3
python main.py send --dry-run --limit 2
```

Preview output is saved locally at:

```text
data/email_previews.txt
```

Send one test email to yourself:

```bash
python main.py send-test --to your.email@gmail.com --live
```

Send one real pending outreach email only after reviewing previews:

```bash
python main.py send --live --limit 1
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

## Sending Controls

In `.env`:

```bash
DRY_RUN=true
DAILY_SEND_LIMIT=20
DELAY_BETWEEN_EMAILS_SECONDS=45
MAX_RETRIES=3
```

Useful values:

- `DRY_RUN=true`: preview/log only.
- `DRY_RUN=false`: allow scheduled jobs to send live email.
- `DAILY_SEND_LIMIT=0`: no daily cap, sends all pending contacts.
- `DELAY_BETWEEN_EMAILS_SECONDS=45`: waits between sends.
- `MAX_RETRIES=3`: retries transient Gmail/API failures.

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

## macOS launchd Setup

The current schedule uses two launchd jobs:

- `com.sai.cold-email-discover`: runs every night at 9:00 PM.
- `com.sai.cold-email-send`: sends pending emails every morning at 8:00 AM.

The discovery job fetches Apollo leads, exports CSV, and writes previews. The send job sends pending emails through Gmail API and attaches the resume when `ATTACH_RESUME=true`.

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
