"""High-level workflow: fetch leads, save them, render emails, and send."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
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
from dmv_location import apply_dmv_location
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
        with db.connect(self.settings.database_path, self.settings.database_url) as conn:
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

        counts = {
            "searched": 0,
            "inserted": 0,
            "updated": 0,
            "send_ready": 0,
            "queued": 0,
            "enriched": 0,
            "apollo_credits_used": 0,
            "credit_budget_hit": 0,
            "skipped_missing_email": 0,
            "skipped_non_dmv": 0,
            "skipped_duplicate": 0,
            "skipped_blocklist": 0,
            "skipped_company_limit": 0,
            "rejected_low_score": 0,
            "rejected_below_send_score": 0,
        }
        with db.connect(settings.database_path, settings.database_url) as conn:
            db.init_db(conn)
            db.backfill_normalized_keys(conn)
            run_id = db.start_automation_run(
                conn,
                "discovery",
                details={
                    "dry_run": settings.dry_run,
                    "min_score_to_enrich": settings.min_score_to_enrich,
                    "min_score_to_send": settings.min_score_to_send,
                    "daily_enrich_limit": settings.apollo_daily_credit_limit,
                },
            )
            suppression_items = db.read_suppression_list(settings.suppression_list_path)
            do_not_contact_items = db.read_blocklist(settings.do_not_contact_path)
            already_contacted_items = db.read_blocklist(settings.already_contacted_path)
            csv_identity_keys = db.read_csv_identity_keys(settings.leads_csv_path)
            block_items = suppression_items | do_not_contact_items | already_contacted_items
            credits_used_today = db.count_apollo_credits_today(conn)
            scheduled_send_time = self._next_morning_send_time()

            try:
                client = ApolloClient(settings)
                raw_people = client.search_people(
                    target_count=max(
                        settings.pending_inventory_target,
                        settings.daily_send_target_min,
                        settings.daily_send_limit,
                    )
                )
                counts["searched"] = len(raw_people)
                db.record_search_logs(conn, run_id, client.search_debug)

                for person in raw_people:
                    lead = client.normalize_person(person)
                    lead.source = "apollo_search"
                    lead.discovery_run_id = run_id
                    lead.search_tier = lead.source_tier
                    lead.apollo_used = False
                    lead.apollo_credits_used = 0
                    if lead.email:
                        lead.email_source = "apollo_search"
                    lead.refresh_normalized_fields()
                    apply_dmv_location(lead)

                    if not self._lead_is_allowed_by_location(lead):
                        counts["skipped_non_dmv"] += 1
                        lead.status = "skipped"
                        lead.rejection_reason = "Outside DMV and not remote"
                        lead.error_message = lead.rejection_reason
                        action = db.upsert_lead(conn, lead)
                        counts[action] += 1
                        logger.info(
                            "Skipping non-DMV/non-remote lead before enrichment: %s at %s (%s)",
                            lead.full_name or lead.first_name or "unknown",
                            lead.company_name or "unknown company",
                            lead.location_match or lead.country or "unknown location",
                        )
                        continue
                    lead.refresh_normalized_fields()

                    if db.lead_matches_blocklist(lead, block_items):
                        counts["skipped_blocklist"] += 1
                        lead.status = "skipped"
                        lead.rejection_reason = "Lead matched do-not-contact, already-contacted, or suppression list"
                        lead.error_message = lead.rejection_reason
                        action = db.upsert_lead(conn, lead)
                        counts[action] += 1
                        logger.info("Skipping blocked lead/company before enrichment: %s at %s", lead.full_name, lead.company_name)
                        continue

                    if db.lead_matches_identity_keys(lead, csv_identity_keys):
                        counts["skipped_duplicate"] += 1
                        lead.status = "skipped"
                        lead.rejection_reason = "Duplicate contact from local CSV/export"
                        lead.error_message = lead.rejection_reason
                        action = db.upsert_lead(conn, lead)
                        counts[action] += 1
                        logger.info("Skipping CSV duplicate before enrichment: %s at %s", lead.full_name, lead.company_name)
                        continue

                    if db.company_contact_count_this_week(conn, lead) >= settings.max_contacts_per_company_per_week:
                        counts["skipped_company_limit"] += 1
                        lead.status = "skipped"
                        lead.rejection_reason = "Company weekly contact limit reached"
                        lead.error_message = lead.rejection_reason
                        lead.notes = "Skipped before enrichment to avoid over-contacting one company"
                        action = db.upsert_lead(conn, lead)
                        counts[action] += 1
                        continue

                    blocking_row = db.blocking_match(conn, lead)
                    if blocking_row:
                        counts["skipped_duplicate"] += 1
                        logger.info(
                            "Skipping duplicate/already-contacted person before enrichment: %s at %s matched row %s with status %s",
                            lead.full_name or lead.first_name or "unknown",
                            lead.company_name or "unknown company",
                            blocking_row["id"],
                            blocking_row["status"],
                        )
                        continue

                    existing_row = db.find_existing_lead(conn, lead)
                    if existing_row:
                        counts["skipped_duplicate"] += 1
                        lead.notes = f"Duplicate local person row {existing_row['id']}; not enriched to save Apollo credits"
                        lead.status = "skipped"
                        lead.rejection_reason = "Duplicate local person"
                        lead.error_message = lead.rejection_reason
                        action = db.upsert_lead(conn, lead)
                        counts[action] += 1
                        continue

                    total_score, score_parts = score_lead(lead, settings)
                    lead.lead_score = total_score
                    lead.score_breakdown = breakdown_json(score_parts)

                    if total_score < settings.min_score_to_enrich:
                        lead.status = "rejected"
                        lead.rejection_reason = (
                            f"Lead score {total_score} is below enrichment threshold "
                            f"{settings.min_score_to_enrich}"
                        )
                        lead.error_message = lead.rejection_reason
                        lead.notes = "Rejected before Apollo enrichment to save credits"
                        counts["rejected_low_score"] += 1
                        action = db.upsert_lead(conn, lead)
                        counts[action] += 1
                        continue

                    self._try_free_email_fallbacks(lead)

                    if not lead.email and settings.apollo_enrich_missing_emails:
                        if settings.apollo_daily_credit_limit >= 0 and credits_used_today >= settings.apollo_daily_credit_limit:
                            lead.status = "raw"
                            lead.rejection_reason = (
                                f"Apollo daily enrichment budget reached "
                                f"({credits_used_today}/{settings.apollo_daily_credit_limit})"
                            )
                            lead.error_message = lead.rejection_reason
                            lead.notes = "Held for a future discovery run instead of spending more Apollo credits"
                            counts["credit_budget_hit"] += 1
                        else:
                            try:
                                lead = client.enrich_lead(lead)
                            except Exception as exc:
                                logger.exception("Apollo enrichment failed for %s: %s", lead.full_name, exc)
                                lead.error_message = str(exc)
                                lead.rejection_reason = str(exc)
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
                                    notes=f"Budgeted enrichment after tiered search from {lead.source_tier}",
                                )

                            if lead.email:
                                lead.email_source = "apollo_enrichment"
                            lead.refresh_normalized_fields()
                            apply_dmv_location(lead)
                            total_score, score_parts = score_lead(lead, settings)
                            lead.lead_score = total_score
                            lead.score_breakdown = breakdown_json(score_parts)

                    if not self._lead_is_allowed_by_location(lead):
                        counts["skipped_non_dmv"] += 1
                        logger.info(
                            "Skipping non-DMV/non-remote lead after enrichment: %s at %s (%s)",
                            lead.full_name or lead.first_name or "unknown",
                            lead.company_name or "unknown company",
                            lead.location_match or lead.country or "unknown location",
                        )
                        continue
                    lead.refresh_normalized_fields()

                    if not lead.email:
                        lead.status = "rejected" if lead.apollo_used else "raw"
                        lead.rejection_reason = "No reliable email found after allowed enrichment" if lead.apollo_used else "Missing email; enrichment deferred"
                        lead.error_message = lead.rejection_reason
                        counts["skipped_missing_email"] += 1
                    elif not self._email_domain_matches_company(lead):
                        lead.status = "rejected"
                        lead.rejection_reason = "Email domain does not match company domain"
                        lead.error_message = lead.rejection_reason
                        lead.notes = "Held back before sending because contact email appears to belong to another company"
                        counts["skipped_missing_email"] += 1
                    elif lead.lead_score < settings.min_score_to_send:
                        lead.status = "enriched" if lead.apollo_used else "rejected"
                        lead.rejection_reason = (
                            f"Lead score {lead.lead_score} is below send-ready threshold "
                            f"{settings.min_score_to_send}"
                        )
                        lead.error_message = lead.rejection_reason
                        counts["rejected_below_send_score"] += 1
                    else:
                        lead.status = "send_ready"
                        lead.error_message = ""
                        lead.rejection_reason = ""
                        counts["send_ready"] += 1

                    action = db.upsert_lead(conn, lead)
                    counts[action] += 1
                    if lead.status == "send_ready":
                        subject, body = render_email(lead, settings)
                        queued_lead_id = db.queue_lead_for_send(
                            conn,
                            lead,
                            scheduled_send_time,
                            subject,
                            body,
                        )
                        if queued_lead_id:
                            counts["queued"] += 1

                db.export_to_csv(conn, settings.leads_csv_path)
                db.complete_automation_run(
                    conn,
                    run_id,
                    "success",
                    counts,
                    details={"apollo_search_debug": client.search_debug},
                )
            except Exception as exc:
                db.complete_automation_run(conn, run_id, "failed", counts, error_summary=str(exc))
                raise

        logger.info(
            "Lead fetch complete: %s searched, %s inserted, %s updated, %s send-ready, "
            "%s enriched, %s Apollo credits used, %s budget hits, %s rejected low score, "
            "%s duplicates, %s blocklist, %s company-limit, %s missing email, %s non-DMV/non-remote.",
            counts["searched"],
            counts["inserted"],
            counts["updated"],
            counts["send_ready"],
            counts["enriched"],
            counts["apollo_credits_used"],
            counts["credit_budget_hit"],
            counts["rejected_low_score"],
            counts["skipped_duplicate"],
            counts["skipped_blocklist"],
            counts["skipped_company_limit"],
            counts["skipped_missing_email"],
            counts["skipped_non_dmv"],
        )
        logger.info("Exported lead CSV to %s", settings.leads_csv_path)
        return counts

    def preview_emails(self, limit: int = 3, output_path=None) -> None:
        """Print rendered emails without sending anything and save a preview file."""

        with db.connect(self.settings.database_path, self.settings.database_url) as conn:
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
                        f"Location Match: {lead.location_match or 'unknown'}",
                        f"Role: {lead.role_title or lead.title or 'unknown'}",
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

        with db.connect(self.settings.database_path, self.settings.database_url) as conn:
            db.init_db(conn)
            db.backfill_normalized_keys(conn)
            run_id = db.start_automation_run(
                conn,
                "sender",
                details={"dry_run": dry_run, "daily_send_limit": self.settings.daily_send_limit},
            )
            sent_today = db.count_sent_today(conn)
            try:
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
                    db.complete_automation_run(conn, run_id, "success", counts)
                    return counts

                pending_rows = db.get_send_queue_candidates(
                    conn,
                    send_limit,
                    self.settings.min_score_to_send,
                )
                suppression_items = db.read_suppression_list(self.settings.suppression_list_path)
                do_not_contact_items = db.read_blocklist(self.settings.do_not_contact_path)
                already_contacted_items = db.read_blocklist(self.settings.already_contacted_path)
                block_items = suppression_items | do_not_contact_items | already_contacted_items

                if not pending_rows:
                    logger.info("No pending leads with email addresses are ready to send")
                    db.complete_automation_run(conn, run_id, "success", counts)
                    return counts
                if (
                    self.settings.daily_send_target_min > 0
                    and len(pending_rows) < self.settings.daily_send_target_min
                ):
                    logger.warning(
                        "Only %s send-ready DMV leads are available; daily target is %s. "
                        "Discovery needs more qualified leads before the sender can hit the target.",
                        len(pending_rows),
                        self.settings.daily_send_target_min,
                    )

                gmail = None if dry_run else GmailClient(self.settings)

                for index, row in enumerate(pending_rows, start=1):
                    lead = Lead.from_row(row)
                    lead_id = row["id"]
                    send_queue_id = row["send_queue_id"] if "send_queue_id" in row.keys() else None

                    if lead.queue_status != "queued":
                        reason = "Lead is not queued for the 8 AM sender"
                        db.mark_skipped(conn, lead_id, reason)
                        db.update_send_queue_status(conn, lead_id, "skipped", reason, send_queue_id)
                        db.record_email_event(conn, lead_id, "skipped", error_message=reason)
                        counts["skipped"] += 1
                        continue

                    if lead.manually_skipped:
                        reason = "Lead was manually skipped in Daily Discovery Review"
                        db.mark_skipped(conn, lead_id, reason)
                        db.update_send_queue_status(conn, lead_id, "skipped", reason, send_queue_id)
                        db.record_email_event(conn, lead_id, "skipped", error_message=reason)
                        counts["skipped"] += 1
                        continue

                    if db.is_suppressed(lead.email, suppression_items):
                        reason = "Email or domain is in the suppression list"
                        db.mark_skipped(conn, lead_id, reason)
                        db.update_send_queue_status(conn, lead_id, "skipped", reason, send_queue_id)
                        db.record_email_event(conn, lead_id, "skipped", error_message=reason)
                        counts["skipped"] += 1
                        continue

                    if db.lead_matches_blocklist(lead, block_items):
                        reason = "Lead matched do-not-contact or already-contacted list"
                        db.mark_skipped(conn, lead_id, reason)
                        db.update_send_queue_status(conn, lead_id, "skipped", reason, send_queue_id)
                        db.record_email_event(conn, lead_id, "skipped", error_message=reason)
                        counts["skipped"] += 1
                        continue

                    if lead.email_source == "unverified_pattern_guess" and not self.settings.allow_unverified_email_patterns:
                        reason = "Unverified email pattern guesses are disabled"
                        db.mark_skipped(conn, lead_id, reason)
                        db.update_send_queue_status(conn, lead_id, "skipped", reason, send_queue_id)
                        db.record_email_event(conn, lead_id, "skipped", error_message=reason)
                        counts["skipped"] += 1
                        continue

                    if db.email_already_sent(conn, lead.email_lower, lead_id):
                        reason = "Duplicate email already sent previously"
                        db.mark_skipped(conn, lead_id, reason)
                        db.update_send_queue_status(conn, lead_id, "skipped", reason, send_queue_id)
                        db.record_email_event(conn, lead_id, "skipped", error_message=reason)
                        counts["skipped"] += 1
                        continue

                    if db.company_contact_count_this_week(conn, lead) > self.settings.max_contacts_per_company_per_week:
                        reason = "Company weekly contact limit reached"
                        db.mark_skipped(conn, lead_id, reason)
                        db.update_send_queue_status(conn, lead_id, "skipped", reason, send_queue_id)
                        db.record_email_event(conn, lead_id, "skipped", error_message=reason)
                        counts["skipped"] += 1
                        continue

                    if not self._lead_is_allowed_by_location(lead):
                        reason = "Lead is outside the DMV/remote target area"
                        db.mark_skipped(conn, lead_id, reason)
                        db.update_send_queue_status(conn, lead_id, "skipped", reason, send_queue_id)
                        db.record_email_event(conn, lead_id, "skipped", error_message=reason)
                        counts["skipped"] += 1
                        continue

                    if lead.lead_score and lead.lead_score < self.settings.min_score_to_send:
                        reason = "Lead score fell below the send-ready threshold"
                        db.mark_skipped(conn, lead_id, reason)
                        db.update_send_queue_status(conn, lead_id, "skipped", reason, send_queue_id)
                        db.record_email_event(conn, lead_id, "skipped", error_message=reason)
                        counts["skipped"] += 1
                        continue

                    if not self._email_domain_matches_company(lead):
                        reason = "Email domain does not match company domain"
                        db.mark_skipped(conn, lead_id, reason)
                        db.update_send_queue_status(conn, lead_id, "skipped", reason, send_queue_id)
                        db.record_email_event(conn, lead_id, "skipped", error_message=reason)
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
                        db.record_email_event(conn, lead_id, "drafted", subject=subject)
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
                        db.update_send_queue_status(conn, lead_id, "sent", send_queue_id=send_queue_id)
                        db.record_email_event(conn, lead_id, "sent", subject=subject)
                        counts["sent"] += 1
                    except Exception as exc:
                        logger.exception("Failed to send email to %s: %s", lead.email, exc)
                        db.mark_failed(conn, lead_id, str(exc))
                        db.update_send_queue_status(conn, lead_id, "failed", str(exc), send_queue_id)
                        db.record_email_event(conn, lead_id, "failed", subject=subject, error_message=str(exc))
                        counts["failed"] += 1

                    if index < len(pending_rows) and self.settings.delay_between_emails_seconds > 0:
                        time.sleep(self.settings.delay_between_emails_seconds)

                db.complete_automation_run(conn, run_id, "success", counts)
            except Exception as exc:
                db.complete_automation_run(conn, run_id, "failed", counts, error_summary=str(exc))
                raise

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

        counts = {"rescored": 0, "rejected": 0, "blocked": 0, "non_dmv": 0, "duplicate_pending": 0}
        with db.connect(self.settings.database_path, self.settings.database_url) as conn:
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
                WHERE status IN ('pending', 'send_ready', 'raw', 'enriched', 'needs_email', 'needs_enrichment')
                ORDER BY lead_score DESC, created_at ASC, id ASC
                """
            ).fetchall()

            pending_candidates = []
            for row in rows:
                lead = Lead.from_row(row)
                lead.refresh_normalized_fields()
                apply_dmv_location(lead)
                db.update_lead_dmv_fields(conn, row["id"], lead)
                score, parts = score_lead(lead, self.settings)
                score_json = breakdown_json(parts)
                counts["rescored"] += 1

                if not self._lead_is_allowed_by_location(lead):
                    db.update_lead_quality(
                        conn,
                        row["id"],
                        score,
                        score_json,
                        status="skipped",
                        error_message="Outside DMV/remote internship target area during rescore",
                        notes="Removed from pending queue before send",
                    )
                    counts["non_dmv"] += 1
                    continue

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

                if score < self.settings.min_score_to_send:
                    db.update_lead_quality(
                        conn,
                        row["id"],
                        score,
                        score_json,
                        status="rejected",
                        error_message=(
                            f"Lead score {score} is below threshold "
                            f"{self.settings.min_score_to_send}"
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

                db.update_lead_quality(conn, row["id"], score, score_json, status="send_ready" if lead.email else "raw")
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
            "Rescore complete: %s rescored, %s rejected, %s blocked, %s non-DMV skipped, %s duplicate pending skipped",
            counts["rescored"],
            counts["rejected"],
            counts["blocked"],
            counts["non_dmv"],
            counts["duplicate_pending"],
        )
        return counts

    def _attachment_paths(self) -> list:
        if not self.settings.attach_resume:
            return []
        return [self.settings.resume_file]

    def _next_morning_send_time(self) -> str:
        now = datetime.now()
        next_send = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= next_send:
            next_send += timedelta(days=1)
        return next_send.isoformat(timespec="seconds")

    def _lead_is_allowed_by_location(self, lead: Lead) -> bool:
        apply_dmv_location(lead)
        return bool(lead.is_dmv)

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
        with db.connect(self.settings.database_path, self.settings.database_url) as conn:
            db.init_db(conn)
            db.backfill_normalized_keys(conn)
            db.export_to_csv(conn, self.settings.leads_csv_path)
        logger.info("Exported lead CSV to %s", self.settings.leads_csv_path)

    def status_report(self) -> dict[str, int]:
        with db.connect(self.settings.database_path, self.settings.database_url) as conn:
            db.init_db(conn)
            db.backfill_normalized_keys(conn)
            counts = db.status_counts(conn)
            sent_today = db.count_sent_today(conn)
            apollo_credits_today = db.count_apollo_credits_today(conn)
            send_ready_pending = db.count_send_ready_pending(conn)

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
        print(f"Send-ready DMV pending leads: {send_ready_pending}")
        if self.settings.daily_send_target_min > 0:
            print(f"Daily send target: {self.settings.daily_send_target_min}-{self.settings.daily_send_limit}")
        if self.settings.pending_inventory_target > 0:
            print(f"Preferred pending inventory: {self.settings.pending_inventory_target}")
        return counts
