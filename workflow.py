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
from lead_scoring import breakdown_json, score_lead


logger = logging.getLogger(__name__)


class ColdEmailWorkflow:
    def __init__(self, settings: Settings):
        self.settings = settings
        ensure_local_folders(settings)

    def init_db(self) -> None:
        with db.connect(self.settings.database_path) as conn:
            db.init_db(conn)
            db.backfill_normalized_keys(conn)
        logger.info("SQLite database is ready at %s", self.settings.database_path)

    def fetch_leads(self, max_pages: Optional[int] = None, per_page: Optional[int] = None) -> dict[str, int]:
        """Discover leads while protecting Apollo credits.

        Apollo search is used with narrow filters, but paid enrichment is only
        attempted after local duplicate checks, blocklists, scoring, and free
        email fallbacks have all passed.
        """

        settings = self.settings
        if max_pages is not None or per_page is not None:
            settings = replace(
                self.settings,
                apollo_fetch_max_pages=max_pages or self.settings.apollo_fetch_max_pages,
                apollo_fetch_per_page=per_page or self.settings.apollo_fetch_per_page,
            )

        client = ApolloClient(settings)
        raw_people = client.search_people()

        counts = {
            "searched": len(raw_people),
            "inserted": 0,
            "updated": 0,
            "pending": 0,
            "enriched": 0,
            "apollo_credits_used": 0,
            "credit_budget_hit": 0,
            "skipped_missing_email": 0,
            "skipped_non_us": 0,
            "skipped_duplicate": 0,
            "skipped_blocklist": 0,
            "rejected_low_score": 0,
        }
        with db.connect(settings.database_path) as conn:
            db.init_db(conn)
            db.backfill_normalized_keys(conn)
            suppression_items = db.read_suppression_list(settings.suppression_list_path)
            do_not_contact_items = db.read_blocklist(settings.do_not_contact_path)
            already_contacted_items = db.read_blocklist(settings.already_contacted_path)
            csv_identity_keys = db.read_csv_identity_keys(settings.leads_csv_path)
            block_items = suppression_items | do_not_contact_items | already_contacted_items
            credits_used_today = db.count_apollo_credits_today(conn)

            for person in raw_people:
                lead = client.normalize_person(person)
                lead.source = "apollo_search"
                lead.apollo_used = True
                lead.apollo_credits_used = 0
                if lead.email:
                    lead.email_source = "apollo_search"
                lead.refresh_normalized_fields()

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
                lead.refresh_normalized_fields()

                if db.lead_matches_blocklist(lead, block_items):
                    counts["skipped_blocklist"] += 1
                    logger.info("Skipping blocked lead/company before enrichment: %s at %s", lead.full_name, lead.company_name)
                    continue

                if db.lead_matches_identity_keys(lead, csv_identity_keys):
                    counts["skipped_duplicate"] += 1
                    logger.info("Skipping CSV duplicate before enrichment: %s at %s", lead.full_name, lead.company_name)
                    continue

                blocking_row = db.blocking_match(conn, lead)
                if blocking_row:
                    counts["skipped_duplicate"] += 1
                    logger.info(
                        "Skipping duplicate/already-contacted company before enrichment: %s at %s matched row %s with status %s",
                        lead.full_name or lead.first_name or "unknown",
                        lead.company_name or "unknown company",
                        blocking_row["id"],
                        blocking_row["status"],
                    )
                    continue

                existing_row = db.find_existing_lead(conn, lead)
                if existing_row:
                    counts["skipped_duplicate"] += 1
                    lead.notes = f"Duplicate local row {existing_row['id']}; not enriched to save Apollo credits"
                    action = db.upsert_lead(conn, lead)
                    counts[action] += 1
                    logger.info(
                        "Updated duplicate local lead without Apollo enrichment: %s at %s",
                        lead.full_name or lead.first_name or "unknown",
                        lead.company_name or "unknown company",
                    )
                    continue

                total_score, score_parts = score_lead(lead, settings)
                lead.lead_score = total_score
                lead.score_breakdown = breakdown_json(score_parts)

                if total_score < settings.lead_score_threshold:
                    lead.status = "rejected"
                    lead.error_message = f"Lead score {total_score} is below threshold {settings.lead_score_threshold}"
                    lead.notes = "Rejected before Apollo enrichment to save credits"
                    counts["rejected_low_score"] += 1
                    action = db.upsert_lead(conn, lead)
                    counts[action] += 1
                    continue

                self._try_free_email_fallbacks(lead)

                if not lead.email and settings.apollo_enrich_missing_emails:
                    if settings.apollo_daily_credit_limit >= 0 and credits_used_today >= settings.apollo_daily_credit_limit:
                        lead.status = "needs_enrichment"
                        lead.error_message = (
                            f"Apollo daily credit budget reached "
                            f"({credits_used_today}/{settings.apollo_daily_credit_limit})"
                        )
                        lead.notes = "Queued for a future day instead of spending more Apollo credits"
                        counts["credit_budget_hit"] += 1
                    else:
                        try:
                            lead = client.enrich_lead(lead)
                        except Exception as exc:
                            logger.exception("Apollo enrichment failed for %s: %s", lead.full_name, exc)
                            lead.error_message = str(exc)
                        finally:
                            credits_used_today += 1
                            lead.apollo_credits_used += 1
                            lead.apollo_used = True
                            counts["apollo_credits_used"] += 1
                            counts["enriched"] += 1
                            db.record_apollo_usage(
                                conn,
                                operation="people_match_enrichment",
                                credits=1,
                                lead=lead,
                                notes="Budgeted enrichment after dedupe and scoring",
                            )

                        if lead.email:
                            lead.email_source = "apollo_enrichment"
                        lead.refresh_normalized_fields()

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
                lead.refresh_normalized_fields()

                if not lead.email:
                    if lead.status != "needs_enrichment":
                        lead.status = "needs_email"
                        lead.error_message = "No reliable email found after free checks and allowed Apollo usage"
                    counts["skipped_missing_email"] += 1
                elif not self._email_domain_matches_company(lead):
                    lead.status = "needs_email"
                    lead.error_message = "Email domain does not match company domain"
                    lead.notes = "Held back before sending because contact email appears to belong to another company"
                    counts["skipped_missing_email"] += 1
                else:
                    lead.status = "pending"
                    counts["pending"] += 1

                action = db.upsert_lead(conn, lead)
                counts[action] += 1

            db.export_to_csv(conn, settings.leads_csv_path)

        logger.info(
            "Lead fetch complete: %s searched, %s inserted, %s updated, %s pending, "
            "%s enriched, %s Apollo credits used, %s budget hits, %s rejected low score, "
            "%s duplicates, %s blocklist, %s missing email, %s non-U.S.",
            counts["searched"],
            counts["inserted"],
            counts["updated"],
            counts["pending"],
            counts["enriched"],
            counts["apollo_credits_used"],
            counts["credit_budget_hit"],
            counts["rejected_low_score"],
            counts["skipped_duplicate"],
            counts["skipped_blocklist"],
            counts["skipped_missing_email"],
            counts["skipped_non_us"],
        )
        logger.info("Exported lead CSV to %s", settings.leads_csv_path)
        return counts

    def preview_emails(self, limit: int = 3, output_path=None) -> None:
        """Print rendered emails without sending anything and save a preview file."""

        with db.connect(self.settings.database_path) as conn:
            db.init_db(conn)
            db.backfill_normalized_keys(conn)
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
            db.backfill_normalized_keys(conn)
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
            do_not_contact_items = db.read_blocklist(self.settings.do_not_contact_path)
            already_contacted_items = db.read_blocklist(self.settings.already_contacted_path)
            block_items = suppression_items | do_not_contact_items | already_contacted_items

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

                if db.lead_matches_blocklist(lead, block_items):
                    db.mark_skipped(conn, lead_id, "Lead matched do-not-contact or already-contacted list")
                    counts["skipped"] += 1
                    continue

                if lead.email_source == "unverified_pattern_guess" and not self.settings.allow_unverified_email_patterns:
                    db.mark_skipped(conn, lead_id, "Unverified email pattern guesses are disabled")
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

                if lead.lead_score and lead.lead_score < self.settings.lead_score_threshold:
                    db.mark_skipped(conn, lead_id, "Lead score fell below the configured threshold")
                    counts["skipped"] += 1
                    continue

                if not self._email_domain_matches_company(lead):
                    db.mark_skipped(conn, lead_id, "Email domain does not match company domain")
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

    def rescore_existing_leads(self) -> dict[str, int]:
        """Apply the current quality gates to already-queued leads."""

        counts = {"rescored": 0, "rejected": 0, "blocked": 0, "duplicate_pending": 0}
        with db.connect(self.settings.database_path) as conn:
            db.init_db(conn)
            db.backfill_normalized_keys(conn)
            suppression_items = db.read_suppression_list(self.settings.suppression_list_path)
            do_not_contact_items = db.read_blocklist(self.settings.do_not_contact_path)
            already_contacted_items = db.read_blocklist(self.settings.already_contacted_path)
            block_items = suppression_items | do_not_contact_items | already_contacted_items

            rows = conn.execute(
                """
                SELECT *
                FROM leads
                WHERE status IN ('pending', 'needs_email', 'needs_enrichment')
                ORDER BY lead_score DESC, created_at ASC, id ASC
                """
            ).fetchall()

            pending_candidates = []
            for row in rows:
                lead = Lead.from_row(row)
                lead.refresh_normalized_fields()
                score, parts = score_lead(lead, self.settings)
                score_json = breakdown_json(parts)
                counts["rescored"] += 1

                if db.lead_matches_blocklist(lead, block_items):
                    db.update_lead_quality(
                        conn,
                        row["id"],
                        score,
                        score_json,
                        status="skipped",
                        error_message="Matched do-not-contact or already-contacted list during rescore",
                        notes="Removed from pending queue before send",
                    )
                    counts["blocked"] += 1
                    continue

                blocking_row = db.blocking_match(conn, lead)
                if blocking_row and blocking_row["id"] != row["id"]:
                    db.update_lead_quality(
                        conn,
                        row["id"],
                        score,
                        score_json,
                        status="skipped",
                        error_message=f"Company/contact already blocked by row {blocking_row['id']}",
                        notes="Removed from pending queue before send",
                    )
                    counts["blocked"] += 1
                    continue

                if score < self.settings.lead_score_threshold:
                    db.update_lead_quality(
                        conn,
                        row["id"],
                        score,
                        score_json,
                        status="rejected",
                        error_message=(
                            f"Lead score {score} is below threshold "
                            f"{self.settings.lead_score_threshold}"
                        ),
                        notes="Rejected by current scoring model before send",
                    )
                    counts["rejected"] += 1
                    continue

                if lead.email and not self._email_domain_matches_company(lead):
                    db.update_lead_quality(
                        conn,
                        row["id"],
                        score,
                        score_json,
                        status="skipped",
                        error_message="Email domain does not match company domain",
                        notes="Removed from pending queue before send",
                    )
                    counts["blocked"] += 1
                    continue

                db.update_lead_quality(conn, row["id"], score, score_json)
                pending_candidates.append((row["id"], lead, score))

            seen_company_keys = set()
            for lead_id, lead, score in sorted(pending_candidates, key=lambda item: item[2], reverse=True):
                key = lead.normalized_domain or lead.normalized_company_name
                if not key:
                    continue
                if key in seen_company_keys:
                    db.mark_skipped(conn, lead_id, "Duplicate pending company; kept the highest-scoring contact")
                    counts["duplicate_pending"] += 1
                    continue
                seen_company_keys.add(key)

        logger.info(
            "Rescore complete: %s rescored, %s rejected, %s blocked, %s duplicate pending skipped",
            counts["rescored"],
            counts["rejected"],
            counts["blocked"],
            counts["duplicate_pending"],
        )
        return counts

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

    def _email_domain_matches_company(self, lead: Lead) -> bool:
        if not lead.email or not lead.normalized_domain:
            return True
        email_domain = lead.email_lower.split("@")[-1] if "@" in lead.email_lower else ""
        if not email_domain:
            return False
        public_domains = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"}
        if email_domain in public_domains:
            return True
        company_domain = lead.normalized_domain
        return (
            email_domain == company_domain
            or email_domain.endswith(f".{company_domain}")
            or company_domain.endswith(f".{email_domain}")
        )

    def _try_free_email_fallbacks(self, lead: Lead) -> bool:
        """Try no-credit email fallbacks before Apollo enrichment.

        The safe default is conservative: we never invent an unverified email
        unless ALLOW_UNVERIFIED_EMAIL_PATTERNS=true is explicitly set.
        """

        if lead.email:
            return True
        if not self.settings.allow_unverified_email_patterns:
            return False
        if not lead.first_name or not lead.last_name or not lead.normalized_domain:
            return False
        public_domains = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"}
        if lead.normalized_domain in public_domains:
            return False
        first = lead.first_name.strip().lower().replace(" ", "")
        last = lead.last_name.strip().lower().replace(" ", "")
        lead.email = f"{first}.{last}@{lead.normalized_domain}"
        lead.email_source = "unverified_pattern_guess"
        lead.notes = "Email guessed from common company pattern; verify before live sending"
        return True

    def export_csv(self) -> None:
        with db.connect(self.settings.database_path) as conn:
            db.init_db(conn)
            db.backfill_normalized_keys(conn)
            db.export_to_csv(conn, self.settings.leads_csv_path)
        logger.info("Exported lead CSV to %s", self.settings.leads_csv_path)

    def status_report(self) -> dict[str, int]:
        with db.connect(self.settings.database_path) as conn:
            db.init_db(conn)
            db.backfill_normalized_keys(conn)
            counts = db.status_counts(conn)
            sent_today = db.count_sent_today(conn)
            apollo_credits_today = db.count_apollo_credits_today(conn)

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
        if self.settings.apollo_daily_credit_limit < 0:
            print(f"Apollo enrichment credits today: {apollo_credits_today}/unlimited")
        else:
            print(
                "Apollo enrichment credits today: "
                f"{apollo_credits_today}/{self.settings.apollo_daily_credit_limit}"
            )
        return counts
