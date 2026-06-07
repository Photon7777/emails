"""Streamlit dashboard for the cold email workflow."""

from __future__ import annotations

import json
import os
import html
import calendar
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

import credits_service
from config import load_settings
import db
from email_template import render_email
from lead import Lead, utc_now_iso
import umd_ta_ra_workflow


def apply_streamlit_secrets_to_env() -> None:
    """Let Streamlit Cloud secrets behave like local .env variables."""

    try:
        secrets = st.secrets
    except Exception:
        return
    try:
        secret_items = secrets.items()
    except Exception:
        return
    for key, value in secret_items:
        if isinstance(value, (str, int, float, bool)):
            os.environ.setdefault(str(key), str(value))


apply_streamlit_secrets_to_env()
settings = load_settings()


@st.cache_resource(show_spinner=False)
def ensure_dashboard_db_ready() -> bool:
    with db.connect(settings.database_path, settings.database_url) as conn:
        try:
            conn.execute("SELECT 1 FROM leads LIMIT 1").fetchone()
            conn.execute("SELECT 1 FROM umd_ta_ra_contacts LIMIT 1").fetchone()
        except Exception as exc:
            if hasattr(conn, "rollback"):
                conn.rollback()
            message = str(exc).lower()
            if "does not exist" not in message and "no such table" not in message:
                raise
            db.init_db(conn)
    return True


ensure_dashboard_db_ready()


@st.cache_data(ttl=20)
def read_sql(query: str, params: tuple = ()) -> pd.DataFrame:
    with db.connect(settings.database_path, settings.database_url) as conn:
        rows = conn.execute(query, params).fetchall()
        return pd.DataFrame([dict(row) for row in rows])


