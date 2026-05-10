"""Apollo credit accounting helpers.

Apollo does not expose a simple plan-balance endpoint in this project, so the
dashboard tracks credits from workflow events plus manual top-ups/adjustments.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import calendar
from typing import Optional

from lead import Lead, utc_now_iso


USAGE_EVENT_TYPES = {"search", "enrich", "email_lookup"}


@dataclass(frozen=True)
class CreditSummary:
    period_start: str
    period_end: str
    base_monthly_credits: int
    top_ups: int
    adjustments: int
    monthly_available: int
    monthly_used: int
    daily_used: int
    monthly_remaining: int
    estimated_credits_saved: int
    average_daily_usage: float
    projected_monthly_usage: float
    projected_remaining_at_month_end: float
    health: str
    days_elapsed: int
    total_days: int

    def to_dict(self) -> dict:
        return asdict(self)


def credit_period_bounds(selected_date: date, reset_day: int = 1) -> tuple[date, date]:
    """Return the credit period containing selected_date.

    The default reset day is the first of each calendar month. If a different
    reset day is configured, periods run from that day to the same day next month.
    """

    reset_day = max(1, min(int(reset_day or 1), 28))
    if selected_date.day >= reset_day:
        start = selected_date.replace(day=reset_day)
    else:
        year = selected_date.year
        month = selected_date.month - 1
        if month == 0:
            month = 12
            year -= 1
        start = date(year, month, reset_day)

    year = start.year
    month = start.month + 1
    if month == 13:
        month = 1
        year += 1
    end = date(year, month, reset_day)
    return start, end


def _sum_int(conn, sql: str, params: tuple) -> int:
    row = conn.execute(sql, params).fetchone()
    if not row:
        return 0
    value = row["value"] if "value" in row.keys() else list(row)[0]
    return int(value or 0)


def _event_type_for_operation(operation: str) -> str:
    operation_lower = (operation or "").lower()
    if "enrich" in operation_lower or "match" in operation_lower:
        return "enrich"
    if "email" in operation_lower:
        return "email_lookup"
    return "search"


def calculate_credit_summary(conn, settings, selected_date: Optional[date] = None) -> CreditSummary:
    selected_date = selected_date or date.today()
    period_start, period_end = credit_period_bounds(selected_date, settings.apollo_credit_reset_day)
    period_start_iso = period_start.isoformat()
    period_end_iso = period_end.isoformat()
    today = datetime.utcnow().date()
    today_iso = today.isoformat()
    tomorrow_iso = (today + timedelta(days=1)).isoformat()

    monthly_used = _sum_int(
        conn,
        """
        SELECT COALESCE(SUM(credit_cost), 0) AS value
        FROM apollo_credit_events
        WHERE created_at >= ?
          AND created_at < ?
        """,
        (period_start_iso, period_end_iso),
    )
    top_ups = _sum_int(
        conn,
        """
        SELECT COALESCE(SUM(credit_delta), 0) AS value
        FROM apollo_credit_events
        WHERE event_type = 'top_up'
          AND created_at >= ?
          AND created_at < ?
        """,
        (period_start_iso, period_end_iso),
    )
    adjustments = _sum_int(
        conn,
        """
        SELECT COALESCE(SUM(credit_delta), 0) AS value
        FROM apollo_credit_events
        WHERE event_type = 'adjustment'
          AND created_at >= ?
          AND created_at < ?
        """,
        (period_start_iso, period_end_iso),
    )
    daily_used = 0
    if period_start <= today < period_end:
        daily_used = _sum_int(
            conn,
            """
            SELECT COALESCE(SUM(credit_cost), 0) AS value
            FROM apollo_credit_events
            WHERE created_at >= ?
              AND created_at < ?
            """,
            (today_iso, tomorrow_iso),
        )

    rejected_before_enrichment = _sum_int(
        conn,
        """
        SELECT COUNT(*) AS value
        FROM leads
        WHERE created_at >= ?
          AND created_at < ?
          AND COALESCE(apollo_used, 0) = 0
          AND status IN ('rejected', 'skipped')
        """,
        (period_start_iso, period_end_iso),
    )
    estimated_saved = rejected_before_enrichment * settings.estimated_credit_cost_per_enrichment

    monthly_available = settings.base_monthly_apollo_credits + top_ups + adjustments
    monthly_remaining = monthly_available - monthly_used
    total_days = max((period_end - period_start).days, 1)
    if today < period_start:
        days_elapsed = 0
    elif today >= period_end:
        days_elapsed = total_days
    else:
        days_elapsed = (today - period_start).days + 1
    average_daily = monthly_used / days_elapsed if days_elapsed else 0.0
    projected_monthly = average_daily * total_days if days_elapsed else 0.0
    projected_remaining = monthly_available - projected_monthly

    if monthly_remaining <= settings.min_apollo_credits_reserve:
        health = "critical"
    elif monthly_available > 0 and monthly_remaining <= monthly_available * 0.25:
        health = "warning"
    else:
        health = "healthy"

    return CreditSummary(
        period_start=period_start_iso,
        period_end=period_end_iso,
        base_monthly_credits=settings.base_monthly_apollo_credits,
        top_ups=top_ups,
        adjustments=adjustments,
        monthly_available=monthly_available,
        monthly_used=monthly_used,
        daily_used=daily_used,
        monthly_remaining=monthly_remaining,
        estimated_credits_saved=estimated_saved,
        average_daily_usage=average_daily,
        projected_monthly_usage=projected_monthly,
        projected_remaining_at_month_end=projected_remaining,
        health=health,
        days_elapsed=days_elapsed,
        total_days=total_days,
    )


def record_credit_event(
    conn,
    event_type: str,
    credit_cost: int = 0,
    credit_delta: int = 0,
    lead: Optional[Lead] = None,
    lead_id: Optional[int] = None,
    automation_run_id: Optional[int] = None,
    description: str = "",
    source: str = "workflow",
    created_at: str = "",
) -> None:
    if event_type not in USAGE_EVENT_TYPES | {"top_up", "adjustment"}:
        raise ValueError(f"Unsupported Apollo credit event type: {event_type}")
    now = created_at or utc_now_iso()
    conn.execute(
        """
        INSERT INTO apollo_credit_events (
            event_type, lead_id, automation_run_id, credit_cost, credit_delta,
            description, source, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_type,
            lead_id,
            automation_run_id,
            int(credit_cost or 0),
            int(credit_delta or 0),
            description[:1000],
            source[:50],
            now,
        ),
    )
    conn.commit()


def credit_guardrail_decision(conn, settings, lead_score: int) -> tuple[bool, str]:
    """Return whether a lead may be enriched under current credit guardrails."""

    if not settings.enable_credit_guardrails:
        return True, ""
    summary = calculate_credit_summary(conn, settings)
    remaining = summary.monthly_remaining
    if remaining <= settings.min_apollo_credits_reserve:
        return False, "Apollo credit reserve reached; enrichment paused"
    if remaining <= 500 and lead_score < 80:
        return False, "Apollo credits are low; enrichment limited to leads with score >= 80"
    return True, ""


def event_created_at_for_date(event_date: date) -> str:
    """Create a stable timestamp for a manually entered event date."""

    return datetime.combine(event_date, datetime.min.time()).replace(microsecond=0).isoformat() + "Z"
