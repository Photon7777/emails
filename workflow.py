"""High-level workflow: fetch leads, save them, render emails, and send."""

from __future__ import annotations

from dataclasses import replace
import logging
from pathlib import Path
import time
from typing import Optional

from apollo_client import ApolloClient
from config import (
    Settings,
    ensure_local_folders,
    validate_resume_attachment,
    validate_sender_settings,
)
import db
from email_template import render_email
from gmail_client import GmailClient
from lead import Lead


logger = logging.getLogger(__name__)


class ColdEmailWorkflow:
    def __init__(self, settings: Settings):
        self.settings = settings
        ensure_local_folders(settings)

    def init_db(self) -> None:
        with db.connect(self.settings.database_path) as conn:
            db.init_db(conn)
        logger.info("SQLite database is ready at %s", self.settings.database_path)

    def fetch_leads(self, max_pages: Optional[int] = None, per_page: Optional[int] = None) -> dict[str, int]:
        """Fetch leads from Apollo, enrich missing emails, and save to SQLite."""

        settings = self.settings
        if max_pages is not None or per_page is not None:
            settings = replace(
                self.settings,
                apollo_fetch_max_pages=max_pages or self.settings.apollo_fetch_max_pages,
                apollo_fetch_per_page=per_page or self.settings.apollo_fetch_per_page,
            )

        client = ApolloClient(settings)
        raw_people = client.search_people()

        counts = {"inserted": 0, "updated": 0, "skipped_missing_email": 0, "skipped_non_us": 0}
        with db.connect(settings.database_path) as conn:
            db.init_db(conn)
            for person in raw_people:
                lead = client.normalize_person(person)
                if not self._lead_is_allowed_by_location(lead):
                    counts["skipped_non_us"] += 1
                    logger.info(
                        "Skipping non-U.S. lead before storage/enrichment: %s at %s (%s)",
                        lead.full_name or lead.first_name or "unknown",
                        lead.company_name or "unknown company",
                        lead.country or "unknown country",
                    )
                    continue
                self._infer_us_country_from_filters(lead)

                if not lead.email and settings.apollo_enrich_missing_emails:
                    try:
                        lead = client.enrich_lead(lead)
                    except Exception as exc:
                        logger.exception("Apollo enrichment failed for %s: %s", lead.full_name, exc)

                if not self._lead_is_allowed_by_location(lead):
                    counts["skipped_non_us"] += 1
                    logger.info(
                        "Skipping non-U.S. lead after enrichment: %s at %s (%s)",
                        lead.full_name or lead.first_name or "unknown",
                        lead.company_name or "unknown company",
                        lead.country or "unknown country",
                    )
                    continue
                self._infer_us_country_from_filters(lead)

                if not lead.email:
                    lead.status = "skipped"
                    lead.error_message = "Apollo did not return an email address for this lead"
                    counts["skipped_missing_email"] += 1
                else:
                    lead.status = "pending"

                action = db.upsert_lead(conn, lead)
                counts[action] += 1

            db.export_to_csv(conn, settings.leads_csv_path)

        logger.info(
            "Lead fetch complete: %s inserted, %s updated, %s skipped without email, %s skipped non-U.S.",
            counts["inserted"],
            counts["updated"],
            counts["skipped_missing_email"],
            counts["skipped_non_us"],
        )
        logger.info("Exported lead CSV to %s", settings.leads_csv_path)
        return counts

    def preview_emails(self, limit: int = 3, output_path=None) -> None:
        """Print rendered emails without sending anything and save a preview file."""

        with db.connect(self.settings.database_path) as conn:
            db.init_db(conn)
            leads = db.get_pending_leads(conn, limit)

        if not leads:
            print("No pending leads with email addresses found.")
            return

        chunks = []
        for index, row in enumerate(leads, start=1):
            lead = Lead.from_row(row)
            subject, body = render_email(lead, self.settings)
            location = ", ".join(part for part in [lead.city, lead.state, lead.country] if part)
            chunks.append(
                "\n".join(
                    [
                        "=" * 72,
                        f"Preview {index}: {lead.email}",
                        f"Company: {lead.company_name}",
                        f"Location: {location}",
                        f"Subject: {subject}",
                        f"Attachments: {', '.join(str(path) for path in self._attachment_paths()) or 'none'}",
                        "-" * 72,
                        body,
                    ]
                )
            )

        preview_text = "\n".join(chunks)
        print(preview_text)

        path = Path(output_path) if output_path else self.settings.email_preview_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(preview_text + "\n", encoding="utf-8")
        logger.info("Saved email previews to %s", path)

    def send_pending(self, dry_run: bool, limit: Optional[int] = None) -> dict[str, int]:
        """Send pending emails through Gmail API while respecting daily limits."""

        validate_sender_settings(self.settings)
        validate_resume_attachment(self.settings)
        counts = {"sent": 0, "failed": 0, "skipped": 0, "dry_run": 0}
        attachment_paths = self._attachment_paths()

        with db.connect(self.settings.database_path) as conn:
            db.init_db(conn)
            sent_today = db.count_sent_today(conn)
            if self.settings.daily_send_limit <= 0:
                send_limit = limit if limit is not None else 1_000_000
            else:
                remaining_today = max(self.settings.daily_send_limit - sent_today, 0)
                requested_limit = limit if limit is not None else self.settings.daily_send_limit
                send_limit = min(requested_limit, remaining_today)

            if send_limit <= 0:
                logger.info(
                    "Daily send limit reached: %s sent today, limit is %s",
                    sent_today,
                    self.settings.daily_send_limit,
                )
                return counts

            pending_rows = db.get_pending_leads(conn, send_limit)
            suppression_items = db.read_suppression_list(self.settings.suppression_list_path)

            if not pending_rows:
                logger.info("No pending leads with email addresses are ready to send")
                return counts

            gmail = None if dry_run else GmailClient(self.settings)

            for index, row in enumerate(pending_rows, start=1):
                lead = Lead.from_row(row)
                lead_id = row["id"]

                if db.is_suppressed(lead.email, suppression_items):
                    db.mark_skipped(conn, lead_id, "Email or domain is in the suppression list")
                    counts["skipped"] += 1
                    continue

                if db.email_already_sent(conn, lead.email_lower, lead_id):
                    db.mark_skipped(conn, lead_id, "Duplicate email already sent previously")
                    counts["skipped"] += 1
                    continue

                if not self._lead_is_allowed_by_location(lead):
                    db.mark_skipped(conn, lead_id, "Contact location is not United States")
                    counts["skipped"] += 1
                    continue

                subject, body = render_email(lead, self.settings)

                if dry_run:
                    logger.info(
                        "DRY RUN: would send to %s with subject %r and %s attachment(s)",
                        lead.email,
                        subject,
                        len(attachment_paths),
                    )
                    counts["dry_run"] += 1
                    continue

                try:
                    gmail_message_id = gmail.send_email(
                        lead.email,
                        subject,
                        body,
                        attachment_paths=attachment_paths,
                    )
                    db.mark_sent(conn, lead_id, gmail_message_id)
                    counts["sent"] += 1
                except Exception as exc:
                    logger.exception("Failed to send email to %s: %s", lead.email, exc)
                    db.mark_failed(conn, lead_id, str(exc))
                    counts["failed"] += 1

                if index < len(pending_rows) and self.settings.delay_between_emails_seconds > 0:
                    time.sleep(self.settings.delay_between_emails_seconds)

        logger.info(
            "Send complete: %s sent, %s failed, %s skipped, %s dry-run",
            counts["sent"],
            counts["failed"],
            counts["skipped"],
            counts["dry_run"],
        )
        return counts

    def send_test(self, to_email: str, dry_run: bool) -> None:
        """Send one test email to yourself before contacting real leads."""

        validate_sender_settings(self.settings)
        validate_resume_attachment(self.settings)
        attachment_paths = self._attachment_paths()
        lead = Lead(
            first_name="Sai",
            full_name="Sai Test",
            email=to_email,
            title="Hiring Manager",
            company_name="Example Data Company",
            company_industry="data and analytics",
            reason_for_outreach="This is a test message so you can inspect formatting before sending live outreach.",
        )
        subject, body = render_email(lead, self.settings)

        if dry_run:
            print(f"DRY RUN test email to {to_email}")
            print(f"Subject: {subject}")
            print(f"Attachments: {', '.join(str(path) for path in attachment_paths) or 'none'}")
            print(body)
            return

        gmail = GmailClient(self.settings)
        message_id = gmail.send_email(to_email, subject, body, attachment_paths=attachment_paths)
        logger.info("Sent Gmail test message %s to %s", message_id, to_email)

    def _attachment_paths(self) -> list:
        if not self.settings.attach_resume:
            return []
        return [self.settings.resume_file]

    def _lead_is_allowed_by_location(self, lead: Lead) -> bool:
        requested_locations = {item.strip().lower() for item in self.settings.apollo_person_locations}
        us_only = requested_locations == {"united states"}
        if not us_only:
            return True
        if not lead.country.strip():
            return True
        return lead.country.strip().lower() in {
            "united states",
            "united states of america",
            "usa",
            "us",
        }

    def _infer_us_country_from_filters(self, lead: Lead) -> None:
        requested_locations = {item.strip().lower() for item in self.settings.apollo_person_locations}
        if requested_locations == {"united states"} and not lead.country.strip():
            lead.country = "United States"

    def export_csv(self) -> None:
        with db.connect(self.settings.database_path) as conn:
            db.init_db(conn)
            db.export_to_csv(conn, self.settings.leads_csv_path)
        logger.info("Exported lead CSV to %s", self.settings.leads_csv_path)

    def status_report(self) -> dict[str, int]:
        with db.connect(self.settings.database_path) as conn:
            db.init_db(conn)
            counts = db.status_counts(conn)
            sent_today = db.count_sent_today(conn)

        print("Lead status counts:")
        if counts:
            for status, count in counts.items():
                print(f"  {status}: {count}")
        else:
            print("  No leads yet.")
        if self.settings.daily_send_limit <= 0:
            print(f"Sent today: {sent_today}/unlimited")
        else:
            print(f"Sent today: {sent_today}/{self.settings.daily_send_limit}")
        return counts
