"""Storage for leads and email status tracking.

SQLite is the default local backend. When DATABASE_URL is set to a Postgres
connection string, the same workflow uses the cloud Postgres database instead.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

from lead import Lead, normalize_company_name, normalize_domain, normalize_linkedin_url, utc_now_iso

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # Postgres support is optional unless DATABASE_URL is set.
    psycopg = None
    dict_row = None


LEAD_COLUMNS = [
    "id",
    "apollo_id",
    "first_name",
    "last_name",
    "full_name",
    "email",
    "email_lower",
    "title",
    "role_title",
    "contact_name",
    "contact_title",
    "company_name",
    "company_domain",
    "company_industry",
    "company_size",
    "linkedin_url",
    "city",
    "state",
    "country",
    "apollo_url",
    "reason_for_outreach",
    "source",
    "email_source",
    "email_status",
    "source_tier",
    "search_tier",
    "location_match",
    "is_dmv",
    "remote_dmv_eligible",
    "internship_type",
    "normalized_company_name",
    "normalized_domain",
    "normalized_linkedin_url",
    "lead_score",
    "score_breakdown",
    "apollo_used",
    "apollo_credits_used",
    "last_contacted_date",
    "email_sent",
    "reply_received",
    "bounced",
    "notes",
    "status",
    "error_message",
    "rejection_reason",
    "discovery_run_id",
    "queued_send_time",
    "queue_status",
    "approved_for_send",
    "manually_skipped",
    "manual_review_note",
    "send_attempts",
    "gmail_message_id",
    "created_at",
    "updated_at",
    "sent_at",
    "skipped_at",
    "raw_json",
]


BLOCKING_STATUSES = {
    "sent",
    "rejected",
    "bounced",
    "not_relevant",
    "unsubscribed",
}


POSTGRES_SCHEMES = ("postgres://", "postgresql://", "postgresql+psycopg2://", "postgresql+psycopg://")


def _database_url_from_env() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def _is_postgres_url(database_url: str) -> bool:
    return database_url.startswith(POSTGRES_SCHEMES)


def _normalize_postgres_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg2://"):
        return "postgresql://" + database_url.split("://", 1)[1]
    if database_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + database_url.split("://", 1)[1]
    return database_url


def _adapt_sql_for_postgres(sql: str) -> str:
    return sql.replace("?", "%s")


class PostgresConnection:
    is_postgres = True

    def __init__(self, database_url: str):
        if psycopg is None:
            raise RuntimeError("DATABASE_URL is set, but psycopg[binary] is not installed.")
        self.database_url = _normalize_postgres_url(database_url)
        self._conn = self._connect()

    def _connect(self):
        return psycopg.connect(
            self.database_url,
            row_factory=dict_row,
            connect_timeout=20,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )

    def _is_lost_connection(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            "connection is lost" in message
            or "connection is closed" in message
            or "server closed the connection" in message
            or "consuming input failed" in message
            or bool(getattr(self._conn, "closed", False))
        )

    def _reconnect(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
        self._conn = self._connect()

    def execute(self, sql: str, params=()):
        adapted_sql = _adapt_sql_for_postgres(sql)
        try:
            cursor = self._conn.cursor()
            cursor.execute(adapted_sql, params or ())
            return cursor
        except psycopg.OperationalError as exc:
            if not self._is_lost_connection(exc):
                raise
            self._reconnect()
            cursor = self._conn.cursor()
            cursor.execute(adapted_sql, params or ())
            return cursor

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        except psycopg.OperationalError as exc:
            if not self._is_lost_connection(exc):
                raise

    def close(self) -> None:
        try:
            self._conn.close()
        except psycopg.OperationalError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


def is_postgres_connection(conn) -> bool:
    return bool(getattr(conn, "is_postgres", False))


def connect(database_path: Path, database_url: str = ""):
    database_url = database_url or _database_url_from_env()
    if database_url:
        if not _is_postgres_url(database_url):
            raise ValueError("DATABASE_URL must start with postgres:// or postgresql://.")
        return PostgresConnection(database_url)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    id_column = "SERIAL PRIMARY KEY" if is_postgres_connection(conn) else "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS leads (
            id {id_column},
            apollo_id TEXT,
            first_name TEXT,
            last_name TEXT,
            full_name TEXT,
            email TEXT,
            email_lower TEXT,
            title TEXT,
            role_title TEXT DEFAULT '',
            contact_name TEXT DEFAULT '',
            contact_title TEXT DEFAULT '',
            company_name TEXT,
            company_domain TEXT,
            company_industry TEXT,
            company_size TEXT,
            linkedin_url TEXT,
            city TEXT,
            state TEXT,
            country TEXT,
            apollo_url TEXT,
            reason_for_outreach TEXT,
            source TEXT DEFAULT 'apollo',
            email_source TEXT DEFAULT '',
            email_status TEXT DEFAULT '',
            source_tier TEXT DEFAULT '',
            search_tier TEXT DEFAULT '',
            location_match TEXT DEFAULT '',
            is_dmv INTEGER DEFAULT 0,
            remote_dmv_eligible INTEGER DEFAULT 0,
            internship_type TEXT DEFAULT '',
            normalized_company_name TEXT DEFAULT '',
            normalized_domain TEXT DEFAULT '',
            normalized_linkedin_url TEXT DEFAULT '',
            lead_score INTEGER DEFAULT 0,
            score_breakdown TEXT DEFAULT '{{}}',
            apollo_used INTEGER DEFAULT 0,
            apollo_credits_used INTEGER DEFAULT 0,
            last_contacted_date TEXT DEFAULT '',
            email_sent INTEGER DEFAULT 0,
            reply_received INTEGER DEFAULT 0,
            bounced INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            error_message TEXT DEFAULT '',
            rejection_reason TEXT DEFAULT '',
            discovery_run_id INTEGER DEFAULT 0,
            queued_send_time TEXT DEFAULT '',
            queue_status TEXT DEFAULT 'not_queued',
            approved_for_send INTEGER DEFAULT 0,
            manually_skipped INTEGER DEFAULT 0,
            manual_review_note TEXT DEFAULT '',
            send_attempts INTEGER DEFAULT 0,
            gmail_message_id TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT,
            sent_at TEXT,
            skipped_at TEXT,
            raw_json TEXT DEFAULT '{{}}'
        )
        """
    )
    _ensure_lead_columns(conn)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_email_lower_unique
        ON leads(email_lower)
        WHERE email_lower IS NOT NULL AND email_lower != ''
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_apollo_id_unique
        ON leads(apollo_id)
        WHERE apollo_id IS NOT NULL AND apollo_id != ''
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_sent_at ON leads(sent_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_discovery_run_id ON leads(discovery_run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_queue_status ON leads(queue_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_normalized_domain ON leads(normalized_domain)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_normalized_company ON leads(normalized_company_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_normalized_linkedin ON leads(normalized_linkedin_url)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_source_tier ON leads(source_tier)")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS apollo_usage (
            id {id_column},
            used_at TEXT NOT NULL,
            operation TEXT NOT NULL,
            credits INTEGER NOT NULL DEFAULT 0,
            lead_id INTEGER,
            company_name TEXT DEFAULT '',
            contact_name TEXT DEFAULT '',
            notes TEXT DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_apollo_usage_used_at ON apollo_usage(used_at)")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS apollo_credit_events (
            id {id_column},
            event_type TEXT NOT NULL,
            lead_id INTEGER,
            automation_run_id INTEGER,
            credit_cost INTEGER NOT NULL DEFAULT 0,
            credit_delta INTEGER NOT NULL DEFAULT 0,
            description TEXT DEFAULT '',
            source TEXT DEFAULT 'workflow',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_apollo_credit_events_created_at ON apollo_credit_events(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_apollo_credit_events_event_type ON apollo_credit_events(event_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_apollo_credit_events_source ON apollo_credit_events(source)")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS email_events (
            id {id_column},
            lead_id INTEGER,
            event_type TEXT NOT NULL,
            subject TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            timestamp TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_email_events_timestamp ON email_events(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_email_events_lead_id ON email_events(lead_id)")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS automation_runs (
            id {id_column},
            run_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'running',
            raw_candidates INTEGER NOT NULL DEFAULT 0,
            enriched_count INTEGER NOT NULL DEFAULT 0,
            send_ready_count INTEGER NOT NULL DEFAULT 0,
            sent_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            error_summary TEXT DEFAULT '',
            details_json TEXT DEFAULT '{{}}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_automation_runs_started_at ON automation_runs(started_at)")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS apollo_search_logs (
            id {id_column},
            automation_run_id INTEGER,
            tier_name TEXT NOT NULL,
            search_type TEXT NOT NULL,
            page INTEGER NOT NULL DEFAULT 0,
            params_json TEXT DEFAULT '{{}}',
            result_count INTEGER NOT NULL DEFAULT 0,
            new_unique_count INTEGER NOT NULL DEFAULT 0,
            accepted_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_apollo_search_logs_run ON apollo_search_logs(automation_run_id)")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS send_queue (
            id {id_column},
            lead_id INTEGER NOT NULL,
            scheduled_send_time TEXT NOT NULL,
            queue_status TEXT NOT NULL DEFAULT 'queued',
            email_subject TEXT DEFAULT '',
            email_body TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            failure_reason TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_send_queue_lead_unique
        ON send_queue(lead_id)
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_send_queue_status ON send_queue(queue_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_send_queue_scheduled ON send_queue(scheduled_send_time)")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS umd_ta_ra_contacts (
            id {id_column},
            name TEXT DEFAULT '',
            email TEXT DEFAULT '',
            email_lower TEXT DEFAULT '',
            title TEXT DEFAULT '',
            department TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            office TEXT DEFAULT '',
            research_interests TEXT DEFAULT '',
            courses_taught TEXT DEFAULT '',
            lab_name TEXT DEFAULT '',
            profile_url TEXT DEFAULT '',
            source_url TEXT NOT NULL,
            research_or_course_area TEXT DEFAULT '',
            opportunity_type TEXT DEFAULT 'General',
            semester TEXT DEFAULT 'General',
            fit_score INTEGER DEFAULT 0,
            fit_reason TEXT DEFAULT '',
            personalization_notes TEXT DEFAULT '',
            personalization_context TEXT DEFAULT '',
            personalization_source TEXT DEFAULT 'Fallback',
            personalization_confidence TEXT DEFAULT 'Low',
            fit_bucket TEXT DEFAULT 'Low Fit',
            contact_type TEXT DEFAULT '',
            campaign_name TEXT DEFAULT '',
            status TEXT DEFAULT 'discovered',
            discovered_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_contacted_at TEXT DEFAULT '',
            email_draft_id INTEGER DEFAULT 0,
            raw_text TEXT DEFAULT '',
            raw_json TEXT DEFAULT '{{}}'
        )
        """
    )
    _ensure_umd_ta_ra_columns(conn)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_umd_contacts_email_unique
        ON umd_ta_ra_contacts(email_lower)
        WHERE email_lower IS NOT NULL AND email_lower != ''
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_contacts_status ON umd_ta_ra_contacts(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_contacts_department ON umd_ta_ra_contacts(department)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_contacts_fit_score ON umd_ta_ra_contacts(fit_score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_contacts_fit_bucket ON umd_ta_ra_contacts(fit_bucket)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_contacts_source_url ON umd_ta_ra_contacts(source_url)")
    conn.execute(
        """
        UPDATE umd_ta_ra_contacts
        SET fit_bucket = CASE
            WHEN COALESCE(fit_score, 0) >= 80 THEN 'High Fit'
            WHEN COALESCE(fit_score, 0) >= 65 THEN 'Good Fit'
            WHEN COALESCE(fit_score, 0) >= 50 THEN 'Medium Fit'
            ELSE 'Low Fit'
        END
        WHERE fit_bucket IS NULL OR fit_bucket = ''
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS umd_ta_ra_email_drafts (
            id {id_column},
            contact_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'drafted',
            approved_at TEXT DEFAULT '',
            sent_at TEXT DEFAULT '',
            gmail_message_id TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            error_message TEXT DEFAULT '',
            validation_status TEXT DEFAULT 'Passed',
            validation_issues TEXT DEFAULT '[]'
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_umd_drafts_contact_unique
        ON umd_ta_ra_email_drafts(contact_id)
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_drafts_status ON umd_ta_ra_email_drafts(status)")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS umd_ta_ra_outreach_logs (
            id {id_column},
            run_id INTEGER DEFAULT 0,
            event_type TEXT NOT NULL,
            source_url TEXT DEFAULT '',
            message TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_logs_run_id ON umd_ta_ra_outreach_logs(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_logs_created_at ON umd_ta_ra_outreach_logs(created_at)")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS umd_ta_ra_campaigns (
            id {id_column},
            campaign_name TEXT NOT NULL,
            semester_target TEXT DEFAULT 'General',
            target_contact_count INTEGER DEFAULT 75,
            selected_contacts_count INTEGER DEFAULT 0,
            approved_drafts_count INTEGER DEFAULT 0,
            sent_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            skipped_count INTEGER DEFAULT 0,
            dry_run_count INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'draft',
            min_delay_seconds INTEGER DEFAULT 90,
            max_delay_seconds INTEGER DEFAULT 240,
            daily_send_limit INTEGER DEFAULT 40,
            max_emails INTEGER DEFAULT 100,
            start_time TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT DEFAULT '',
            completed_at TEXT DEFAULT '',
            details_json TEXT DEFAULT '{{}}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_campaigns_status ON umd_ta_ra_campaigns(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_campaigns_created_at ON umd_ta_ra_campaigns(created_at)")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS umd_ta_ra_campaign_recipients (
            id {id_column},
            campaign_id INTEGER NOT NULL,
            contact_id INTEGER NOT NULL,
            draft_id INTEGER NOT NULL,
            send_status TEXT NOT NULL DEFAULT 'pending',
            scheduled_send_time TEXT DEFAULT '',
            actual_send_time TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            retry_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_umd_campaign_recipient_unique
        ON umd_ta_ra_campaign_recipients(campaign_id, contact_id)
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_campaign_recipients_campaign ON umd_ta_ra_campaign_recipients(campaign_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_campaign_recipients_status ON umd_ta_ra_campaign_recipients(send_status)")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS umd_ta_ra_contact_history (
            id {id_column},
            contact_id INTEGER NOT NULL,
            campaign_id INTEGER DEFAULT 0,
            event_type TEXT NOT NULL,
            email_status TEXT DEFAULT '',
            campaign_name TEXT DEFAULT '',
            event_at TEXT NOT NULL,
            notes TEXT DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_contact_history_contact ON umd_ta_ra_contact_history(contact_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_contact_history_event_at ON umd_ta_ra_contact_history(event_at)")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS umd_ta_ra_workflow_runs (
            id {id_column},
            run_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'running',
            pages_searched INTEGER DEFAULT 0,
            contacts_discovered INTEGER DEFAULT 0,
            high_fit_contacts INTEGER DEFAULT 0,
            emails_drafted INTEGER DEFAULT 0,
            emails_approved INTEGER DEFAULT 0,
            emails_sent INTEGER DEFAULT 0,
            duplicates_removed INTEGER DEFAULT 0,
            missing_emails INTEGER DEFAULT 0,
            error_summary TEXT DEFAULT '',
            details_json TEXT DEFAULT '{{}}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_runs_started_at ON umd_ta_ra_workflow_runs(started_at)")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS umd_ta_ra_discovery_runs (
            id {id_column},
            workflow_run_id INTEGER DEFAULT 0,
            started_at TEXT NOT NULL,
            completed_at TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'running',
            search_depth TEXT DEFAULT 'standard',
            target_contact_count INTEGER DEFAULT 75,
            max_contacts INTEGER DEFAULT 100,
            pages_searched INTEGER DEFAULT 0,
            sources_searched INTEGER DEFAULT 0,
            contacts_discovered INTEGER DEFAULT 0,
            high_fit_contacts INTEGER DEFAULT 0,
            good_fit_contacts INTEGER DEFAULT 0,
            medium_fit_contacts INTEGER DEFAULT 0,
            low_fit_contacts INTEGER DEFAULT 0,
            emails_drafted INTEGER DEFAULT 0,
            duplicates_removed INTEGER DEFAULT 0,
            missing_emails INTEGER DEFAULT 0,
            failed_pages INTEGER DEFAULT 0,
            error_summary TEXT DEFAULT '',
            details_json TEXT DEFAULT '{{}}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umd_discovery_runs_started_at ON umd_ta_ra_discovery_runs(started_at)")
    _migrate_apollo_usage_to_credit_events(conn)
    conn.commit()


def _credit_event_type_for_operation(operation: str) -> str:
    operation_lower = (operation or "").lower()
    if "enrich" in operation_lower or "match" in operation_lower:
        return "enrich"
    if "email" in operation_lower:
        return "email_lookup"
    return "search"


def _apollo_usage_credit_description(usage_id: int, operation: str, notes: str = "") -> str:
    detail = notes or operation or "Apollo credit usage"
    return f"apollo_usage:{usage_id} {detail}"[:1000]


def _migrate_apollo_usage_to_credit_events(conn: sqlite3.Connection) -> None:
    """Copy legacy apollo_usage rows into the newer credit event ledger once."""

    rows = conn.execute(
        """
        SELECT id, used_at, operation, credits, lead_id, company_name, contact_name, notes
        FROM apollo_usage
        WHERE COALESCE(credits, 0) > 0
        ORDER BY id ASC
        """
    ).fetchall()
    for row in rows:
        usage_id = int(row["id"])
        existing = conn.execute(
            """
            SELECT id
            FROM apollo_credit_events
            WHERE description LIKE ?
            LIMIT 1
            """,
            (f"apollo_usage:{usage_id} %",),
        ).fetchone()
        if existing:
            continue
        operation = row["operation"] or ""
        notes = row["notes"] or ""
        if not notes:
            company = row["company_name"] or ""
            contact = row["contact_name"] or ""
            notes = " ".join(part for part in [operation, contact, company] if part)
        conn.execute(
            """
            INSERT INTO apollo_credit_events (
                event_type, lead_id, automation_run_id, credit_cost, credit_delta,
                description, source, created_at
            )
            VALUES (?, ?, NULL, ?, 0, ?, 'system', ?)
            """,
            (
                _credit_event_type_for_operation(operation),
                row["lead_id"],
                int(row["credits"] or 0),
                _apollo_usage_credit_description(usage_id, operation, notes),
                row["used_at"],
            ),
        )


def _existing_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if is_postgres_connection(conn):
        return {
            row["name"]
            for row in conn.execute(
                """
                SELECT column_name AS name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ?
                """,
                (table_name,),
            ).fetchall()
        }
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _add_missing_columns(conn: sqlite3.Connection, table_name: str, column_defs: dict[str, str]) -> None:
    existing_columns = _existing_columns(conn, table_name)
    for column, definition in column_defs.items():
        if column not in existing_columns:
            try:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column} {definition}")
            except Exception as exc:
                message = str(exc).lower()
                if "duplicate column" not in message and "already exists" not in message:
                    raise
                if hasattr(conn, "rollback"):
                    conn.rollback()


def _ensure_umd_ta_ra_columns(conn: sqlite3.Connection) -> None:
    _add_missing_columns(
        conn,
        "umd_ta_ra_contacts",
        {
            "phone": "TEXT DEFAULT ''",
            "office": "TEXT DEFAULT ''",
            "research_interests": "TEXT DEFAULT ''",
            "courses_taught": "TEXT DEFAULT ''",
            "lab_name": "TEXT DEFAULT ''",
            "profile_url": "TEXT DEFAULT ''",
            "personalization_context": "TEXT DEFAULT ''",
            "personalization_source": "TEXT DEFAULT 'Fallback'",
            "personalization_confidence": "TEXT DEFAULT 'Low'",
            "fit_bucket": "TEXT DEFAULT 'Low Fit'",
            "contact_type": "TEXT DEFAULT ''",
            "campaign_name": "TEXT DEFAULT ''",
        },
    )
    _add_missing_columns(
        conn,
        "umd_ta_ra_email_drafts",
        {
            "validation_status": "TEXT DEFAULT 'Passed'",
            "validation_issues": "TEXT DEFAULT '[]'",
        },
    )


def _ensure_lead_columns(conn: sqlite3.Connection) -> None:
    column_defs = {
        "email_source": "TEXT DEFAULT ''",
        "email_status": "TEXT DEFAULT ''",
        "source_tier": "TEXT DEFAULT ''",
        "search_tier": "TEXT DEFAULT ''",
        "role_title": "TEXT DEFAULT ''",
        "contact_name": "TEXT DEFAULT ''",
        "contact_title": "TEXT DEFAULT ''",
        "location_match": "TEXT DEFAULT ''",
        "is_dmv": "INTEGER DEFAULT 0",
        "remote_dmv_eligible": "INTEGER DEFAULT 0",
        "internship_type": "TEXT DEFAULT ''",
        "normalized_company_name": "TEXT DEFAULT ''",
        "normalized_domain": "TEXT DEFAULT ''",
        "normalized_linkedin_url": "TEXT DEFAULT ''",
        "lead_score": "INTEGER DEFAULT 0",
        "score_breakdown": "TEXT DEFAULT '{}'",
        "apollo_used": "INTEGER DEFAULT 0",
        "apollo_credits_used": "INTEGER DEFAULT 0",
        "last_contacted_date": "TEXT DEFAULT ''",
        "email_sent": "INTEGER DEFAULT 0",
        "reply_received": "INTEGER DEFAULT 0",
        "bounced": "INTEGER DEFAULT 0",
        "notes": "TEXT DEFAULT ''",
        "rejection_reason": "TEXT DEFAULT ''",
        "discovery_run_id": "INTEGER DEFAULT 0",
        "queued_send_time": "TEXT DEFAULT ''",
        "queue_status": "TEXT DEFAULT 'not_queued'",
        "approved_for_send": "INTEGER DEFAULT 0",
        "manually_skipped": "INTEGER DEFAULT 0",
        "manual_review_note": "TEXT DEFAULT ''",
    }
    _add_missing_columns(conn, "leads", column_defs)


def _find_existing_lead(conn: sqlite3.Connection, lead: Lead) -> Optional[sqlite3.Row]:
    checks = []
    values = []
    lead.refresh_normalized_fields()

    if lead.apollo_id:
        checks.append("apollo_id = ?")
        values.append(lead.apollo_id)
    if lead.email_lower:
        checks.append("email_lower = ?")
        values.append(lead.email_lower)
    if lead.linkedin_url:
        checks.append("linkedin_url = ?")
        values.append(lead.linkedin_url)
        checks.append("LOWER(linkedin_url) = ?")
        values.append(lead.linkedin_url.lower())
    if lead.normalized_linkedin_url:
        checks.append("normalized_linkedin_url = ?")
        values.append(lead.normalized_linkedin_url)
    company_checks = []
    company_values = []
    if lead.normalized_domain:
        company_checks.append("normalized_domain = ?")
        company_values.append(lead.normalized_domain)
    if lead.normalized_company_name:
        company_checks.append("normalized_company_name = ?")
        company_values.append(lead.normalized_company_name)
    if lead.full_name and company_checks:
        checks.append(f"(LOWER(full_name) = ? AND ({' OR '.join(company_checks)}))")
        values.extend([lead.full_name.lower(), *company_values])

    if not checks:
        return None

    query = f"SELECT * FROM leads WHERE {' OR '.join(checks)} LIMIT 1"
    return conn.execute(query, values).fetchone()


def backfill_normalized_keys(conn: sqlite3.Connection) -> None:
    """Populate normalized duplicate keys for older rows."""

    rows = conn.execute(
        """
        SELECT id, company_name, company_domain, linkedin_url
        FROM leads
        WHERE normalized_company_name = ''
           OR normalized_domain = ''
           OR normalized_linkedin_url = ''
        """
    ).fetchall()
    for row in rows:
        conn.execute(
            """
            UPDATE leads
            SET normalized_company_name = COALESCE(NULLIF(normalized_company_name, ''), ?),
                normalized_domain = COALESCE(NULLIF(normalized_domain, ''), ?),
                normalized_linkedin_url = COALESCE(NULLIF(normalized_linkedin_url, ''), ?)
            WHERE id = ?
            """,
            (
                normalize_company_name(row["company_name"] or ""),
                normalize_domain(row["company_domain"] or ""),
                normalize_linkedin_url(row["linkedin_url"] or ""),
                row["id"],
            ),
        )
    conn.execute(
        """
        UPDATE leads
        SET contact_name = COALESCE(NULLIF(contact_name, ''), full_name),
            contact_title = COALESCE(NULLIF(contact_title, ''), title)
        WHERE contact_name = ''
           OR contact_title = ''
        """
    )
    conn.commit()


def upsert_lead(conn: sqlite3.Connection, lead: Lead) -> str:
    """Insert a lead or update blank fields on an existing duplicate.

    Returns "inserted" or "updated" so the workflow can log what happened.
    """

    now = utc_now_iso()
    lead.refresh_normalized_fields()
    existing = _find_existing_lead(conn, lead)

    if existing:
        current_status = existing["status"] or "pending"
        next_status = current_status
        terminal_statuses = {"sent", "bounced", "unsubscribed", "not_relevant"}
        if current_status not in terminal_statuses:
            if lead.status in {"queued", "send_ready", "enriched", "raw", "rejected", "skipped"}:
                next_status = lead.status
            if current_status == "failed" and lead.status in {"pending", "send_ready", "queued"}:
                next_status = lead.status

        conn.execute(
            """
            UPDATE leads
            SET apollo_id = COALESCE(NULLIF(?, ''), apollo_id),
                first_name = COALESCE(NULLIF(?, ''), first_name),
                last_name = COALESCE(NULLIF(?, ''), last_name),
                full_name = COALESCE(NULLIF(?, ''), full_name),
                email = COALESCE(NULLIF(?, ''), email),
                email_lower = COALESCE(NULLIF(?, ''), email_lower),
                title = COALESCE(NULLIF(?, ''), title),
                role_title = COALESCE(NULLIF(?, ''), role_title),
                contact_name = COALESCE(NULLIF(?, ''), contact_name),
                contact_title = COALESCE(NULLIF(?, ''), contact_title),
                company_name = COALESCE(NULLIF(?, ''), company_name),
                company_domain = COALESCE(NULLIF(?, ''), company_domain),
                company_industry = COALESCE(NULLIF(?, ''), company_industry),
                company_size = COALESCE(NULLIF(?, ''), company_size),
                linkedin_url = COALESCE(NULLIF(?, ''), linkedin_url),
                city = COALESCE(NULLIF(?, ''), city),
                state = COALESCE(NULLIF(?, ''), state),
                country = COALESCE(NULLIF(?, ''), country),
                apollo_url = COALESCE(NULLIF(?, ''), apollo_url),
                reason_for_outreach = COALESCE(NULLIF(?, ''), reason_for_outreach),
                source = COALESCE(NULLIF(?, ''), source),
                email_source = COALESCE(NULLIF(?, ''), email_source),
                email_status = COALESCE(NULLIF(?, ''), email_status),
                source_tier = COALESCE(NULLIF(?, ''), source_tier),
                search_tier = COALESCE(NULLIF(?, ''), search_tier),
                location_match = COALESCE(NULLIF(?, ''), location_match),
                is_dmv = CASE WHEN ? != 0 THEN 1 ELSE is_dmv END,
                remote_dmv_eligible = CASE WHEN ? != 0 THEN 1 ELSE remote_dmv_eligible END,
                internship_type = COALESCE(NULLIF(?, ''), internship_type),
                normalized_company_name = COALESCE(NULLIF(?, ''), normalized_company_name),
                normalized_domain = COALESCE(NULLIF(?, ''), normalized_domain),
                normalized_linkedin_url = COALESCE(NULLIF(?, ''), normalized_linkedin_url),
                lead_score = CASE WHEN ? > lead_score THEN ? ELSE lead_score END,
                score_breakdown = COALESCE(NULLIF(?, '{}'), score_breakdown),
                apollo_used = CASE WHEN ? != 0 THEN 1 ELSE apollo_used END,
                apollo_credits_used = apollo_credits_used + ?,
                last_contacted_date = COALESCE(NULLIF(?, ''), last_contacted_date),
                email_sent = CASE WHEN ? != 0 THEN 1 ELSE email_sent END,
                reply_received = CASE WHEN ? != 0 THEN 1 ELSE reply_received END,
                bounced = CASE WHEN ? != 0 THEN 1 ELSE bounced END,
                notes = COALESCE(NULLIF(?, ''), notes),
                status = ?,
                error_message = ?,
                rejection_reason = COALESCE(NULLIF(?, ''), rejection_reason),
                discovery_run_id = CASE WHEN ? > 0 THEN ? ELSE discovery_run_id END,
                queued_send_time = COALESCE(NULLIF(?, ''), queued_send_time),
                queue_status = COALESCE(NULLIF(?, ''), queue_status),
                approved_for_send = CASE WHEN ? != 0 THEN 1 ELSE approved_for_send END,
                manually_skipped = CASE WHEN ? != 0 THEN 1 ELSE manually_skipped END,
                manual_review_note = COALESCE(NULLIF(?, ''), manual_review_note),
                updated_at = ?,
                raw_json = COALESCE(NULLIF(?, '{}'), raw_json)
            WHERE id = ?
            """,
            (
                lead.apollo_id,
                lead.first_name,
                lead.last_name,
                lead.full_name,
                lead.email,
                lead.email_lower,
                lead.title,
                lead.role_title,
                lead.contact_name or lead.full_name,
                lead.contact_title or lead.title,
                lead.company_name,
                lead.company_domain,
                lead.company_industry,
                lead.company_size,
                lead.linkedin_url,
                lead.city,
                lead.state,
                lead.country,
                lead.apollo_url,
                lead.reason_for_outreach,
                lead.source,
                lead.email_source,
                lead.email_status,
                lead.source_tier,
                lead.search_tier or lead.source_tier,
                lead.location_match,
                int(lead.is_dmv),
                int(lead.remote_dmv_eligible),
                lead.internship_type,
                lead.normalized_company_name,
                lead.normalized_domain,
                lead.normalized_linkedin_url,
                lead.lead_score,
                lead.lead_score,
                lead.score_breakdown,
                int(lead.apollo_used),
                lead.apollo_credits_used,
                lead.last_contacted_date,
                int(lead.email_sent),
                int(lead.reply_received),
                int(lead.bounced),
                lead.notes,
                next_status,
                "" if next_status in {"pending", "send_ready"} else lead.error_message,
                lead.rejection_reason,
                int(lead.discovery_run_id or 0),
                int(lead.discovery_run_id or 0),
                lead.queued_send_time,
                lead.queue_status,
                int(lead.approved_for_send),
                int(lead.manually_skipped),
                lead.manual_review_note,
                now,
                lead.raw_json,
                existing["id"],
            ),
        )
        conn.commit()
        return "updated"

    conn.execute(
        """
        INSERT INTO leads (
            apollo_id, first_name, last_name, full_name, email, email_lower,
            title, role_title, contact_name, contact_title,
            company_name, company_domain, company_industry, company_size,
            linkedin_url, city, state, country, apollo_url, reason_for_outreach,
            source, email_source, email_status, source_tier,
            search_tier,
            location_match, is_dmv, remote_dmv_eligible,
            internship_type, normalized_company_name, normalized_domain,
            normalized_linkedin_url, lead_score, score_breakdown, apollo_used,
            apollo_credits_used, last_contacted_date, email_sent, reply_received,
            bounced, notes, status, error_message, rejection_reason,
            discovery_run_id, queued_send_time, queue_status, approved_for_send,
            manually_skipped, manual_review_note,
            send_attempts, gmail_message_id,
            created_at, updated_at, sent_at, skipped_at, raw_json
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            lead.apollo_id,
            lead.first_name,
            lead.last_name,
            lead.full_name,
            lead.email,
            lead.email_lower,
            lead.title,
            lead.role_title,
            lead.contact_name or lead.full_name,
            lead.contact_title or lead.title,
            lead.company_name,
            lead.company_domain,
            lead.company_industry,
            lead.company_size,
            lead.linkedin_url,
            lead.city,
            lead.state,
            lead.country,
            lead.apollo_url,
            lead.reason_for_outreach,
            lead.source,
            lead.email_source,
            lead.email_status,
            lead.source_tier,
            lead.search_tier or lead.source_tier,
            lead.location_match,
            int(lead.is_dmv),
            int(lead.remote_dmv_eligible),
            lead.internship_type,
            lead.normalized_company_name,
            lead.normalized_domain,
            lead.normalized_linkedin_url,
            lead.lead_score,
            lead.score_breakdown,
            int(lead.apollo_used),
            lead.apollo_credits_used,
            lead.last_contacted_date,
            int(lead.email_sent),
            int(lead.reply_received),
            int(lead.bounced),
            lead.notes,
            lead.status,
            lead.error_message,
            lead.rejection_reason,
            int(lead.discovery_run_id or 0),
            lead.queued_send_time,
            lead.queue_status,
            int(lead.approved_for_send),
            int(lead.manually_skipped),
            lead.manual_review_note,
            0,
            "",
            now,
            now,
            now if lead.status == "sent" else None,
            now if lead.status == "skipped" else None,
            lead.raw_json,
        ),
    )
    conn.commit()
    return "inserted"


def update_lead_dmv_fields(conn: sqlite3.Connection, lead_id: int, lead: Lead) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        UPDATE leads
        SET role_title = COALESCE(NULLIF(?, ''), role_title),
            contact_name = COALESCE(NULLIF(?, ''), contact_name),
            contact_title = COALESCE(NULLIF(?, ''), contact_title),
            location_match = ?,
            is_dmv = ?,
            remote_dmv_eligible = ?,
            internship_type = COALESCE(NULLIF(?, ''), internship_type),
            updated_at = ?
        WHERE id = ?
        """,
        (
            lead.role_title,
            lead.contact_name or lead.full_name,
            lead.contact_title or lead.title,
            lead.location_match,
            int(lead.is_dmv),
            int(lead.remote_dmv_eligible),
            lead.internship_type,
            now,
            lead_id,
        ),
    )
    conn.commit()


def find_existing_lead(conn: sqlite3.Connection, lead: Lead) -> Optional[sqlite3.Row]:
    return _find_existing_lead(conn, lead)


def blocking_match(conn: sqlite3.Connection, lead: Lead) -> Optional[sqlite3.Row]:
    """Return a local row that should prevent Apollo enrichment or sending."""

    lead.refresh_normalized_fields()
    checks = []
    values = []

    if lead.email_lower:
        checks.append("email_lower = ?")
        values.append(lead.email_lower)
    if lead.normalized_linkedin_url:
        checks.append("normalized_linkedin_url = ?")
        values.append(lead.normalized_linkedin_url)
    if lead.linkedin_url:
        checks.append("LOWER(linkedin_url) = ?")
        values.append(lead.linkedin_url.lower())
    company_checks = []
    company_values = []
    if lead.normalized_domain:
        company_checks.append("normalized_domain = ?")
        company_values.append(lead.normalized_domain)
    if lead.normalized_company_name:
        company_checks.append("normalized_company_name = ?")
        company_values.append(lead.normalized_company_name)
    if lead.full_name and company_checks:
        checks.append(f"(LOWER(full_name) = ? AND ({' OR '.join(company_checks)}))")
        values.extend([lead.full_name.lower(), *company_values])

    if not checks:
        return None

    placeholders = ", ".join("?" for _ in BLOCKING_STATUSES)
    query = f"""
        SELECT *
        FROM leads
        WHERE ({' OR '.join(checks)})
          AND (
            status IN ({placeholders})
            OR email_sent = 1
            OR bounced = 1
          )
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
    """
    return conn.execute(query, values + list(BLOCKING_STATUSES)).fetchone()


def get_pending_leads(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM leads
        WHERE status IN ('pending', 'send_ready', 'queued')
          AND email_lower IS NOT NULL
          AND email_lower != ''
          AND is_dmv = 1
          AND COALESCE(manually_skipped, 0) = 0
        ORDER BY lead_score DESC, created_at ASC, id ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def count_send_ready_pending(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM leads
        WHERE status IN ('pending', 'send_ready', 'queued')
          AND email_lower IS NOT NULL
          AND email_lower != ''
          AND is_dmv = 1
          AND COALESCE(manually_skipped, 0) = 0
        """
    ).fetchone()
    return int(row["count"])


def get_send_queue_candidates(conn: sqlite3.Connection, limit: int, min_score: int) -> list[sqlite3.Row]:
    """Return leads explicitly queued for the morning sender."""

    queued_rows = conn.execute(
        """
        SELECT
            l.*,
            q.id AS send_queue_id,
            q.scheduled_send_time AS queue_scheduled_send_time,
            q.email_subject AS queue_email_subject,
            q.email_body AS queue_email_body,
            q.failure_reason AS queue_failure_reason
        FROM send_queue q
        JOIN leads l ON l.id = q.lead_id
        WHERE q.queue_status = 'queued'
          AND l.queue_status = 'queued'
          AND l.status IN ('queued', 'send_ready')
          AND COALESCE(l.approved_for_send, 0) = 1
          AND COALESCE(l.manually_skipped, 0) = 0
          AND COALESCE(l.email_sent, 0) = 0
          AND COALESCE(l.lead_score, 0) >= ?
          AND l.email_lower IS NOT NULL
          AND l.email_lower != ''
        ORDER BY l.lead_score DESC, q.created_at ASC, l.id ASC
        LIMIT ?
        """,
        (min_score, limit),
    ).fetchall()

    if len(queued_rows) >= limit:
        return queued_rows

    queued_ids = {int(row["id"]) for row in queued_rows}
    remaining = limit - len(queued_rows)
    fallback_rows = conn.execute(
        """
        SELECT
            l.*,
            NULL AS send_queue_id,
            l.queued_send_time AS queue_scheduled_send_time,
            '' AS queue_email_subject,
            '' AS queue_email_body,
            '' AS queue_failure_reason
        FROM leads l
        WHERE l.status IN ('queued', 'send_ready')
          AND l.queue_status = 'queued'
          AND COALESCE(l.approved_for_send, 0) = 1
          AND COALESCE(l.manually_skipped, 0) = 0
          AND COALESCE(l.email_sent, 0) = 0
          AND COALESCE(l.lead_score, 0) >= ?
          AND l.email_lower IS NOT NULL
          AND l.email_lower != ''
        ORDER BY l.lead_score DESC, l.created_at ASC, l.id ASC
        LIMIT ?
        """,
        (min_score, remaining),
    ).fetchall()
    return queued_rows + [row for row in fallback_rows if int(row["id"]) not in queued_ids]


def mark_sent(conn: sqlite3.Connection, lead_id: int, gmail_message_id: str) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        UPDATE leads
        SET status = 'sent',
            gmail_message_id = ?,
            error_message = '',
            send_attempts = send_attempts + 1,
            last_contacted_date = ?,
            email_sent = 1,
            queue_status = 'sent',
            sent_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (gmail_message_id, now, now, now, lead_id),
    )
    conn.commit()


def mark_failed(conn: sqlite3.Connection, lead_id: int, error_message: str) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        UPDATE leads
        SET status = 'failed',
            error_message = ?,
            queue_status = 'failed',
            send_attempts = send_attempts + 1,
            updated_at = ?
        WHERE id = ?
        """,
        (error_message[:1000], now, lead_id),
    )
    conn.commit()


def mark_skipped(conn: sqlite3.Connection, lead_id: int, reason: str) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        UPDATE leads
        SET status = 'skipped',
            error_message = ?,
            rejection_reason = ?,
            queue_status = 'skipped',
            skipped_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (reason[:1000], reason[:1000], now, now, lead_id),
    )
    conn.commit()


def update_lead_quality(
    conn: sqlite3.Connection,
    lead_id: int,
    lead_score: int,
    score_breakdown: str,
    status: Optional[str] = None,
    error_message: str = "",
    notes: str = "",
) -> None:
    now = utc_now_iso()
    if status:
        conn.execute(
            """
            UPDATE leads
            SET lead_score = ?,
                score_breakdown = ?,
                status = ?,
                error_message = ?,
                rejection_reason = COALESCE(NULLIF(?, ''), rejection_reason),
                notes = COALESCE(NULLIF(?, ''), notes),
                updated_at = ?
            WHERE id = ?
            """,
            (lead_score, score_breakdown, status, error_message[:1000], error_message[:1000], notes, now, lead_id),
        )
    else:
        conn.execute(
            """
            UPDATE leads
            SET lead_score = ?,
                score_breakdown = ?,
                notes = COALESCE(NULLIF(?, ''), notes),
                updated_at = ?
            WHERE id = ?
            """,
            (lead_score, score_breakdown, notes, now, lead_id),
        )
    conn.commit()


def email_already_sent(conn: sqlite3.Connection, email_lower: str, current_id: int) -> bool:
    row = conn.execute(
        """
        SELECT id
        FROM leads
        WHERE email_lower = ?
          AND id != ?
          AND status = 'sent'
        LIMIT 1
        """,
        (email_lower, current_id),
    ).fetchone()
    return row is not None


def count_sent_today(conn: sqlite3.Connection) -> int:
    today_prefix = datetime.utcnow().date().isoformat() + "%"
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM leads
        WHERE status = 'sent'
          AND sent_at LIKE ?
        """,
        (today_prefix,),
    ).fetchone()
    return int(row["count"])


def count_apollo_credits_today(conn: sqlite3.Connection) -> int:
    today_prefix = datetime.utcnow().date().isoformat() + "%"
    row = conn.execute(
        """
        SELECT COALESCE(SUM(credit_cost), 0) AS count
        FROM apollo_credit_events
        WHERE created_at LIKE ?
        """,
        (today_prefix,),
    ).fetchone()
    return int(row["count"])


def record_apollo_usage(
    conn: sqlite3.Connection,
    operation: str,
    credits: int,
    lead: Optional[Lead] = None,
    notes: str = "",
    automation_run_id: Optional[int] = None,
) -> None:
    now = utc_now_iso()
    params = (
        now,
        operation,
        credits,
        lead.company_name if lead else "",
        lead.full_name if lead else "",
        notes[:1000],
    )
    if is_postgres_connection(conn):
        row = conn.execute(
            """
            INSERT INTO apollo_usage (
                used_at, operation, credits, lead_id, company_name, contact_name, notes
            )
            VALUES (?, ?, ?, NULL, ?, ?, ?)
            RETURNING id
            """,
            params,
        ).fetchone()
        usage_id = int(row["id"])
    else:
        conn.execute(
            """
            INSERT INTO apollo_usage (
                used_at, operation, credits, lead_id, company_name, contact_name, notes
            )
            VALUES (?, ?, ?, NULL, ?, ?, ?)
            """,
            params,
        )
        usage_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
    if credits > 0:
        conn.execute(
            """
            INSERT INTO apollo_credit_events (
                event_type, lead_id, automation_run_id, credit_cost, credit_delta,
                description, source, created_at
            )
            VALUES (?, NULL, ?, ?, 0, ?, 'workflow', ?)
            """,
            (
                _credit_event_type_for_operation(operation),
                automation_run_id,
                int(credits),
                _apollo_usage_credit_description(usage_id, operation, notes),
                now,
            ),
        )
    conn.commit()


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM leads
        GROUP BY status
        ORDER BY status
        """
    ).fetchall()
    return {row["status"]: int(row["count"]) for row in rows}


def export_to_csv(conn: sqlite3.Connection, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(f"SELECT {', '.join(LEAD_COLUMNS)} FROM leads ORDER BY id").fetchall()
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=LEAD_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row[column] for column in LEAD_COLUMNS})


def read_suppression_list(path: Path) -> set[str]:
    """Read emails or domains that should never be contacted.

    Put one item per line in data/suppression_list.txt. Examples:
    jane@example.com
    example.com
    """

    if not path.exists():
        path.touch()
        return set()
    items = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip().lower()
        if cleaned and not cleaned.startswith("#"):
            items.add(cleaned)
    return items


def read_blocklist(path: Path) -> set[str]:
    return read_suppression_list(path)


def read_csv_identity_keys(path: Path) -> set[str]:
    """Read duplicate keys from a CSV export or manually maintained sheet dump."""

    if not path.exists():
        return set()
    keys = set()
    with path.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            status = (row.get("status") or row.get("Status") or "").strip().lower()
            if status in {"raw", "rejected", "skipped"}:
                continue
            email = (row.get("email") or row.get("Email") or "").strip().lower()
            domain = normalize_domain(
                row.get("domain")
                or row.get("company_domain")
                or row.get("Company Domain")
                or row.get("website")
                or ""
            )
            company = normalize_company_name(row.get("company_name") or row.get("Company") or "")
            linkedin = normalize_linkedin_url(row.get("linkedin_url") or row.get("LinkedIn") or "")
            for value in (email, domain, company, linkedin):
                if value:
                    keys.add(value)
    return keys


def is_suppressed(email: str, suppression_items: set[str]) -> bool:
    email_lower = email.strip().lower()
    domain = email_lower.split("@")[-1] if "@" in email_lower else ""
    return email_lower in suppression_items or domain in suppression_items


def lead_matches_blocklist(lead: Lead, block_items: set[str]) -> bool:
    lead.refresh_normalized_fields()
    values = {
        lead.email_lower,
        lead.normalized_domain,
        lead.normalized_company_name,
        lead.normalized_linkedin_url,
        lead.company_name.strip().lower(),
        lead.company_domain.strip().lower(),
        lead.linkedin_url.strip().lower(),
    }
    values = {value for value in values if value}
    return bool(values & block_items)


def lead_matches_identity_keys(lead: Lead, keys: set[str]) -> bool:
    return lead_matches_blocklist(lead, keys)


def company_contact_count_this_week(conn: sqlite3.Connection, lead: Lead) -> int:
    lead.refresh_normalized_fields()
    company_checks = []
    values = []
    if lead.normalized_domain:
        company_checks.append("normalized_domain = ?")
        values.append(lead.normalized_domain)
    if lead.normalized_company_name:
        company_checks.append("normalized_company_name = ?")
        values.append(lead.normalized_company_name)
    if not company_checks:
        return 0
    cutoff = (datetime.utcnow() - timedelta(days=7)).replace(microsecond=0).isoformat() + "Z"
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM leads
        WHERE ({' OR '.join(company_checks)})
          AND status IN ('pending', 'send_ready', 'queued', 'sent')
          AND COALESCE(NULLIF(last_contacted_date, ''), sent_at, created_at) >= ?
        """,
        (*values, cutoff),
    ).fetchone()
    return int(row["count"])


def start_automation_run(conn: sqlite3.Connection, run_type: str, details: Optional[dict] = None) -> int:
    now = utc_now_iso()
    if is_postgres_connection(conn):
        row = conn.execute(
            """
            INSERT INTO automation_runs (run_type, started_at, status, details_json)
            VALUES (?, ?, 'running', ?)
            RETURNING id
            """,
            (run_type, now, json.dumps(details or {}, sort_keys=True)),
        ).fetchone()
        conn.commit()
        return int(row["id"])
    conn.execute(
        """
        INSERT INTO automation_runs (run_type, started_at, status, details_json)
        VALUES (?, ?, 'running', ?)
        """,
        (run_type, now, json.dumps(details or {}, sort_keys=True)),
    )
    conn.commit()
    return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])


def complete_automation_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    counts: dict,
    error_summary: str = "",
    details: Optional[dict] = None,
) -> None:
    now = utc_now_iso()
    skipped_count = counts.get(
        "skipped_total",
        sum(
            int(value)
            for key, value in counts.items()
            if key.startswith("skipped")
            or key.startswith("rejected")
            or key in {"credit_budget_hit", "credit_guardrail_hit"}
        ),
    )
    run_details = {"counts": counts}
    if details:
        run_details.update(details)
    conn.execute(
        """
        UPDATE automation_runs
        SET completed_at = ?,
            status = ?,
            raw_candidates = ?,
            enriched_count = ?,
            send_ready_count = ?,
            sent_count = ?,
            skipped_count = ?,
            failed_count = ?,
            error_summary = ?,
            details_json = ?
        WHERE id = ?
        """,
        (
            now,
            status,
            int(counts.get("searched", counts.get("raw_candidates", 0)) or 0),
            int(counts.get("enriched", counts.get("enriched_count", 0)) or 0),
            int(counts.get("send_ready", counts.get("pending", counts.get("send_ready_count", 0))) or 0),
            int(counts.get("sent", counts.get("sent_count", 0)) or 0),
            int(skipped_count or 0),
            int(counts.get("failed", counts.get("failed_count", 0)) or 0),
            error_summary[:2000],
            json.dumps(run_details, sort_keys=True, default=str),
            run_id,
        ),
    )
    conn.commit()


def record_email_event(
    conn: sqlite3.Connection,
    lead_id: int,
    event_type: str,
    subject: str = "",
    error_message: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO email_events (lead_id, event_type, subject, error_message, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (lead_id, event_type, subject[:500], error_message[:1000], utc_now_iso()),
    )
    conn.commit()


def queue_lead_for_send(
    conn: sqlite3.Connection,
    lead: Lead,
    scheduled_send_time: str,
    email_subject: str,
    email_body: str,
) -> Optional[int]:
    """Add or refresh a lead in the explicit 8 AM send queue."""

    existing = _find_existing_lead(conn, lead)
    if not existing:
        return None
    lead_id = int(existing["id"])
    now = utc_now_iso()
    conn.execute(
        """
        UPDATE leads
        SET status = 'queued',
            queue_status = 'queued',
            queued_send_time = ?,
            approved_for_send = 1,
            manually_skipped = 0,
            updated_at = ?
        WHERE id = ?
          AND COALESCE(email_sent, 0) = 0
          AND status NOT IN ('sent', 'bounced', 'unsubscribed', 'not_relevant')
        """,
        (scheduled_send_time, now, lead_id),
    )
    conn.execute(
        """
        INSERT INTO send_queue (
            lead_id, scheduled_send_time, queue_status, email_subject, email_body,
            created_at, updated_at, failure_reason
        )
        VALUES (?, ?, 'queued', ?, ?, ?, ?, '')
        ON CONFLICT(lead_id) DO UPDATE SET
            scheduled_send_time = excluded.scheduled_send_time,
            queue_status = 'queued',
            email_subject = excluded.email_subject,
            email_body = excluded.email_body,
            updated_at = excluded.updated_at,
            failure_reason = ''
        """,
        (lead_id, scheduled_send_time, email_subject[:500], email_body, now, now),
    )
    conn.commit()
    return lead_id


def update_send_queue_status(
    conn: sqlite3.Connection,
    lead_id: int,
    queue_status: str,
    failure_reason: str = "",
    send_queue_id: Optional[int] = None,
) -> None:
    now = utc_now_iso()
    if send_queue_id:
        conn.execute(
            """
            UPDATE send_queue
            SET queue_status = ?,
                failure_reason = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (queue_status, failure_reason[:1000], now, send_queue_id),
        )
    else:
        conn.execute(
            """
            UPDATE send_queue
            SET queue_status = ?,
                failure_reason = ?,
                updated_at = ?
            WHERE lead_id = ?
            """,
            (queue_status, failure_reason[:1000], now, lead_id),
        )
    conn.execute(
        """
        UPDATE leads
        SET queue_status = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (queue_status, now, lead_id),
    )
    conn.commit()


def record_search_logs(conn: sqlite3.Connection, run_id: int, logs: list[dict]) -> None:
    now = utc_now_iso()
    for item in logs:
        conn.execute(
            """
            INSERT INTO apollo_search_logs (
                automation_run_id, tier_name, search_type, page, params_json,
                result_count, new_unique_count, accepted_count, rejected_count, notes, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                item.get("tier", ""),
                item.get("search_type", ""),
                int(item.get("page", 0) or 0),
                json.dumps(item.get("params", {}), sort_keys=True),
                int(item.get("result_count", 0) or 0),
                int(item.get("new_unique_count", 0) or 0),
                int(item.get("accepted_count", 0) or 0),
                int(item.get("rejected_count", 0) or 0),
                item.get("description", "")[:1000],
                now,
            ),
        )
    conn.commit()
