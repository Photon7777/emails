"""SQLite storage for leads and email status tracking."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Optional

from lead import Lead, utc_now_iso


LEAD_COLUMNS = [
    "id",
    "apollo_id",
    "first_name",
    "last_name",
    "full_name",
    "email",
    "email_lower",
    "title",
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
    "status",
    "error_message",
    "send_attempts",
    "gmail_message_id",
    "created_at",
    "updated_at",
    "sent_at",
    "skipped_at",
    "raw_json",
]


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            apollo_id TEXT,
            first_name TEXT,
            last_name TEXT,
            full_name TEXT,
            email TEXT,
            email_lower TEXT,
            title TEXT,
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
            status TEXT DEFAULT 'pending',
            error_message TEXT DEFAULT '',
            send_attempts INTEGER DEFAULT 0,
            gmail_message_id TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT,
            sent_at TEXT,
            skipped_at TEXT,
            raw_json TEXT DEFAULT '{}'
        )
        """
    )
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
    conn.commit()


def _find_existing_lead(conn: sqlite3.Connection, lead: Lead) -> Optional[sqlite3.Row]:
    checks = []
    values = []

    if lead.apollo_id:
        checks.append("apollo_id = ?")
        values.append(lead.apollo_id)
    if lead.email_lower:
        checks.append("email_lower = ?")
        values.append(lead.email_lower)
    if lead.linkedin_url:
        checks.append("linkedin_url = ?")
        values.append(lead.linkedin_url)

    if not checks:
        return None

    query = f"SELECT * FROM leads WHERE {' OR '.join(checks)} LIMIT 1"
    return conn.execute(query, values).fetchone()


def upsert_lead(conn: sqlite3.Connection, lead: Lead) -> str:
    """Insert a lead or update blank fields on an existing duplicate.

    Returns "inserted" or "updated" so the workflow can log what happened.
    """

    now = utc_now_iso()
    existing = _find_existing_lead(conn, lead)

    if existing:
        current_status = existing["status"] or "pending"
        next_status = current_status
        if current_status == "skipped" and lead.email:
            next_status = "pending"
        if current_status == "failed":
            next_status = "pending"

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
                status = ?,
                error_message = ?,
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
                next_status,
                "" if next_status == "pending" else lead.error_message,
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
            title, company_name, company_domain, company_industry, company_size,
            linkedin_url, city, state, country, apollo_url, reason_for_outreach,
            source, status, error_message, send_attempts, gmail_message_id,
            created_at, updated_at, sent_at, skipped_at, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', ?, ?, NULL, ?, ?)
        """,
        (
            lead.apollo_id,
            lead.first_name,
            lead.last_name,
            lead.full_name,
            lead.email,
            lead.email_lower,
            lead.title,
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
            lead.status,
            lead.error_message,
            now,
            now,
            now if lead.status == "skipped" else None,
            lead.raw_json,
        ),
    )
    conn.commit()
    return "inserted"


def get_pending_leads(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM leads
        WHERE status = 'pending'
          AND email_lower IS NOT NULL
          AND email_lower != ''
        ORDER BY created_at ASC, id ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def mark_sent(conn: sqlite3.Connection, lead_id: int, gmail_message_id: str) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        UPDATE leads
        SET status = 'sent',
            gmail_message_id = ?,
            error_message = '',
            send_attempts = send_attempts + 1,
            sent_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (gmail_message_id, now, now, lead_id),
    )
    conn.commit()


def mark_failed(conn: sqlite3.Connection, lead_id: int, error_message: str) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        UPDATE leads
        SET status = 'failed',
            error_message = ?,
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
            skipped_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (reason[:1000], now, now, lead_id),
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
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM leads
        WHERE status = 'sent'
          AND date(sent_at, 'localtime') = date('now', 'localtime')
        """
    ).fetchone()
    return int(row["count"])


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


def is_suppressed(email: str, suppression_items: set[str]) -> bool:
    email_lower = email.strip().lower()
    domain = email_lower.split("@")[-1] if "@" in email_lower else ""
    return email_lower in suppression_items or domain in suppression_items
