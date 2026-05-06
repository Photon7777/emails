"""SQLite storage for leads and email status tracking."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Optional

from lead import Lead, normalize_company_name, normalize_domain, normalize_linkedin_url, utc_now_iso


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
    "email_source",
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
            email_source TEXT DEFAULT '',
            normalized_company_name TEXT DEFAULT '',
            normalized_domain TEXT DEFAULT '',
            normalized_linkedin_url TEXT DEFAULT '',
            lead_score INTEGER DEFAULT 0,
            score_breakdown TEXT DEFAULT '{}',
            apollo_used INTEGER DEFAULT 0,
            apollo_credits_used INTEGER DEFAULT 0,
            last_contacted_date TEXT DEFAULT '',
            email_sent INTEGER DEFAULT 0,
            reply_received INTEGER DEFAULT 0,
            bounced INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_normalized_domain ON leads(normalized_domain)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_normalized_company ON leads(normalized_company_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_normalized_linkedin ON leads(normalized_linkedin_url)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS apollo_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    conn.commit()


def _ensure_lead_columns(conn: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(leads)").fetchall()
    }
    column_defs = {
        "email_source": "TEXT DEFAULT ''",
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
    }
    for column, definition in column_defs.items():
        if column not in existing_columns:
            try:
                conn.execute(f"ALTER TABLE leads ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise


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
    if lead.company_domain:
        checks.append("LOWER(company_domain) = ?")
        values.append(lead.company_domain.lower())
    if lead.company_name:
        checks.append("LOWER(company_name) = ?")
        values.append(lead.company_name.lower())
    if lead.normalized_linkedin_url:
        checks.append("normalized_linkedin_url = ?")
        values.append(lead.normalized_linkedin_url)
    if lead.normalized_domain:
        checks.append("normalized_domain = ?")
        values.append(lead.normalized_domain)
    if lead.normalized_company_name:
        checks.append("normalized_company_name = ?")
        values.append(lead.normalized_company_name)

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
                email_source = COALESCE(NULLIF(?, ''), email_source),
                normalized_company_name = COALESCE(NULLIF(?, ''), normalized_company_name),
                normalized_domain = COALESCE(NULLIF(?, ''), normalized_domain),
                normalized_linkedin_url = COALESCE(NULLIF(?, ''), normalized_linkedin_url),
                lead_score = CASE WHEN ? > lead_score THEN ? ELSE lead_score END,
                score_breakdown = COALESCE(NULLIF(?, '{}'), score_breakdown),
                apollo_used = CASE WHEN ? THEN 1 ELSE apollo_used END,
                apollo_credits_used = apollo_credits_used + ?,
                last_contacted_date = COALESCE(NULLIF(?, ''), last_contacted_date),
                email_sent = CASE WHEN ? THEN 1 ELSE email_sent END,
                reply_received = CASE WHEN ? THEN 1 ELSE reply_received END,
                bounced = CASE WHEN ? THEN 1 ELSE bounced END,
                notes = COALESCE(NULLIF(?, ''), notes),
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
                lead.email_source,
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
            source, email_source, normalized_company_name, normalized_domain,
            normalized_linkedin_url, lead_score, score_breakdown, apollo_used,
            apollo_credits_used, last_contacted_date, email_sent, reply_received,
            bounced, notes, status, error_message, send_attempts, gmail_message_id,
            created_at, updated_at, sent_at, skipped_at, raw_json
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            0, '', ?, ?, NULL, ?, ?
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
            now,
            now,
            now if lead.status == "skipped" else None,
            lead.raw_json,
        ),
    )
    conn.commit()
    return "inserted"


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
    if lead.normalized_domain:
        checks.append("normalized_domain = ?")
        values.append(lead.normalized_domain)
    if lead.company_domain:
        checks.append("LOWER(company_domain) = ?")
        values.append(lead.company_domain.lower())
    if lead.normalized_company_name:
        checks.append("normalized_company_name = ?")
        values.append(lead.normalized_company_name)
    if lead.company_name:
        checks.append("LOWER(company_name) = ?")
        values.append(lead.company_name.lower())

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
        WHERE status = 'pending'
          AND email_lower IS NOT NULL
          AND email_lower != ''
        ORDER BY lead_score DESC, created_at ASC, id ASC
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
            last_contacted_date = ?,
            email_sent = 1,
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
                notes = COALESCE(NULLIF(?, ''), notes),
                updated_at = ?
            WHERE id = ?
            """,
            (lead_score, score_breakdown, status, error_message[:1000], notes, now, lead_id),
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
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM leads
        WHERE status = 'sent'
          AND date(sent_at, 'localtime') = date('now', 'localtime')
        """
    ).fetchone()
    return int(row["count"])


def count_apollo_credits_today(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(credits), 0) AS count
        FROM apollo_usage
        WHERE date(used_at, 'localtime') = date('now', 'localtime')
        """
    ).fetchone()
    return int(row["count"])


def record_apollo_usage(
    conn: sqlite3.Connection,
    operation: str,
    credits: int,
    lead: Optional[Lead] = None,
    notes: str = "",
) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO apollo_usage (
            used_at, operation, credits, lead_id, company_name, contact_name, notes
        )
        VALUES (?, ?, ?, NULL, ?, ?, ?)
        """,
        (
            now,
            operation,
            credits,
            lead.company_name if lead else "",
            lead.full_name if lead else "",
            notes[:1000],
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