def metric_card(label: str, value, help_text: str = "") -> None:
    display_value = f"{value:,}" if isinstance(value, int) else str(value)
    title = f' title="{html.escape(help_text)}"' if help_text else ""
    value_class = " metric-card-value-long" if len(display_value) > 7 else ""
    st.markdown(
        f"""
        <div class="metric-card"{title}>
            <div class="metric-card-label">{html.escape(label)}</div>
            <div class="metric-card-value{value_class}">{html.escape(display_value)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def workflow_callout(title: str, body: str, tone: str = "info") -> None:
    tones = {
        "info": ("#1d4ed8", "#eff6ff", "#bfdbfe"),
        "success": ("#047857", "#ecfdf5", "#a7f3d0"),
        "warning": ("#b45309", "#fffbeb", "#fde68a"),
        "danger": ("#b91c1c", "#fef2f2", "#fecaca"),
    }
    color, background, border = tones.get(tone, tones["info"])
    st.markdown(
        f"""
        <div class="workflow-callout" style="border-color:{border}; background:{background};">
            <div class="workflow-callout-title" style="color:{color};">{html.escape(title)}</div>
            <div class="workflow-callout-body">{html.escape(body)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def resume_attachment_status_text() -> str:
    """Human-friendly attachment status for local and hosted dashboards."""

    if settings.attach_resume and settings.resume_file.exists():
        return f"enabled ({settings.resume_file.name})"
    if settings.attach_resume:
        filename = settings.resume_file.name or "resume PDF"
        return f"configured for Mac sender ({filename}); hosted dashboard cannot verify local file"
    return "enabled in local Mac automation; hosted dashboard cannot inspect local resume"


def load_leads() -> pd.DataFrame:
    return read_sql(
        """
        SELECT
            id,
            full_name,
            title,
            company_name AS company,
            company_domain,
            TRIM(COALESCE(city, '') || ' ' || COALESCE(state, '') || ' ' || COALESCE(country, '')) AS location,
            linkedin_url,
            email,
            email_status,
            source,
            source_tier,
            search_tier,
            apollo_id AS apollo_person_id,
            lead_score AS score,
            score_breakdown,
            apollo_used,
            apollo_credits_used,
            status,
            COALESCE(NULLIF(rejection_reason, ''), error_message) AS rejection_reason,
            discovery_run_id,
            queued_send_time,
            queue_status,
            approved_for_send,
            manually_skipped,
            manual_review_note,
            updated_at,
            created_at
        FROM leads
        ORDER BY updated_at DESC, id DESC
        """
    )


def load_runs() -> pd.DataFrame:
    return read_sql("SELECT * FROM automation_runs ORDER BY started_at DESC LIMIT 200")


def load_events() -> pd.DataFrame:
    return read_sql(
        """
        SELECT e.*, l.full_name, l.company_name, l.email
        FROM email_events e
        LEFT JOIN leads l ON l.id = e.lead_id
        ORDER BY e.timestamp DESC
        LIMIT 500
        """
    )


def load_search_logs() -> pd.DataFrame:
    return read_sql(
        """
        SELECT s.*, r.started_at
        FROM apollo_search_logs s
        LEFT JOIN automation_runs r ON r.id = s.automation_run_id
        ORDER BY s.created_at DESC, s.id DESC
        LIMIT 500
        """
    )


@st.cache_data(ttl=20)
def load_credit_summary(month_start_iso: str) -> dict:
    selected_month = date.fromisoformat(month_start_iso)
    with db.connect(settings.database_path, settings.database_url) as conn:
        return credits_service.calculate_credit_summary(conn, settings, selected_month).to_dict()


@st.cache_data(ttl=20)
def load_credit_events(month_start_iso: str) -> pd.DataFrame:
    selected_month = date.fromisoformat(month_start_iso)
    period_start, period_end = credits_service.credit_period_bounds(
        selected_month,
        settings.apollo_credit_reset_day,
    )
    return read_sql(
        """
        SELECT
            e.id,
            e.created_at,
            e.event_type,
            e.lead_id,
            COALESCE(l.full_name, '') AS lead_name,
            COALESCE(l.company_name, '') AS company_name,
            e.automation_run_id,
            e.credit_cost,
            e.credit_delta,
            e.description,
            e.source
        FROM apollo_credit_events e
        LEFT JOIN leads l ON l.id = e.lead_id
        WHERE e.created_at >= ?
          AND e.created_at < ?
        ORDER BY e.created_at DESC, e.id DESC
        """,
        (period_start.isoformat(), period_end.isoformat()),
    )


def load_latest_discovery_run(selected_date: date) -> pd.DataFrame:
    return read_sql(
        """
        SELECT *
        FROM automation_runs
        WHERE run_type = 'discovery'
          AND substr(started_at, 1, 10) = ?
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """,
        (selected_date.isoformat(),),
    )


def load_daily_review_leads(selected_date: date, run_id: int | None) -> pd.DataFrame:
    if run_id:
        where_clause = "l.discovery_run_id = ?"
        params = (run_id,)
    else:
        where_clause = "substr(l.created_at, 1, 10) = ?"
        params = (selected_date.isoformat(),)
    return read_sql(
        f"""
        SELECT
            l.id,
            l.full_name,
            l.title,
            l.company_name AS company,
            l.company_domain,
            TRIM(COALESCE(l.city, '') || ' ' || COALESCE(l.state, '') || ' ' || COALESCE(l.country, '')) AS location,
            l.linkedin_url,
            l.email,
            l.email_status,
            l.source,
            COALESCE(NULLIF(l.search_tier, ''), l.source_tier) AS search_tier,
            l.lead_score AS score,
            l.score_breakdown,
            l.apollo_used,
            l.apollo_credits_used,
            l.status,
            COALESCE(NULLIF(l.rejection_reason, ''), l.error_message) AS rejection_reason,
            l.discovery_run_id,
            l.queued_send_time,
            l.queue_status,
            l.approved_for_send,
            l.manually_skipped,
            l.manual_review_note,
            l.created_at,
            l.updated_at,
            q.id AS send_queue_id,
            q.scheduled_send_time,
            q.queue_status AS send_queue_status,
            q.email_subject,
            q.email_body,
            q.failure_reason
        FROM leads l
        LEFT JOIN send_queue q ON q.lead_id = l.id
        WHERE {where_clause}
        ORDER BY l.lead_score DESC, l.created_at DESC, l.id DESC
        """,
        params,
    )


def load_discovery_search_logs(run_id: int | None) -> pd.DataFrame:
    if not run_id:
        return pd.DataFrame()
    return read_sql(
        """
        SELECT *
        FROM apollo_search_logs
        WHERE automation_run_id = ?
        ORDER BY tier_name, search_type, page
        """,
        (run_id,),
    )


@st.cache_data(ttl=20)
def load_umd_ta_ra_contacts() -> pd.DataFrame:
    return read_sql(
        """
        SELECT
            c.id,
            c.name,
            c.email,
            c.title,
            c.department,
            c.phone,
            c.office,
            c.research_interests,
            c.courses_taught,
            c.lab_name,
            c.profile_url,
            c.source_url,
            c.research_or_course_area,
            c.opportunity_type,
            c.semester,
            c.fit_score,
            c.fit_reason,
            c.personalization_notes,
            c.personalization_context,
            c.personalization_source,
            c.personalization_confidence,
            c.fit_bucket,
            c.contact_type,
            c.campaign_name,
            c.status,
            c.discovered_at,
            c.updated_at,
            c.last_contacted_at,
            c.email_draft_id,
            d.subject,
            d.body,
            d.status AS draft_status,
            d.approved_at,
            d.sent_at,
            d.error_message AS draft_error,
            COALESCE(d.validation_status, 'Passed') AS validation_status,
            COALESCE(d.validation_issues, '[]') AS validation_issues
        FROM umd_ta_ra_contacts c
        LEFT JOIN umd_ta_ra_email_drafts d ON d.id = c.email_draft_id
        ORDER BY c.fit_score DESC, c.updated_at DESC, c.id DESC
        """
    )


@st.cache_data(ttl=20)
def load_umd_ta_ra_runs() -> pd.DataFrame:
    return read_sql(
        """
        SELECT *
        FROM umd_ta_ra_workflow_runs
        ORDER BY started_at DESC, id DESC
        LIMIT 100
        """
    )


@st.cache_data(ttl=20)
def load_umd_ta_ra_logs() -> pd.DataFrame:
    return read_sql(
        """
        SELECT *
        FROM umd_ta_ra_outreach_logs
        ORDER BY created_at DESC, id DESC
        LIMIT 300
        """
    )


@st.cache_data(ttl=20)
def load_umd_ta_ra_campaigns() -> pd.DataFrame:
    return read_sql(
        """
        SELECT *
        FROM umd_ta_ra_campaigns
        ORDER BY created_at DESC, id DESC
        LIMIT 100
        """
    )


@st.cache_data(ttl=20)
def load_umd_ta_ra_campaign_recipients(campaign_id: int) -> pd.DataFrame:
    return read_sql(
        """
        SELECT
            r.*,
            c.name,
            c.email,
            c.title,
            c.department,
            c.fit_score,
            c.fit_bucket,
            d.subject
        FROM umd_ta_ra_campaign_recipients r
        JOIN umd_ta_ra_contacts c ON c.id = r.contact_id
        JOIN umd_ta_ra_email_drafts d ON d.id = r.draft_id
        WHERE r.campaign_id = ?
        ORDER BY r.id ASC
        """,
        (campaign_id,),
    )


def next_8am_iso() -> str:
    now = datetime.now()
    send_time = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if now >= send_time:
        send_time += timedelta(days=1)
    return send_time.isoformat(timespec="seconds")


def next_hybrid_send_window(now: datetime | None = None) -> tuple[str, str]:
    now = now or datetime.now()
    allowed_weekdays = {0: "Monday primary batch", 1: "Tuesday overflow batch", 2: "Wednesday overflow batch"}
    today_send = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if now.weekday() in allowed_weekdays and now < today_send:
        return today_send.isoformat(timespec="seconds"), allowed_weekdays[now.weekday()]
    for days_ahead in range(1, 8):
        candidate = now + timedelta(days=days_ahead)
        if candidate.weekday() in allowed_weekdays:
            send_time = candidate.replace(hour=8, minute=0, second=0, microsecond=0)
            return send_time.isoformat(timespec="seconds"), allowed_weekdays[candidate.weekday()]
    return today_send.isoformat(timespec="seconds"), "Next hybrid send"


def load_manual_send_candidates(limit: int = 50) -> pd.DataFrame:
    with db.connect(settings.database_path, settings.database_url) as conn:
        rows = db.get_send_queue_candidates(conn, limit, settings.min_score_to_send)
    if not rows:
        return pd.DataFrame()
    records = []
    for row in rows:
        record = dict(row)
        record["email_subject"] = record.get("queue_email_subject") or ""
        records.append(record)
    return pd.DataFrame(records)


def get_lead_row(lead_id: int):
    with db.connect(settings.database_path, settings.database_url) as conn:
        return conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()


def mark_manual_skip(lead_id: int, note: str) -> None:
    reason = note.strip() or "Manually skipped in Daily Full-Time Review"
    now = utc_now_iso()
    with db.connect(settings.database_path, settings.database_url) as conn:
        conn.execute(
            """
            UPDATE leads
            SET status = 'skipped',
                queue_status = 'skipped',
                approved_for_send = 0,
                manually_skipped = 1,
                manual_review_note = ?,
                rejection_reason = ?,
                error_message = ?,
                skipped_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (reason[:1000], reason[:1000], reason[:1000], now, now, lead_id),
        )
        conn.execute(
            """
            UPDATE send_queue
            SET queue_status = 'skipped',
                failure_reason = ?,
                updated_at = ?
            WHERE lead_id = ?
            """,
            (reason[:1000], now, lead_id),
        )
        conn.commit()


def remove_from_queue(lead_id: int, note: str) -> None:
    reason = note.strip() or "Removed from 8 AM queue in Daily Full-Time Review"
    now = utc_now_iso()
    with db.connect(settings.database_path, settings.database_url) as conn:
        conn.execute(
            """
            UPDATE leads
            SET status = CASE WHEN status = 'queued' THEN 'send_ready' ELSE status END,
                queue_status = 'not_queued',
                approved_for_send = 0,
                manual_review_note = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (reason[:1000], now, lead_id),
        )
        conn.execute(
            """
            UPDATE send_queue
            SET queue_status = 'not_queued',
                failure_reason = ?,
                updated_at = ?
            WHERE lead_id = ?
            """,
            (reason[:1000], now, lead_id),
        )
        conn.commit()


def approve_for_queue(lead_id: int, note: str = "") -> tuple[bool, str]:
    row = get_lead_row(lead_id)
    if not row:
        return False, "Lead was not found."
    lead = Lead.from_row(row)
    if not lead.email:
        return False, "This lead cannot be queued because it is missing an email."
    if lead.email_sent:
        return False, "This lead has already been sent."
    if lead.lead_score < settings.min_score_to_send:
        return False, f"Score must be at least {settings.min_score_to_send} to queue this lead."
    if lead.manually_skipped:
        lead.manually_skipped = False
    lead.status = "send_ready"
    lead.queue_status = "queued"
    lead.approved_for_send = True
    lead.manual_review_note = note.strip()
    subject, body = render_email(lead, settings)
    with db.connect(settings.database_path, settings.database_url) as conn:
        db.upsert_lead(conn, lead)
        queued_id = db.queue_lead_for_send(conn, lead, next_8am_iso(), subject, body)
    if not queued_id:
        return False, "Lead could not be queued."
    return True, "Lead approved and queued for the next 8:00 AM sender run."


def status_badge(status: str) -> str:
    status = status or "unknown"
    colors = {
        "raw": "#475569",
        "scored": "#2563eb",
        "enriched": "#7c3aed",
        "send_ready": "#059669",
        "drafted": "#2563eb",
        "needs_review": "#f59e0b",
        "approved": "#0f766e",
        "ready": "#0f766e",
        "sending": "#2563eb",
        "paused": "#f59e0b",
        "completed": "#16a34a",
        "stopped": "#64748b",
        "pending": "#475569",
        "queued": "#0f766e",
        "sent": "#16a34a",
        "contacted": "#16a34a",
        "follow_up_needed": "#7c3aed",
        "not_relevant": "#64748b",
        "rejected": "#dc2626",
        "skipped": "#b45309",
        "failed": "#b91c1c",
        "duplicate": "#6b7280",
        "missing_email": "#9333ea",
        "pending_credit_limit": "#f59e0b",
    }
    color = colors.get(status, "#334155")
    return f"<span class='status-pill' style='color:{color}; background:#f8fafc; border:1px solid #e2e8f0;'>{status}</span>"


def classify_rejection(row: pd.Series) -> str:
    reason = str(row.get("rejection_reason") or row.get("failure_reason") or "").lower()
    status = str(row.get("status") or "").lower()
    title = str(row.get("title") or "").lower()
    if "low score" in reason or "below" in reason:
        return "Low score"
    if "missing email" in reason or "no reliable email" in reason or not row.get("email"):
        return "Missing email"
    if "duplicate" in reason or status == "duplicate":
        return "Duplicate"
    if "outside" in reason or "non-dmv" in reason:
        return "Outside target location"
    if "irrelevant" in reason or "sales" in title:
        return "Irrelevant title"
    if "weekly contact limit" in reason:
        return "Company weekly limit reached"
    if "credit" in reason and ("reserve" in reason or "low" in reason or "limit" in reason):
        return "Apollo credit guardrail"
    if "apollo" in reason and "failed" in reason:
        return "Apollo enrichment failed"
    if "gmail" in reason or "send" in reason or "validation" in reason:
        return "Gmail/send validation failed"
    return "Other"


def setup_page() -> None:
    st.set_page_config(page_title="Full-Time Job Outreach Dashboard", layout="wide")
    st.markdown(
        """
        <style>
        .main .block-container {
            padding-top: 1.3rem;
            max-width: 1500px;
        }
        h1, h2, h3 {
            letter-spacing: 0;
        }
        .workflow-hero {
            border: 1px solid #1f2937;
            border-radius: 8px;
            background: #111827;
            padding: 18px 20px;
            margin: 0.4rem 0 1rem 0;
        }
        .workflow-hero-kicker {
            color: #93c5fd;
            font-size: 0.82rem;
            font-weight: 800;
            letter-spacing: 0;
            text-transform: uppercase;
        }
        .workflow-hero-title {
            color: #f8fafc;
            font-size: clamp(1.65rem, 3vw, 2.55rem);
            line-height: 1.08;
            font-weight: 900;
            margin-top: 6px;
        }
        .workflow-hero-copy {
            color: #cbd5e1;
            max-width: 980px;
            margin-top: 8px;
            font-size: 1rem;
            line-height: 1.45;
        }
        .workflow-callout {
            border: 1px solid;
            border-radius: 8px;
            padding: 13px 16px;
            margin: 0.7rem 0;
        }
        .workflow-callout-title {
            font-weight: 850;
            font-size: 0.96rem;
            line-height: 1.25;
            margin-bottom: 3px;
        }
        .workflow-callout-body {
            color: #334155;
            font-size: 0.94rem;
            line-height: 1.38;
        }
        .schedule-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 12px;
            margin: 0.7rem 0 1.1rem 0;
        }
        .schedule-card {
            background: #0f172a;
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 14px;
            min-height: 104px;
        }
        .schedule-card-title {
            color: #f8fafc;
            font-weight: 850;
            line-height: 1.25;
            margin-bottom: 7px;
        }
        .schedule-card-copy {
            color: #cbd5e1;
            font-size: 0.9rem;
            line-height: 1.35;
        }
        @media (max-width: 980px) {
            .schedule-grid {
                grid-template-columns: 1fr;
            }
        }
        .metric-card,
        div[data-testid="stMetric"] {
            background: #f8fafc;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            padding: 14px 15px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.12);
        }
        .metric-card {
            min-height: 118px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            gap: 9px;
            overflow: hidden;
            box-sizing: border-box;
            min-width: 0;
        }
        .metric-card-label {
            color: #475569 !important;
            font-size: 0.84rem;
            font-weight: 750;
            line-height: 1.24;
            white-space: normal;
            overflow-wrap: anywhere;
        }
        .metric-card-value {
            color: #0f172a !important;
            font-size: clamp(1.28rem, 1.55vw, 1.9rem);
            font-weight: 850;
            line-height: 1.08;
            max-width: 100%;
            min-width: 0;
            white-space: normal;
            overflow-wrap: anywhere;
            word-break: break-word;
        }
        .metric-card-value-long {
            font-size: clamp(1.02rem, 1.15vw, 1.35rem);
            line-height: 1.1;
        }
        @media (max-width: 1200px) {
            .metric-card {
                min-height: 104px;
                padding: 12px 14px;
            }
            .metric-card-value {
                font-size: 1.65rem;
            }
            .metric-card-value-long {
                font-size: 1.2rem;
            }
        }
        div[data-testid="stMetric"] label,
        div[data-testid="stMetric"] [data-testid="stMetricLabel"],
        div[data-testid="stMetric"] [data-testid="stMetricLabel"] p {
            color: #334155 !important;
            font-weight: 700;
        }
        div[data-testid="stMetric"] [data-testid="stMetricValue"],
        div[data-testid="stMetric"] [data-testid="stMetricValue"] div,
        div[data-testid="stMetric"] [data-testid="stMetricValue"] p {
            color: #0f172a !important;
            font-weight: 800;
        }
        div[data-testid="stMetric"] [data-testid="stMetricDelta"],
        div[data-testid="stMetric"] [data-testid="stMetricDelta"] div,
        div[data-testid="stMetric"] [data-testid="stMetricDelta"] p {
            color: #475569 !important;
        }
        .status-pill {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 600;
            background: #eef2ff;
            color: #3730a3;
        }
        div[data-testid="stDataFrame"] {
            border: 1px solid #1f2937;
            border-radius: 8px;
            overflow: hidden;
        }
        section[data-testid="stSidebar"] {
            border-right: 1px solid #1f2937;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def daily_discovery_review() -> None:
    st.header("Daily Full-Time Discovery Review")
    selected_date = st.date_input("Discovery date", value=date.today())
    latest_run_df = load_latest_discovery_run(selected_date)
    latest_run = latest_run_df.iloc[0] if not latest_run_df.empty else None
    run_id = int(latest_run["id"]) if latest_run is not None else None
    leads = load_daily_review_leads(selected_date, run_id)
    search_logs = load_discovery_search_logs(run_id)

    if settings.dry_run:
        st.warning("DRY_RUN is active. The 8 AM full-time sender will not send live emails.")
    else:
        st.error("Live full-time sending is enabled. Review the queued table carefully before 8:00 AM.")

    if leads.empty:
        st.info("No full-time contacts were found for this date yet. After nightly discovery runs, this page will populate automatically.")
        if st.button("Refresh dashboard data"):
            st.cache_data.clear()
            st.rerun()
        return

    leads = leads.copy()
    leads["score"] = leads["score"].fillna(0).astype(int)
    leads["has_email"] = leads["email"].fillna("").str.len() > 0
    leads["review_status"] = leads["status"].fillna("")
    leads.loc[leads["queue_status"].fillna("") == "queued", "review_status"] = "queued"
    leads.loc[(~leads["has_email"]) & (leads["status"].isin(["raw", "rejected", "skipped"])), "review_status"] = "missing_email"
    leads.loc[leads["rejection_reason"].fillna("").str.contains("duplicate", case=False, na=False), "review_status"] = "duplicate"
    queued = leads[
        (leads["queue_status"] == "queued")
        & (leads["status"].isin(["queued", "send_ready"]))
        & (leads["score"] >= settings.min_score_to_send)
        & leads["has_email"]
        & (leads["approved_for_send"].fillna(0).astype(int) == 1)
        & (leads["manually_skipped"].fillna(0).astype(int) == 0)
    ].copy()

    if not queued.empty and not settings.dry_run:
        st.warning(f"{len(queued)} full-time contact(s) are queued for live sending at 8:00 AM.")

    rejected = leads[leads["status"].isin(["rejected", "skipped", "failed"]) | (leads["review_status"] == "missing_email")]
    missing_email = int((~leads["has_email"]).sum())
    duplicate_count = int(leads["rejection_reason"].fillna("").str.contains("duplicate", case=False, na=False).sum())
    enriched_count = int((leads["apollo_used"].fillna(0).astype(int) == 1).sum())
    scored_count = int((leads["score"] > 0).sum())
    raw_contacts = int(latest_run["raw_candidates"]) if latest_run is not None else len(leads)

    cols = st.columns(8)
    metrics = [
        ("Full-time contacts found", raw_contacts),
        ("Contacts scored", scored_count),
        ("Contacts enriched", enriched_count),
        ("Full-time send-ready", int(leads["status"].isin(["send_ready", "queued"]).sum())),
        ("Queued for 8:00 AM", len(queued)),
        ("Rejected/skipped", len(rejected)),
        ("Missing emails", missing_email),
        ("Duplicate contacts", duplicate_count),
    ]
    for col, (label, value) in zip(cols, metrics):
        with col:
            metric_card(label, value)

    tab_found, tab_queued, tab_rejected, tab_run = st.tabs(
        ["Full-Time Leads", "Queued for 8AM", "Rejected/Skipped", "Run Details"]
    )

    with tab_found:
        st.subheader("Full-Time Contacts Found Today")
        f1, f2, f3, f4 = st.columns(4)
        with f1:
            status_filter = st.multiselect("Status", sorted(leads["review_status"].dropna().unique().tolist()))
        with f2:
            score_range = st.slider("Score range", 0, 100, (0, 100), key="daily_score_range")
        with f3:
            search_tiers = st.multiselect("Search tier", sorted(leads["search_tier"].fillna("").unique().tolist()))
        with f4:
            email_filter = st.selectbox("Email", ["All", "Has email", "Missing email"])
        c1, c2, c3 = st.columns(3)
        with c1:
            company_filter = st.text_input("Company contains", key="daily_company")
        with c2:
            title_filter = st.text_input("Title keyword", key="daily_title")
        with c3:
            reason_filter = st.text_input("Rejection reason contains", key="daily_reason")

        filtered = leads.copy()
        if status_filter:
            filtered = filtered[filtered["review_status"].isin(status_filter)]
        if search_tiers:
            filtered = filtered[filtered["search_tier"].fillna("").isin(search_tiers)]
        filtered = filtered[(filtered["score"] >= score_range[0]) & (filtered["score"] <= score_range[1])]
        if email_filter == "Has email":
            filtered = filtered[filtered["has_email"]]
        elif email_filter == "Missing email":
            filtered = filtered[~filtered["has_email"]]
        if company_filter:
            filtered = filtered[filtered["company"].fillna("").str.contains(company_filter, case=False, na=False)]
        if title_filter:
            filtered = filtered[filtered["title"].fillna("").str.contains(title_filter, case=False, na=False)]
        if reason_filter:
            filtered = filtered[filtered["rejection_reason"].fillna("").str.contains(reason_filter, case=False, na=False)]

        table = filtered[
            [
                "full_name",
                "title",
                "company",
                "location",
                "linkedin_url",
                "email",
                "score",
                "review_status",
                "search_tier",
                "source",
                "rejection_reason",
                "created_at",
            ]
        ].rename(
            columns={
                "full_name": "Full name",
                "title": "Title",
                "company": "Company",
                "location": "Location",
                "linkedin_url": "LinkedIn URL",
                "email": "Email",
                "score": "Score",
                "review_status": "Status",
                "search_tier": "Search tier",
                "source": "Source",
                "rejection_reason": "Rejection/skipped reason",
                "created_at": "Created time",
            }
        )
        st.dataframe(table, use_container_width=True, hide_index=True)

        st.subheader("Manual Review Controls")
        labels = {
            f"{row.full_name or 'Unknown'} | {row.company or 'Unknown company'} | score {row.score} | #{row.id}": int(row.id)
            for row in filtered.itertuples()
        }
        if labels:
            selected_label = st.selectbox("Select a lead", list(labels.keys()))
            selected_id = labels[selected_label]
            review_note = st.text_input("Manual review note")
            b1, b2, b3, b4 = st.columns(4)
            with b1:
                if st.button("Mark skipped"):
                    mark_manual_skip(selected_id, review_note)
                    st.cache_data.clear()
                    st.success("Lead marked skipped. No email was sent.")
                    st.rerun()
            with b2:
                if st.button("Approve / queue"):
                    ok, message = approve_for_queue(selected_id, review_note)
                    st.cache_data.clear()
                    if ok:
                        st.success(message)
                        st.rerun()
                    else:
                        st.error(message)
            with b3:
                if st.button("Remove from 8 AM queue"):
                    remove_from_queue(selected_id, review_note)
                    st.cache_data.clear()
                    st.success("Lead removed from the 8 AM queue. No email was sent.")
                    st.rerun()
            with b4:
                if st.button("Refresh dashboard data"):
                    st.cache_data.clear()
                    st.rerun()
        else:
            st.info("No visible leads match the current filters.")

    with tab_queued:
        st.subheader("Full-Time Emails Queued for 8:00 AM")
        if queued.empty:
            st.info("No full-time contacts queued for 8:00 AM yet.")
        else:
            queued["scheduled_send_time"] = queued["scheduled_send_time"].fillna(queued["queued_send_time"])
            queued_table = queued[
                [
                    "full_name",
                    "title",
                    "company",
                    "email",
                    "score",
                    "email_subject",
                    "email_body",
                    "scheduled_send_time",
                    "queue_status",
                    "failure_reason",
                ]
            ].copy()
            queued_table["Email preview snippet"] = queued_table["email_body"].fillna("").str.slice(0, 180)
            queued_table["Scheduled send time"] = "8:00 AM"
            queued_table = queued_table.drop(columns=["email_body", "scheduled_send_time"]).rename(
                columns={
                    "full_name": "Full name",
                    "title": "Title",
                    "company": "Company",
                    "email": "Email",
                    "score": "Score",
                    "email_subject": "Email subject",
                    "queue_status": "Send status",
                    "failure_reason": "Reason if not queued",
                }
            )
            st.dataframe(queued_table, use_container_width=True, hide_index=True)

            queued_labels = {
                f"{row.full_name or 'Unknown'} | {row.company or 'Unknown company'} | {row.email} | #{row.id}": row
                for row in queued.itertuples()
            }
            selected_queue_label = st.selectbox("Select queued contact for preview", list(queued_labels.keys()))
            selected = queued_labels[selected_queue_label]
            with st.expander("Email Preview", expanded=True):
                st.write(f"Recipient name: {selected.full_name}")
                st.write(f"Recipient email: {selected.email}")
                st.write(f"Company: {selected.company}")
                st.write(f"Subject line: {selected.email_subject}")
                st.text_area("Full email body", selected.email_body or "", height=360)
                st.json(
                    {
                        "first_name": (selected.full_name or "").split(" ")[0],
                        "company_name": selected.company,
                        "role": selected.title,
                        "search_tier": selected.search_tier,
                        "source": selected.source,
                    }
                )
                st.write("Why selected")
                st.info(
                    f"Full-time score {selected.score} met the send threshold of {settings.min_score_to_send}; "
                    f"email exists; queue status is {selected.queue_status}; search tier is {selected.search_tier or 'unknown'}."
                )
                try:
                    st.json(json.loads(selected.score_breakdown or "{}"))
                except json.JSONDecodeError:
                    st.code(selected.score_breakdown or "{}")

    with tab_rejected:
        st.subheader("Rejected / Skipped Contacts")
        if rejected.empty:
            st.info("No rejected or skipped contacts for this discovery run.")
        else:
            rejected = rejected.copy()
            rejected["reason_group"] = rejected.apply(classify_rejection, axis=1)
            for reason, group in rejected.groupby("reason_group"):
                with st.expander(f"{reason} ({len(group)})"):
                    st.dataframe(
                        group[
                            [
                                "full_name",
                                "title",
                                "company",
                                "location",
                                "email",
                                "score",
                                "status",
                                "search_tier",
                                "rejection_reason",
                            ]
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )

    with tab_run:
        st.subheader("Run Metadata")
        if latest_run is None:
            st.info("No discovery run was recorded for this date.")
        else:
            details = json.loads(latest_run["details_json"] or "{}")
            m1, m2, m3, m4 = st.columns(4)
            with m1:
                metric_card("Run ID", int(latest_run["id"]))
            with m2:
                metric_card("Status", latest_run["status"])
            with m3:
                metric_card("Enriched", int(latest_run["enriched_count"]))
            with m4:
                metric_card("Send-ready", int(latest_run["send_ready_count"]))
            st.write(f"Start time: {latest_run['started_at']}")
            st.write(f"End time: {latest_run['completed_at']}")
            if latest_run["error_summary"]:
                st.error(latest_run["error_summary"])
            if not search_logs.empty:
                tier_counts = search_logs.groupby(["tier_name", "search_type"], dropna=False).agg(
                    raw_candidates=("result_count", "sum"),
                    new_unique=("new_unique_count", "sum"),
                ).reset_index()
                st.dataframe(tier_counts, use_container_width=True, hide_index=True)
                st.plotly_chart(
                    px.bar(tier_counts, x="tier_name", y="raw_candidates", color="search_type", title="Raw candidates found per tier"),
                    use_container_width=True,
                )
            else:
                st.info("No Apollo search tier logs were attached to this run.")
            with st.expander("Detailed run JSON"):
                st.json(details)


def overview() -> None:
    leads = load_leads()
    runs = load_runs()
    today_runs = read_sql(
        """
        SELECT
            COALESCE(SUM(raw_candidates), 0) AS raw_candidates,
            COALESCE(SUM(enriched_count), 0) AS enriched_count,
            COALESCE(SUM(send_ready_count), 0) AS send_ready_count,
            COALESCE(SUM(sent_count), 0) AS sent_count,
            COALESCE(SUM(skipped_count), 0) AS skipped_count,
            COALESCE(SUM(failed_count), 0) AS failed_count
        FROM automation_runs
        WHERE substr(started_at, 1, 10) = ?
        """,
        (date.today().isoformat(),),
    ).iloc[0]
    credit_summary = load_credit_summary(datetime.utcnow().date().isoformat())
    sent_today = int(today_runs["sent_count"])
    remaining_capacity = max(settings.daily_send_limit - sent_today, 0)
    send_ready = int(leads["status"].isin(["send_ready", "queued"]).sum()) if not leads.empty else 0
    failed = int((leads["status"] == "failed").sum()) if not leads.empty else 0
    next_send_time, next_send_label = next_hybrid_send_window()

    if settings.dry_run:
        workflow_callout(
            "Dry-run is active",
            "Scheduled and manual sender runs will draft/log only until DRY_RUN is disabled.",
            "warning",
        )
    else:
        workflow_callout(
            "Live full-time sending is enabled",
            "The scheduled sender can send live Gmail API emails when the launchd wrapper supplies live confirmation.",
            "danger",
        )

    st.markdown(
        f"""
        <div class="schedule-grid">
            <div class="schedule-card">
                <div class="schedule-card-title">Hybrid Send Cadence</div>
                <div class="schedule-card-copy">Discovery runs nightly. Sending concentrates on Monday, with Tuesday and Wednesday reserved for overflow.</div>
            </div>
            <div class="schedule-card">
                <div class="schedule-card-title">Next Scheduled Window</div>
                <div class="schedule-card-copy">{html.escape(next_send_label)}<br>{html.escape(next_send_time)}</div>
            </div>
            <div class="schedule-card">
                <div class="schedule-card-title">Manual Off-Schedule Send</div>
                <div class="schedule-card-copy">Use the Manual Send page to copy local Terminal commands for reviewed full-time queue items.</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    cols = st.columns(8)
    with cols[0]:
        metric_card("Full-Time Found", int(today_runs["raw_candidates"]))
    with cols[1]:
        metric_card("Enriched Today", int(today_runs["enriched_count"]))
    with cols[2]:
        metric_card("Full-Time Ready", send_ready)
    with cols[3]:
        metric_card("Sent Today", sent_today)
    with cols[4]:
        metric_card("Failed Sends", failed)
    with cols[5]:
        metric_card("Apollo Used Today", f"{int(credit_summary['daily_used'])}/{settings.apollo_daily_credit_limit}")
    with cols[6]:
        metric_card(
            "Apollo Monthly Usage",
            f"{int(credit_summary['monthly_used']):,}/{int(credit_summary['monthly_available']):,}",
            "Credits logged by this workflow for the current Apollo credit month.",
        )
    with cols[7]:
        metric_card(
            "Apollo Remaining",
            int(credit_summary["monthly_remaining"]),
            "Base monthly credits plus top-ups and adjustments minus logged usage.",
        )

    st.caption(f"Remaining daily send capacity: {remaining_capacity}")

    left, right = st.columns(2)
    with left:
        if runs.empty:
            st.info("No automation runs have been recorded yet.")
        else:
            daily = read_sql(
                """
                SELECT substr(started_at, 1, 10) AS day, COALESCE(SUM(sent_count), 0) AS sent
                FROM automation_runs
                GROUP BY day
                ORDER BY day
                """
            )
            st.plotly_chart(px.bar(daily, x="day", y="sent", title="Daily Full-Time Emails Sent"), use_container_width=True)
    with right:
        if leads.empty:
            st.info("No full-time leads in the database yet.")
        else:
            by_status = leads.groupby("status", dropna=False).size().reset_index(name="count")
            st.plotly_chart(px.pie(by_status, values="count", names="status", title="Full-Time Leads by Status"), use_container_width=True)


def manual_full_time_sender() -> None:
    st.header("Manual Full-Time Send Commands")
    st.caption("Use this page to review the queue and copy local Mac commands for off-schedule full-time sends.")

    candidates = load_manual_send_candidates(100)
    sent_today_df = read_sql(
        """
        SELECT COALESCE(SUM(sent_count), 0) AS sent_count
        FROM automation_runs
        WHERE run_type = 'sender'
          AND substr(started_at, 1, 10) = ?
        """,
        (date.today().isoformat(),),
    )
    sent_today = int(sent_today_df.iloc[0]["sent_count"]) if not sent_today_df.empty else 0
    remaining_capacity = max(settings.daily_send_limit - sent_today, 0)
    next_send_time, next_send_label = next_hybrid_send_window()
    resume_ready = bool(settings.attach_resume and settings.resume_file.exists())
    gmail_ready = bool(settings.gmail_credentials_file.exists() and settings.gmail_token_file.exists())

    if settings.dry_run:
        workflow_callout(
            "Dry-run is active",
            "The local live command will not send until DRY_RUN is disabled in your Mac runtime .env.",
            "warning",
        )
    else:
        workflow_callout(
            "Copy-paste local send commands are ready",
            "Run these commands on your Mac. The hosted dashboard only generates commands and never sends Gmail messages directly.",
            "danger",
        )

    cols = st.columns(6)
    with cols[0]:
        metric_card("Queued Candidates", len(candidates))
    with cols[1]:
        metric_card("Daily Capacity", remaining_capacity)
    with cols[2]:
        metric_card("Default Daily Limit", settings.daily_send_limit)
    with cols[3]:
        metric_card("Min Send Score", settings.min_score_to_send)
    with cols[4]:
        metric_card("Resume", "Ready" if resume_ready else "Missing")
    with cols[5]:
        metric_card("Gmail OAuth", "Ready" if gmail_ready else "Missing")

    st.markdown(
        f"""
        <div class="schedule-grid">
            <div class="schedule-card">
                <div class="schedule-card-title">Normal Schedule</div>
                <div class="schedule-card-copy">Next automatic window: {html.escape(next_send_label)} at {html.escape(next_send_time)}.</div>
            </div>
            <div class="schedule-card">
                <div class="schedule-card-title">Copy-Paste Workflow</div>
                <div class="schedule-card-copy">Use the generated commands below in Terminal on your Mac. The dashboard will refresh from the shared database after sending.</div>
            </div>
            <div class="schedule-card">
                <div class="schedule-card-title">Attachment Rule</div>
                <div class="schedule-card-copy">Live sends require the resume PDF to be readable by the local Mac runtime, not the hosted dashboard.</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.subheader("Queued Email Preview")
    if candidates.empty:
        st.info("No approved full-time contacts are currently queued for sending.")
    else:
        preview = candidates[
            [
                "full_name",
                "title",
                "company_name",
                "email",
                "lead_score",
                "queue_scheduled_send_time",
                "email_subject",
            ]
        ].rename(
            columns={
                "full_name": "Full name",
                "title": "Title",
                "company_name": "Company",
                "email": "Email",
                "lead_score": "Score",
                "queue_scheduled_send_time": "Queued time",
                "email_subject": "Subject",
            }
        )
        st.dataframe(preview.head(50), use_container_width=True, hide_index=True)

    st.subheader("Copy-Paste Terminal Commands")
    c1, c2, c3 = st.columns(3)
    with c1:
        max_limit = max(1, min(settings.daily_send_limit or 30, max(len(candidates), 1)))
        default_limit = min(5, max_limit)
        send_limit = st.number_input("Emails to process", min_value=1, max_value=max_limit, value=default_limit)
    with c2:
        include_logs = st.checkbox("Include log tail command", value=True)
    with c3:
        st.write("")
        st.write("")
        st.caption("Run dry-run first, then live command only after reviewing output.")

    runtime_dir = '$HOME/Library/Application\\ Support/cold_email_workflow'
    dry_run_command = (
        f'cd {runtime_dir} && '
        f'.venv/bin/python run_sender.py --dry-run --limit {int(send_limit)}'
    )
    live_command = (
        f'cd {runtime_dir} && '
        f'LIVE_SEND_CONFIRM=I_UNDERSTAND_SEND_LIVE_EMAILS '
        f'.venv/bin/python run_sender.py --live --limit {int(send_limit)}'
    )
    status_command = f'cd {runtime_dir} && .venv/bin/python main.py status'
    log_command = f'cd {runtime_dir} && tail -n 80 logs/send.log'

    if candidates.empty:
        st.warning("No queued candidates are available, so these commands will not send anything yet.")
    if not resume_ready:
        st.warning("Hosted dashboard may show the resume as missing because it cannot inspect your Mac files. The local command will validate the Mac runtime resume path before sending.")
    if not gmail_ready:
        st.warning("Hosted dashboard may not see Gmail OAuth files. The local Mac command will use the runtime credentials.json and token.json.")

    workflow_callout(
        "Important",
        "The hosted dashboard does not send directly. Copy these commands into Terminal on your Mac so Gmail OAuth, resume attachment, local logs, and launchd-safe paths are used.",
        "info",
    )

    st.markdown("**1. Dry-run first**")
    st.code(dry_run_command, language="bash")

    st.markdown("**2. If the dry-run looks good, run live send**")
    st.code(live_command, language="bash")

    st.markdown("**3. Confirm status afterward**")
    st.code(status_command, language="bash")

    if include_logs:
        st.markdown("**4. Check local send logs**")
        st.code(log_command, language="bash")

    with st.expander("Safety details"):
        st.write(
            "These commands use the same full-time sender as launchd: score threshold, queue status, "
            "approved flag, duplicate checks, company weekly limits, full-time wording validation, "
            "Gmail API sending, resume attachment validation, and email event logging."
        )
        st.write("The live command bypasses only the weekday shell wrapper. It still uses run_sender.py safety confirmation.")
        st.write(f"Resume path: {settings.resume_file}")
        st.write(f"Gmail credentials: {settings.gmail_credentials_file}")
        st.write(f"Gmail token: {settings.gmail_token_file}")


def lead_pipeline() -> None:
    leads = load_leads()
    if leads.empty:
        st.info("No full-time leads yet. Run full-time discovery to populate the pipeline.")
        return
    stages = ["raw", "rejected", "enriched", "send_ready", "sent"]
    counts = []
    for status in stages:
        counts.append({"stage": status, "count": int((leads["status"] == status).sum())})
    st.plotly_chart(px.funnel(pd.DataFrame(counts), x="count", y="stage", title="Full-Time Lead Funnel"), use_container_width=True)

    st.subheader("Filters")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        statuses = st.multiselect("Status", sorted(leads["status"].dropna().unique().tolist()))
    with c2:
        score_range = st.slider("Score range", 0, 100, (0, 100))
    with c3:
        company = st.text_input("Company contains")
    with c4:
        title = st.text_input("Title keyword")

    filtered = leads.copy()
    if statuses:
        filtered = filtered[filtered["status"].isin(statuses)]
    filtered = filtered[(filtered["score"].fillna(0) >= score_range[0]) & (filtered["score"].fillna(0) <= score_range[1])]
    if company:
        filtered = filtered[filtered["company"].fillna("").str.contains(company, case=False, na=False)]
    if title:
        filtered = filtered[filtered["title"].fillna("").str.contains(title, case=False, na=False)]
    st.dataframe(filtered, use_container_width=True, hide_index=True)


def lead_table() -> None:
    leads = load_leads()
    if leads.empty:
        st.info("No full-time leads yet.")
        return
    query = st.text_input("Search full-time leads")
    filtered = leads
    if query:
        haystack = filtered.fillna("").astype(str).agg(" ".join, axis=1)
        filtered = filtered[haystack.str.contains(query, case=False, na=False)]
    st.dataframe(
        filtered[
            [
                "full_name",
                "title",
                "company",
                "location",
                "email",
                "email_status",
                "score",
                "status",
                "rejection_reason",
                "updated_at",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )


def errors_failures() -> None:
    leads = load_leads()
    events = load_events()
    if leads.empty and events.empty:
        st.info("No errors or failures logged yet.")
        return
    failures = leads[leads["status"].isin(["failed", "skipped", "rejected"])] if not leads.empty else pd.DataFrame()
    if not failures.empty:
        reasons = failures.groupby("rejection_reason", dropna=False).size().reset_index(name="count")
        st.plotly_chart(px.bar(reasons, x="count", y="rejection_reason", orientation="h", title="Failure and Skip Reasons"), use_container_width=True)
        st.dataframe(failures[["full_name", "company", "email", "status", "rejection_reason", "updated_at"]], use_container_width=True, hide_index=True)
    failed_events = events[events["event_type"].isin(["failed", "skipped"])] if not events.empty else pd.DataFrame()
    if not failed_events.empty:
        st.subheader("Recent Failed/Skipped Events")
        st.dataframe(failed_events, use_container_width=True, hide_index=True)


def automation_runs() -> None:
    runs = load_runs()
    if runs.empty:
        st.info("No automation runs have been recorded yet.")
        return
    st.dataframe(
        runs[
            [
                "id",
                "run_type",
                "started_at",
                "completed_at",
                "status",
                "raw_candidates",
                "enriched_count",
                "send_ready_count",
                "sent_count",
                "failed_count",
                "skipped_count",
                "error_summary",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )
    for _, row in runs.head(20).iterrows():
        with st.expander(f"Run {row['id']} | {row['run_type']} | {row['started_at']}"):
            st.json(json.loads(row["details_json"] or "{}"))


def apollo_debugger() -> None:
    logs = load_search_logs()
    if logs.empty:
        st.info("No Apollo search logs yet. Run full-time discovery to see tier performance.")
        return
    tier_perf = logs.groupby(["tier_name", "search_type"], dropna=False).agg(
        result_count=("result_count", "sum"),
        new_unique_count=("new_unique_count", "sum"),
    ).reset_index()
    st.plotly_chart(px.bar(tier_perf, x="tier_name", y="result_count", color="search_type", title="Full-Time Apollo Search Tier Performance"), use_container_width=True)
    saved = int((logs["result_count"] - logs["new_unique_count"]).clip(lower=0).sum())
    st.success(f"Estimated Apollo enrichments avoided through dedupe/low-fit filtering: {saved}")
    st.dataframe(logs[["started_at", "tier_name", "search_type", "page", "result_count", "new_unique_count", "notes"]], use_container_width=True, hide_index=True)
    latest = logs.iloc[0]
    with st.expander("Latest Apollo query parameters"):
        st.json(json.loads(latest["params_json"] or "{}"))


def _month_options(months_back: int = 18) -> list[date]:
    today = date.today().replace(day=1)
    options = []
    year = today.year
    month = today.month
    for _ in range(months_back):
        options.append(date(year, month, 1))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return options


def _month_label(month_start: date) -> str:
    return f"{calendar.month_name[month_start.month]} {month_start.year}"


def _credit_health_banner(summary: dict) -> None:
    remaining = int(summary["monthly_remaining"])
    available = int(summary["monthly_available"])
    if summary["health"] == "critical":
        st.error(
            "Critical: Apollo credits are close to reserve. "
            "Enrichment should pause unless manually overridden."
        )
    elif summary["health"] == "warning":
        st.warning("Warning: Apollo credits are below 25%. Consider reducing enrichment limits.")
    else:
        st.success("Healthy: You have enough Apollo credits for the current workflow.")
    st.caption(f"Current remaining balance: {remaining:,} of {available:,} available credits.")


def _insert_manual_credit_event(event_type: str, amount: int, event_date: date, note: str) -> None:
    with db.connect(settings.database_path, settings.database_url) as conn:
        description = note.strip() or (
            "Manual Apollo credit top-up entered from dashboard"
            if event_type == "top_up"
            else "Manual Apollo credit correction entered from dashboard"
        )
        credits_service.record_credit_event(
            conn,
            event_type=event_type,
            credit_cost=0,
            credit_delta=int(amount),
            description=description,
            source="manual_ui",
            created_at=credits_service.event_created_at_for_date(event_date),
        )


def _remaining_over_time(events: pd.DataFrame, summary: dict) -> pd.DataFrame:
    start = date.fromisoformat(summary["period_start"])
    end = date.fromisoformat(summary["period_end"])
    event_rows = []
    if not events.empty:
        normalized = events.copy()
        normalized["event_date"] = pd.to_datetime(normalized["created_at"]).dt.date
        event_rows = normalized.to_dict("records")
    balance = int(summary["base_monthly_credits"])
    rows = []
    current = start
    while current < end:
        for row in event_rows:
            if row["event_date"] == current:
                balance += int(row.get("credit_delta") or 0)
                balance -= int(row.get("credit_cost") or 0)
        rows.append({"date": current.isoformat(), "remaining": balance})
        current += timedelta(days=1)
    return pd.DataFrame(rows)


def apollo_credits_page() -> None:
    st.header("Apollo Credits")
    if "credit_success_message" in st.session_state:
        st.success(st.session_state.pop("credit_success_message"))

    month_options = _month_options()
    selected_month = st.selectbox(
        "Credit month",
        month_options,
        index=0,
        format_func=_month_label,
    )
    summary = load_credit_summary(selected_month.isoformat())
    events = load_credit_events(selected_month.isoformat())

    _credit_health_banner(summary)

    cols = st.columns(4)
    with cols[0]:
        metric_card("Base Monthly Credits", int(summary["base_monthly_credits"]))
    with cols[1]:
        metric_card("Top-ups This Month", int(summary["top_ups"]))
    with cols[2]:
        metric_card("Total Available Credits", int(summary["monthly_available"]))
    with cols[3]:
        metric_card("Used This Month", int(summary["monthly_used"]))

    cols = st.columns(4)
    with cols[0]:
        metric_card("Used Today", int(summary["daily_used"]))
    with cols[1]:
        metric_card("Remaining Credits", int(summary["monthly_remaining"]))
    with cols[2]:
        metric_card("Estimated Credits Saved", int(summary["estimated_credits_saved"]))
    with cols[3]:
        metric_card("Average Used Per Day", f"{summary['average_daily_usage']:.1f}")

    st.subheader("Manual Credit Entries")
    left, right = st.columns(2)
    with left:
        with st.form("apollo_top_up_form"):
            st.markdown("**Manual Top-Up**")
            top_up_amount = st.number_input("Top-up amount", min_value=1, step=1, value=500)
            top_up_date = st.date_input("Date of top-up", value=date.today(), key="top_up_date")
            top_up_note = st.text_input("Optional note", value="Manual Apollo credit top-up entered from dashboard")
            submitted = st.form_submit_button("Add top-up")
            if submitted:
                _insert_manual_credit_event("top_up", int(top_up_amount), top_up_date, top_up_note)
                st.cache_data.clear()
                st.session_state["credit_success_message"] = f"Added {int(top_up_amount):,} Apollo top-up credits."
                st.rerun()
    with right:
        with st.form("apollo_adjustment_form"):
            st.markdown("**Manual Adjustment**")
            adjustment_amount = st.number_input("Adjustment amount", step=1, value=0)
            adjustment_date = st.date_input("Adjustment date", value=date.today(), key="adjustment_date")
            adjustment_note = st.text_input("Reason/note", value="Manual correction after Apollo usage mismatch")
            submitted = st.form_submit_button("Add adjustment")
            if submitted:
                if int(adjustment_amount) == 0:
                    st.warning("Enter a positive or negative adjustment amount.")
                else:
                    _insert_manual_credit_event("adjustment", int(adjustment_amount), adjustment_date, adjustment_note)
                    st.cache_data.clear()
                    st.session_state["credit_success_message"] = f"Added {int(adjustment_amount):,} Apollo credit adjustment."
                    st.rerun()

    st.subheader("Credit Forecast")
    forecast_cols = st.columns(4)
    with forecast_cols[0]:
        metric_card("Average Daily Usage", f"{summary['average_daily_usage']:.1f}")
    with forecast_cols[1]:
        metric_card("Projected Monthly Usage", f"{summary['projected_monthly_usage']:.0f}")
    with forecast_cols[2]:
        metric_card("Projected Month-End Remaining", f"{summary['projected_remaining_at_month_end']:.0f}")
    with forecast_cols[3]:
        sustainable = "Yes" if summary["projected_remaining_at_month_end"] > settings.min_apollo_credits_reserve else "No"
        metric_card("Sustainable?", sustainable)

    st.subheader("Usage Charts")
    if events.empty:
        st.info("No Apollo credit events have been logged for this month yet.")
    else:
        chart_events = events.copy()
        chart_events["date"] = pd.to_datetime(chart_events["created_at"]).dt.date.astype(str)
        left, right = st.columns(2)
        daily_usage = chart_events.groupby("date", as_index=False)["credit_cost"].sum()
        with left:
            st.plotly_chart(
                px.bar(daily_usage, x="date", y="credit_cost", title="Daily Apollo Credits Used"),
                use_container_width=True,
            )
        remaining_daily = _remaining_over_time(chart_events, summary)
        with right:
            st.plotly_chart(
                px.line(remaining_daily, x="date", y="remaining", title="Remaining Credits Over Time"),
                use_container_width=True,
            )
        left, right = st.columns(2)
        by_type = chart_events.groupby("event_type", as_index=False)["credit_cost"].sum()
        with left:
            st.plotly_chart(
                px.pie(by_type, values="credit_cost", names="event_type", title="Credits Used by Event Type"),
                use_container_width=True,
            )
        top_ups = chart_events[chart_events["credit_delta"] > 0]
        with right:
            if top_ups.empty:
                st.info("No top-ups were entered for this month.")
            else:
                st.plotly_chart(
                    px.bar(top_ups, x="date", y="credit_delta", color="event_type", title="Top-ups and Positive Adjustments"),
                    use_container_width=True,
                )

    st.subheader("Credit Usage History")
    if events.empty:
        return
    filter_cols = st.columns(3)
    with filter_cols[0]:
        event_filter = st.multiselect("Event type", sorted(events["event_type"].dropna().unique().tolist()))
    with filter_cols[1]:
        source_filter = st.multiselect("Source", sorted(events["source"].dropna().unique().tolist()))
    with filter_cols[2]:
        start_date, end_date = st.date_input(
            "Date range",
            value=(date.fromisoformat(summary["period_start"]), date.fromisoformat(summary["period_end"]) - timedelta(days=1)),
        )

    filtered = events.copy()
    filtered["event_date"] = pd.to_datetime(filtered["created_at"]).dt.date
    if event_filter:
        filtered = filtered[filtered["event_type"].isin(event_filter)]
    if source_filter:
        filtered = filtered[filtered["source"].isin(source_filter)]
    filtered = filtered[(filtered["event_date"] >= start_date) & (filtered["event_date"] <= end_date)]
    filtered["Lead/company"] = (
        filtered["lead_name"].fillna("").astype(str)
        + filtered["company_name"].fillna("").astype(str).apply(lambda value: f" at {value}" if value else "")
    ).str.strip()
    st.dataframe(
        filtered[
            [
                "created_at",
                "event_type",
                "Lead/company",
                "credit_cost",
                "credit_delta",
                "source",
                "description",
            ]
        ].rename(
            columns={
                "created_at": "Date/time",
                "event_type": "Event type",
                "credit_cost": "Credit cost",
                "credit_delta": "Credit delta",
                "source": "Source",
                "description": "Description",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )


def umd_ta_ra_outreach_page() -> None:
    st.header("UMD TA/RA Outreach")
    st.caption("Separate workflow for UMD teaching assistant, research assistant, grader, course support, lab assistant, and faculty assistant outreach.")
    st.info("This workflow uses separate UMD tables and does not touch the main Apollo full-time workflow or the 8:00 AM sender.")

    contacts = load_umd_ta_ra_contacts()
    runs = load_umd_ta_ra_runs()
    logs = load_umd_ta_ra_logs()
    campaigns = load_umd_ta_ra_campaigns()

    latest_run = runs.iloc[0] if not runs.empty else None
    approved_count = int((contacts["status"] == "approved").sum()) if not contacts.empty else 0
    sent_count = int((contacts["status"] == "sent").sum()) if not contacts.empty else 0
    drafted_count = int(contacts["draft_status"].fillna("").isin(["drafted", "approved", "sent"]).sum()) if not contacts.empty else 0
    if not contacts.empty:
        contacts["fit_score"] = contacts["fit_score"].fillna(0).astype(int)
        high_fit_count = int((contacts["fit_bucket"] == "High Fit").sum())
        good_fit_count = int((contacts["fit_bucket"] == "Good Fit").sum())
        medium_fit_count = int((contacts["fit_bucket"] == "Medium Fit").sum())
        low_fit_count = int((contacts["fit_bucket"] == "Low Fit").sum())
        missing_email_count = int((contacts["email"].fillna("").str.len() == 0).sum())
    else:
        high_fit_count = good_fit_count = medium_fit_count = low_fit_count = missing_email_count = 0

    cols = st.columns(6)
    with cols[0]:
        metric_card("Last Run", latest_run["started_at"] if latest_run is not None else "Never")
    with cols[1]:
        metric_card("Contacts Discovered", len(contacts))
    with cols[2]:
        metric_card("High-Fit Contacts", high_fit_count)
    with cols[3]:
        metric_card("Emails Drafted", drafted_count)
    with cols[4]:
        metric_card("Emails Approved", approved_count)
    with cols[5]:
        metric_card("Emails Sent", sent_count)

    summary_cols = st.columns(6)
    with summary_cols[0]:
        metric_card("Good Fit", good_fit_count)
    with summary_cols[1]:
        metric_card("Medium Fit", medium_fit_count)
    with summary_cols[2]:
        metric_card("Low Fit", low_fit_count)
    with summary_cols[3]:
        metric_card("Missing Emails", missing_email_count)
    with summary_cols[4]:
        metric_card("Campaigns", 0 if campaigns.empty else len(campaigns))
    with summary_cols[5]:
        metric_card("Resume", "Ready" if settings.attach_resume else "Off")

    with st.expander("Discovery Controls", expanded=contacts.empty):
        control_cols = st.columns(3)
        with control_cols[0]:
            target_contacts = st.number_input("Target number of contacts", min_value=10, max_value=200, value=settings.umd_ta_ra_target_contacts)
            max_contacts = st.number_input("Max contacts to add", min_value=10, max_value=250, value=settings.umd_ta_ra_max_contacts)
            max_pages = st.number_input("Safe page/search limit", min_value=1, max_value=250, value=min(max(settings.umd_ta_ra_max_pages, settings.umd_ta_ra_target_contacts * 2), 180))
        with control_cols[1]:
            min_discovery_score = st.slider("Fit score threshold", 0, 100, 50)
            search_depth = st.selectbox("Search depth", ["standard", "expanded", "aggressive"], index=1)
        with control_cols[2]:
            department_options = sorted(contacts["department"].dropna().unique().tolist()) if not contacts.empty else []
            selected_departments = st.multiselect("Departments to include", department_options)
            opportunity_options = ["TA", "RA", "Grader", "Course Support", "Faculty Assistant", "General"]
            selected_opportunities = st.multiselect("Opportunity types to include", opportunity_options)
        st.caption("Discovery is read-only against UMD/public search pages and drafts emails for review. It does not send.")
        if st.button("Run UMD TA/RA Discovery", type="primary"):
            with st.spinner("Searching UMD pages and drafting reviewable emails..."):
                try:
                    counts = umd_ta_ra_workflow.run_discovery(
                        settings,
                        max_pages=int(max_pages),
                        target_contacts=int(target_contacts),
                        max_contacts=int(max_contacts),
                        search_depth=search_depth,
                        departments=selected_departments,
                        opportunity_types=selected_opportunities,
                        min_score=int(min_discovery_score),
                    )
                    st.success(f"Discovery finished: {counts}")
                    if int(counts.get("high_fit_contacts", 0)) + int(counts.get("good_fit_contacts", 0)) < 50:
                        st.warning("Fewer than 50 Good/High Fit contacts were found. Try expanded/aggressive search or a lower score threshold.")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as exc:
                    st.error(f"UMD discovery failed: {exc}")

    if contacts.empty:
        st.info("No UMD TA/RA contacts yet. Run discovery to populate this page.")
        return

    st.subheader("Contact Discovery Table")
    filters = st.columns(5)
    with filters[0]:
        department_filter = st.multiselect("Department", sorted(contacts["department"].dropna().unique().tolist()))
    with filters[1]:
        type_filter = st.multiselect("Opportunity type", sorted(contacts["opportunity_type"].dropna().unique().tolist()))
    with filters[2]:
        min_score = st.slider("Fit score threshold", 0, 100, settings.umd_ta_ra_min_fit_score)
    with filters[3]:
        email_filter = st.selectbox("Email availability", ["All", "Has email", "Missing email"])
    with filters[4]:
        semester_filter = st.multiselect("Semester", sorted(contacts["semester"].dropna().unique().tolist()))

    filtered = contacts.copy()
    filtered["fit_score"] = filtered["fit_score"].fillna(0).astype(int)
    if department_filter:
        filtered = filtered[filtered["department"].isin(department_filter)]
    if type_filter:
        filtered = filtered[filtered["opportunity_type"].isin(type_filter)]
    if semester_filter:
        filtered = filtered[filtered["semester"].isin(semester_filter)]
    filtered = filtered[filtered["fit_score"] >= min_score]
    if email_filter == "Has email":
        filtered = filtered[filtered["email"].fillna("").str.len() > 0]
    elif email_filter == "Missing email":
        filtered = filtered[filtered["email"].fillna("").str.len() == 0]

    table = filtered[
        [
            "id",
            "name",
            "title",
            "contact_type",
            "department",
            "email",
            "opportunity_type",
            "semester",
            "personalization_source",
            "personalization_confidence",
            "personalization_context",
            "validation_status",
            "research_or_course_area",
            "fit_score",
            "fit_bucket",
            "source_url",
            "status",
            "last_contacted_at",
            "draft_status",
            "updated_at",
        ]
    ].rename(
        columns={
            "name": "Name",
            "title": "Title",
            "contact_type": "Contact type",
            "department": "Department",
            "email": "Email",
            "opportunity_type": "Opportunity type",
            "semester": "Semester",
            "personalization_source": "Personalization source",
            "personalization_confidence": "Confidence",
            "personalization_context": "Personalization context",
            "validation_status": "Validation",
            "research_or_course_area": "Course or research area",
            "fit_score": "Fit score",
            "fit_bucket": "Fit bucket",
            "source_url": "Source URL",
            "status": "Status",
            "last_contacted_at": "Last contacted date",
            "draft_status": "Draft status",
            "updated_at": "Last updated",
        }
    )
    st.dataframe(table, use_container_width=True, hide_index=True)

    st.subheader("Bulk Draft and Approval")
    selectable_bulk = filtered[filtered["email"].fillna("").str.len() > 0].copy()
    selected_bulk_ids = []
    if selectable_bulk.empty:
        st.info("No filtered contacts with email addresses are available for bulk actions.")
    else:
        bulk_options = selectable_bulk["id"].tolist()
        selected_bulk_ids = st.multiselect(
            "Select contacts for bulk actions",
            bulk_options,
            format_func=lambda value: (
                f"{selectable_bulk.loc[selectable_bulk['id'] == value, 'name'].iloc[0]} | "
                f"{selectable_bulk.loc[selectable_bulk['id'] == value, 'fit_bucket'].iloc[0]} | "
                f"{int(selectable_bulk.loc[selectable_bulk['id'] == value, 'fit_score'].iloc[0])}"
            ),
        )
        bulk_cols = st.columns(4)
        with bulk_cols[0]:
            if st.button("Generate drafts for selected"):
                counts = umd_ta_ra_workflow.bulk_generate_drafts(settings, selected_bulk_ids)
                st.success(f"Draft generation finished: {counts}")
                st.cache_data.clear()
                st.rerun()
        with bulk_cols[1]:
            if st.button("Generate High Fit drafts"):
                high_ids = filtered[filtered["fit_bucket"] == "High Fit"]["id"].tolist()
                counts = umd_ta_ra_workflow.bulk_generate_drafts(settings, high_ids)
                st.success(f"High Fit draft generation finished: {counts}")
                st.cache_data.clear()
                st.rerun()
        with bulk_cols[2]:
            if st.button("Generate High + Good drafts"):
                high_good_ids = filtered[filtered["fit_bucket"].isin(["High Fit", "Good Fit"])]["id"].tolist()
                counts = umd_ta_ra_workflow.bulk_generate_drafts(settings, high_good_ids)
                st.success(f"High + Good draft generation finished: {counts}")
                st.cache_data.clear()
                st.rerun()
        with bulk_cols[3]:
            if st.button("Mark selected skipped"):
                for contact_id in selected_bulk_ids:
                    umd_ta_ra_workflow.mark_contact_status(settings, int(contact_id), "skipped", "Skipped from UMD TA/RA bulk action")
                st.success(f"Skipped {len(selected_bulk_ids)} selected contact(s).")
                st.cache_data.clear()
                st.rerun()

        st.write("Bulk approval")
        approval_threshold = st.slider("Approve drafts with fit score at least", 0, 100, 65)
        reviewed = st.checkbox("I reviewed the selected contacts and approve these drafts for sending.")
        approve_cols = st.columns(2)
        with approve_cols[0]:
            if st.button("Approve selected drafts", disabled=not reviewed):
                counts = umd_ta_ra_workflow.bulk_approve_contacts(settings, selected_bulk_ids, min_score=int(approval_threshold), include_buckets=("High Fit", "Good Fit", "Medium Fit"))
                st.success(f"Bulk approval finished: {counts}")
                st.cache_data.clear()
                st.rerun()
        with approve_cols[1]:
            if st.button("Approve all High + Good drafts", disabled=not reviewed):
                counts = umd_ta_ra_workflow.bulk_approve_contacts(settings, None, min_score=int(approval_threshold), include_buckets=("High Fit", "Good Fit"))
                st.success(f"Bulk approval finished: {counts}")
                st.cache_data.clear()
                st.rerun()

    st.subheader("Email Preview and Review")
    selectable = filtered[filtered["email"].fillna("").str.len() > 0].copy()
    if selectable.empty:
        st.info("No filtered contacts with email addresses are available for draft review.")
    else:
        selected_id = st.selectbox(
            "Select a contact",
            selectable["id"].tolist(),
            format_func=lambda value: (
                f"{selectable.loc[selectable['id'] == value, 'name'].iloc[0]} | "
                f"{selectable.loc[selectable['id'] == value, 'department'].iloc[0]} | "
                f"{int(selectable.loc[selectable['id'] == value, 'fit_score'].iloc[0])}"
            ),
        )
        selected = selectable[selectable["id"] == selected_id].iloc[0]
        st.markdown(f"**Status:** {status_badge(str(selected['status']))}", unsafe_allow_html=True)
        st.caption(f"Resume attachment: {resume_attachment_status_text()}")
        st.caption(f"Source: {selected['source_url']}")
        st.write(f"**Personalization source:** {selected.get('personalization_source') or 'Fallback'}")
        st.write(f"**Personalization confidence:** {selected.get('personalization_confidence') or 'Low'}")
        st.write(f"**Personalization context:** {selected.get('personalization_context') or 'No clean context available'}")
        validation_status = str(selected.get("validation_status") or "Passed")
        validation_issues_raw = selected.get("validation_issues") or "[]"
        try:
            validation_issues = json.loads(validation_issues_raw) if isinstance(validation_issues_raw, str) else []
        except Exception:
            validation_issues = [str(validation_issues_raw)]
        if validation_status == "Passed" and not validation_issues:
            st.success("Validation status: Passed")
        else:
            st.warning("Validation status: Needs Review")
            for issue in validation_issues:
                st.write(f"- {issue}")
        st.write(f"**Why this contact:** {selected['fit_reason']}")
        st.write(f"**Personalization notes:** {selected['personalization_notes']}")

        subject = st.text_input("Subject line", value=str(selected.get("subject") or "MSIS Student Interested in TA/RA or Course Support Opportunities"))
        body = st.text_area("Drafted email body", value=str(selected.get("body") or ""), height=320)
        action_cols = st.columns(4)
        with action_cols[0]:
            if st.button("Save draft edits"):
                status, issues = umd_ta_ra_workflow.update_draft(settings, int(selected_id), subject, body)
                if issues:
                    st.warning(f"Draft saved but needs review: {'; '.join(issues)}")
                else:
                    st.success(f"Draft saved. Validation: {status}.")
                st.cache_data.clear()
                st.rerun()
        with action_cols[1]:
            if st.button("Approve draft"):
                status, issues = umd_ta_ra_workflow.update_draft(settings, int(selected_id), subject, body)
                if issues:
                    st.warning(f"Draft needs review before approval: {'; '.join(issues)}")
                else:
                    approved, approval_issues = umd_ta_ra_workflow.approve_draft(settings, int(selected_id))
                    if approved:
                        st.success("Draft approved. It will not send until you explicitly run the UMD sender.")
                    else:
                        st.warning(f"Draft needs review before approval: {'; '.join(approval_issues)}")
                st.cache_data.clear()
                st.rerun()
        with action_cols[2]:
            if st.button("Regenerate clean draft"):
                status, issues = umd_ta_ra_workflow.regenerate_clean_draft(settings, int(selected_id))
                if issues:
                    st.warning(f"Regenerated draft still needs review: {'; '.join(issues)}")
                else:
                    st.success(f"Regenerated clean draft. Validation: {status}.")
                st.cache_data.clear()
                st.rerun()
        with action_cols[3]:
            if st.button("Skip contact"):
                umd_ta_ra_workflow.mark_contact_status(settings, int(selected_id), "skipped", "Skipped from UMD TA/RA dashboard")
                st.success("Contact skipped.")
                st.cache_data.clear()
                st.rerun()
        status_cols = st.columns(3)
        with status_cols[0]:
            if st.button("Mark follow-up needed"):
                umd_ta_ra_workflow.mark_contact_status(settings, int(selected_id), "follow_up_needed", "Follow-up needed")
                st.success("Marked follow-up needed.")
                st.cache_data.clear()
                st.rerun()
        with status_cols[1]:
            if st.button("Mark contacted"):
                umd_ta_ra_workflow.mark_contact_status(settings, int(selected_id), "contacted", "Contacted outside the automated sender")
                st.success("Marked contacted.")
                st.cache_data.clear()
                st.rerun()
        with status_cols[2]:
            if st.button("Not relevant"):
                umd_ta_ra_workflow.mark_contact_status(settings, int(selected_id), "not_relevant", "Marked not relevant from UMD TA/RA dashboard")
                st.success("Marked not relevant.")
                st.cache_data.clear()
                st.rerun()

    st.subheader("Campaign Bulk Send With Lag")
    st.warning("Campaign sending only uses approved drafts. Dry-run is the default and does not send Gmail messages.")
    campaign_cols = st.columns(4)
    with campaign_cols[0]:
        campaign_name = st.text_input("Campaign name", value="UMD TA/RA Summer/Fall 2026 Outreach")
        semester_target = st.selectbox("Semester target", ["Both", "Summer 2026", "Fall 2026", "General"])
    with campaign_cols[1]:
        min_delay = st.number_input("Minimum delay seconds", min_value=0, max_value=3600, value=settings.umd_ta_ra_min_send_delay_seconds)
        max_delay = st.number_input("Maximum delay seconds", min_value=0, max_value=7200, value=settings.umd_ta_ra_max_send_delay_seconds)
    with campaign_cols[2]:
        daily_limit = st.number_input("Daily send limit", min_value=1, max_value=200, value=settings.umd_ta_ra_default_daily_limit)
        max_campaign_emails = st.number_input("Maximum emails per campaign", min_value=1, max_value=250, value=settings.umd_ta_ra_max_contacts)
    with campaign_cols[3]:
        st.caption(f"Resume attachment: {resume_attachment_status_text()}")
        selected_approved_count = int((filtered["status"] == "approved").sum()) if not filtered.empty else 0
        metric_card("Approved in Filter", selected_approved_count)

    campaign_action_cols = st.columns(4)
    with campaign_action_cols[0]:
        if st.button("Create campaign from selected"):
            campaign_id, counts = umd_ta_ra_workflow.create_campaign(
                settings,
                campaign_name=campaign_name,
                semester_target=semester_target,
                contact_ids=selected_bulk_ids,
                min_score=65,
                max_emails=int(max_campaign_emails),
                min_delay_seconds=int(min_delay),
                max_delay_seconds=int(max_delay),
                daily_send_limit=int(daily_limit),
            )
            st.success(f"Campaign {campaign_id} created: {counts}")
            st.cache_data.clear()
            st.rerun()
    with campaign_action_cols[1]:
        if st.button("Create campaign from all approved High + Good"):
            campaign_id, counts = umd_ta_ra_workflow.create_campaign(
                settings,
                campaign_name=campaign_name,
                semester_target=semester_target,
                min_score=65,
                max_emails=int(max_campaign_emails),
                min_delay_seconds=int(min_delay),
                max_delay_seconds=int(max_delay),
                daily_send_limit=int(daily_limit),
            )
            st.success(f"Campaign {campaign_id} created: {counts}")
            st.cache_data.clear()
            st.rerun()

    campaigns = load_umd_ta_ra_campaigns()
    if campaigns.empty:
        st.info("No UMD campaigns yet. Approve drafts, then create a campaign.")
    else:
        selected_campaign_id = st.selectbox(
            "Campaign",
            campaigns["id"].tolist(),
            format_func=lambda value: (
                f"{campaigns.loc[campaigns['id'] == value, 'campaign_name'].iloc[0]} | "
                f"{campaigns.loc[campaigns['id'] == value, 'status'].iloc[0]} | "
                f"{int(campaigns.loc[campaigns['id'] == value, 'approved_drafts_count'].iloc[0])} approved"
            ),
        )
        campaign_row = campaigns[campaigns["id"] == selected_campaign_id].iloc[0]
        st.markdown(f"**Campaign status:** {status_badge(str(campaign_row['status']))}", unsafe_allow_html=True)
        progress_cols = st.columns(5)
        with progress_cols[0]:
            metric_card("Sent", int(campaign_row["sent_count"]))
        with progress_cols[1]:
            metric_card("Failed", int(campaign_row["failed_count"]))
        with progress_cols[2]:
            metric_card("Skipped", int(campaign_row["skipped_count"]))
        with progress_cols[3]:
            metric_card("Dry Runs", int(campaign_row.get("dry_run_count", 0)))
        with progress_cols[4]:
            metric_card("Remaining", max(int(campaign_row["approved_drafts_count"]) - int(campaign_row["sent_count"]) - int(campaign_row["skipped_count"]), 0))

        send_cols = st.columns(5)
        dry_run_campaign = send_cols[0].checkbox("Dry-run campaign", value=True)
        live_confirm = send_cols[1].text_input('Type "SEND UMD CAMPAIGN" for live')
        with send_cols[2]:
            if st.button("Start Bulk Send"):
                live_allowed = live_confirm == "SEND UMD CAMPAIGN" and not dry_run_campaign
                if live_allowed and not settings.umd_ta_ra_send_enabled:
                    st.error("UMD_TA_RA_SEND_ENABLED is false. Enable it in local .env before live UMD campaign sending.")
                else:
                    with st.spinner("Processing campaign. Live sends wait between emails; dry-run only simulates the schedule."):
                        result = umd_ta_ra_workflow.run_campaign_send(
                            settings,
                            int(selected_campaign_id),
                            dry_run=not live_allowed,
                            min_delay_seconds=int(min_delay),
                            max_delay_seconds=int(max_delay),
                            daily_send_limit=int(daily_limit),
                            max_emails=int(max_campaign_emails),
                            sleep_between=live_allowed,
                        )
                    st.success(f"Campaign run complete: sent={result['sent']}, dry_run={result['dry_run']}, failed={result['failed']}, skipped={result['skipped']}")
                    if result.get("schedule"):
                        st.dataframe(pd.DataFrame(result["schedule"]), use_container_width=True, hide_index=True)
                    st.cache_data.clear()
                    st.rerun()
        with send_cols[3]:
            if st.button("Pause"):
                umd_ta_ra_workflow.set_campaign_status(settings, int(selected_campaign_id), "paused")
                st.success("Campaign paused.")
                st.cache_data.clear()
                st.rerun()
            if st.button("Resume"):
                umd_ta_ra_workflow.set_campaign_status(settings, int(selected_campaign_id), "ready")
                st.success("Campaign resumed.")
                st.cache_data.clear()
                st.rerun()
        with send_cols[4]:
            if st.button("Stop"):
                umd_ta_ra_workflow.set_campaign_status(settings, int(selected_campaign_id), "stopped")
                st.success("Campaign stopped.")
                st.cache_data.clear()
                st.rerun()

        recipients = load_umd_ta_ra_campaign_recipients(int(selected_campaign_id))
        if not recipients.empty:
            st.dataframe(
                recipients[
                    [
                        "name",
                        "email",
                        "department",
                        "fit_score",
                        "fit_bucket",
                        "send_status",
                        "scheduled_send_time",
                        "actual_send_time",
                        "error_message",
                        "retry_count",
                    ]
                ].rename(
                    columns={
                        "name": "Name",
                        "email": "Email",
                        "department": "Department",
                        "fit_score": "Fit score",
                        "fit_bucket": "Fit bucket",
                        "send_status": "Send status",
                        "scheduled_send_time": "Scheduled send time",
                        "actual_send_time": "Actual send time",
                        "error_message": "Error",
                        "retry_count": "Retry count",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    st.subheader("UMD Workflow Runs")
    if runs.empty:
        st.info("No UMD workflow runs yet.")
    else:
        st.dataframe(
            runs[
                [
                    "id",
                    "run_type",
                    "started_at",
                    "completed_at",
                    "status",
                    "pages_searched",
                    "contacts_discovered",
                    "high_fit_contacts",
                    "emails_drafted",
                    "emails_approved",
                    "emails_sent",
                    "duplicates_removed",
                    "missing_emails",
                    "error_summary",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Logs and Errors")
    if logs.empty:
        st.info("No UMD logs yet.")
    else:
        st.dataframe(logs, use_container_width=True, hide_index=True)


setup_page()
st.markdown(
    """
    <div class="workflow-hero">
        <div class="workflow-hero-kicker">InternReach AI</div>
        <div class="workflow-hero-title">Full-Time Job Outreach Dashboard</div>
        <div class="workflow-hero-copy">
            Monitor Apollo discovery, queue quality, Gmail readiness, hybrid Monday sending, manual off-schedule sends, and UMD TA/RA outreach from one operations view.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

page = st.sidebar.radio(
    "Navigation",
    [
        "Overview",
        "Full-Time Review",
        "Credits",
        "Manual Send",
        "UMD TA/RA Outreach",
        "Full-Time Pipeline",
        "Full-Time Lead Table",
        "Errors & Failures",
        "Automation Runs",
        "Full-Time Search Debugger",
    ],
)

with st.spinner("Loading workflow data..."):
    if page == "Overview":
        overview()
    elif page == "Full-Time Review":
        daily_discovery_review()
    elif page == "Credits":
        apollo_credits_page()
    elif page == "Manual Send":
        manual_full_time_sender()
    elif page == "UMD TA/RA Outreach":
        umd_ta_ra_outreach_page()
    elif page == "Full-Time Pipeline":
        lead_pipeline()
    elif page == "Full-Time Lead Table":
        lead_table()
    elif page == "Errors & Failures":
        errors_failures()
    elif page == "Automation Runs":
        automation_runs()
    else:
        apollo_debugger()
