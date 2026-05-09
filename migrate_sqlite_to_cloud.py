"""Copy the local SQLite workflow database into DATABASE_URL.

Use this once after creating a free Postgres database on Neon, Supabase, or
another Postgres host. The migration preserves IDs so automation run links,
search logs, email events, and send queue rows stay connected.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from config import load_settings
import db


TABLES = [
    "leads",
    "apollo_usage",
    "email_events",
    "automation_runs",
    "apollo_search_logs",
    "send_queue",
]


def source_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def source_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def copy_table(source: sqlite3.Connection, target, table: str) -> int:
    if not source_table_exists(source, table):
        return 0
    columns = source_columns(source, table)
    if not columns:
        return 0

    rows = source.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY id").fetchall()
    if not rows:
        return 0

    placeholders = ", ".join("?" for _ in columns)
    update_columns = [column for column in columns if column != "id"]
    assignments = ", ".join(f"{column} = excluded.{column}" for column in update_columns)
    insert_sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {assignments}"
    )
    for row in rows:
        target.execute(insert_sql, tuple(row[column] for column in columns))
    target.commit()

    if db.is_postgres_connection(target):
        max_id = int(target.execute(f"SELECT COALESCE(MAX(id), 0) AS max_id FROM {table}").fetchone()["max_id"])
        if max_id > 0:
            target.execute(
                "SELECT setval(pg_get_serial_sequence(?, ?), ?, true)",
                (table, "id", max_id),
            )
            target.commit()
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate local SQLite data to cloud Postgres DATABASE_URL.")
    parser.add_argument(
        "--source",
        default="",
        help="Path to source SQLite database. Defaults to DATABASE_PATH from .env.",
    )
    args = parser.parse_args()

    settings = load_settings()
    if not settings.database_url:
        raise SystemExit("Set DATABASE_URL in .env before running this migration.")

    source_path = Path(args.source).expanduser() if args.source else settings.database_path
    if not source_path.exists():
        raise SystemExit(f"Source SQLite database does not exist: {source_path}")

    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    try:
        with db.connect(settings.database_path, settings.database_url) as target:
            db.init_db(target)
            total = 0
            for table in TABLES:
                copied = copy_table(source, target, table)
                total += copied
                print(f"{table}: copied {copied} rows")
            print(f"Migration complete. Total rows copied: {total}")
    finally:
        source.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
