"""Streamlit dashboard for the cold email workflow."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from config import load_settings
import db
from email_template import render_email
from lead import Lead, utc_now_iso


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


@st.cache_data(ttl=20)
def read_sql(query: str, params: tuple = ()) -> pd.DataFrame:
    with db.connect(settings.database_path, settings.database_url) as conn:
        db.init_db(conn)
        rows = conn.execute(query, params).fetchall()
        return pd.DataFrame([dict(row) for row in rows])


def metric_card(label: str, value, help_text: str = "") -> None:
    st.metric(label, value, help=help_text)


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


def next_8am_iso() -> str:
    now = datetime.now()
    send_time = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if now >= send_time:
        send_time += timedelta(days=1)
    return send_time.isoformat(timespec="seconds")


def get_lead_row(lead_id: int):
    with db.connect(settings.database_path, settings.database_url) as conn:
        db.init_db(conn)
        return conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()


def mark_manual_skip(lead_id: int, note: str) -> None:
    reason = note.strip() or "Manually skipped in Daily Discovery Review"
    now = utc_now_iso()
    with db.connect(settings.database_path, settings.database_url) as conn:
        db.init_db(conn)
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
    reason = note.strip() or "Removed from 8 AM queue in Daily Discovery Review"
    now = utc_now_iso()
    with db.connect(settings.database_path, settings.database_url) as conn:
        db.init_db(conn)
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
        db.init_db(conn)
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
        "queued": "#0f766e",
        "sent": "#16a34a",
        "rejected": "#dc2626",
        "skipped": "#b45309",
        "failed": "#b91c1c",
        "duplicate": "#6b7280",
        "missing_email": "#9333ea",
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
    if "outside dmv" in reason or "outside" in reason or "non-dmv" in reason:
        return "Outside DMV and not remote"
    if "irrelevant" in reason or "sales" in title:
        return "Irrelevant title"
    if "weekly contact limit" in reason:
        return "Company weekly limit reached"
    if "apollo" in reason and "failed" in reason:
        return "Apollo enrichment failed"
    if "gmail" in reason or "send" in reason or "validation" in reason:
        return "Gmail/send validation failed"
    return "Other"


def setup_page() -> None:
    st.set_page_config(page_title="InternReach AI Dashboard", layout="wide")
    st.markdown(
        """
        <style>
        .main .block-container {padding-top: 1.4rem;}
        div[data-testid="stMetric"] {
            background: #f8fafc;
            border: 1px solid #d8dee9;
            border-radius: 8px;
            padding: 14px 16px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.10);
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
        </style>
        """,
        unsafe_allow_html=True,
    )


def daily_discovery_review() -> None:
    st.header("Daily Discovery Review")
    selected_date = st.date_input("Discovery date", value=date.today())
    latest_run_df = load_latest_discovery_run(selected_date)
    latest_run = latest_run_df.iloc[0] if not latest_run_df.empty else None
    run_id = int(latest_run["id"]) if latest_run is not None else None
    leads = load_daily_review_leads(selected_date, run_id)
    search_logs = load_discovery_search_logs(run_id)

    if settings.dry_run:
        st.warning("DRY_RUN is active. The 8 AM sender will not send live emails.")
    else:
        st.error("Live sending is enabled. Review the queued table carefully before 8:00 AM.")

    if leads.empty:
        st.info("No contacts were found for this date yet. After nightly discovery runs, this page will populate automatically.")
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
        st.warning(f"{len(queued)} contact(s) are queued for live sending at 8:00 AM.")

    rejected = leads[leads["status"].isin(["rejected", "skipped", "failed"]) | (leads["review_status"] == "missing_email")]
    missing_email = int((~leads["has_email"]).sum())
    duplicate_count = int(leads["rejection_reason"].fillna("").str.contains("duplicate", case=False, na=False).sum())
    enriched_count = int((leads["apollo_used"].fillna(0).astype(int) == 1).sum())
    scored_count = int((leads["score"] > 0).sum())
    raw_contacts = int(latest_run["raw_candidates"]) if latest_run is not None else len(leads)

    cols = st.columns(8)
    metrics = [
        ("Raw contacts found", raw_contacts),
        ("Contacts scored", scored_count),
        ("Contacts enriched", enriched_count),
        ("Send-ready contacts", int(leads["status"].isin(["send_ready", "queued"]).sum())),
        ("Queued for 8:00 AM", len(queued)),
        ("Rejected/skipped", len(rejected)),
        ("Missing emails", missing_email),
        ("Duplicate contacts", duplicate_count),
    ]
    for col, (label, value) in zip(cols, metrics):
        with col:
            metric_card(label, value)

    tab_found, tab_queued, tab_rejected, tab_run = st.tabs(
        ["Found Today", "Queued for 8AM", "Rejected/Skipped", "Run Details"]
    )

    with tab_found:
        st.subheader("Contacts Found Today")
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
        st.subheader("Emails Queued for 8:00 AM")
        if queued.empty:
            st.info("No contacts queued for 8:00 AM yet.")
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
                    f"Score {selected.score} met the send threshold of {settings.min_score_to_send}; "
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
    credits = read_sql(
        """
        SELECT COALESCE(SUM(credits), 0) AS credits
        FROM apollo_usage
        WHERE substr(used_at, 1, 10) = ?
        """,
        (date.today().isoformat(),),
    ).iloc[0]["credits"]
    sent_today = int(today_runs["sent_count"])
    remaining_capacity = max(settings.daily_send_limit - sent_today, 0)
    send_ready = int(leads["status"].isin(["send_ready", "queued"]).sum()) if not leads.empty else 0
    failed = int((leads["status"] == "failed").sum()) if not leads.empty else 0

    if settings.dry_run:
        st.warning("DRY_RUN is active. Sender runs will draft/log only and will not send live emails.")
    else:
        st.error("DRY_RUN is disabled. Live sender can send emails if run with live confirmation.")

    cols = st.columns(6)
    with cols[0]:
        metric_card("Discovered Today", int(today_runs["raw_candidates"]))
    with cols[1]:
        metric_card("Enriched Today", int(today_runs["enriched_count"]))
    with cols[2]:
        metric_card("Send-Ready", send_ready)
    with cols[3]:
        metric_card("Sent Today", sent_today)
    with cols[4]:
        metric_card("Failed Sends", failed)
    with cols[5]:
        metric_card("Apollo Credits", f"{int(credits)}/{settings.apollo_daily_credit_limit}")

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
            st.plotly_chart(px.bar(daily, x="day", y="sent", title="Daily Emails Sent"), use_container_width=True)
    with right:
        if leads.empty:
            st.info("No leads in the database yet.")
        else:
            by_status = leads.groupby("status", dropna=False).size().reset_index(name="count")
            st.plotly_chart(px.pie(by_status, values="count", names="status", title="Leads by Status"), use_container_width=True)


def lead_pipeline() -> None:
    leads = load_leads()
    if leads.empty:
        st.info("No leads yet. Run discovery to populate the pipeline.")
        return
    stages = ["raw", "rejected", "enriched", "send_ready", "sent"]
    counts = []
    for status in stages:
        counts.append({"stage": status, "count": int((leads["status"] == status).sum())})
    st.plotly_chart(px.funnel(pd.DataFrame(counts), x="count", y="stage", title="Lead Funnel"), use_container_width=True)

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
        st.info("No leads yet.")
        return
    query = st.text_input("Search leads")
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
        st.info("No Apollo search logs yet. Run discovery to see tier performance.")
        return
    tier_perf = logs.groupby(["tier_name", "search_type"], dropna=False).agg(
        result_count=("result_count", "sum"),
        new_unique_count=("new_unique_count", "sum"),
    ).reset_index()
    st.plotly_chart(px.bar(tier_perf, x="tier_name", y="result_count", color="search_type", title="Apollo Search Tier Performance"), use_container_width=True)
    saved = int((logs["result_count"] - logs["new_unique_count"]).clip(lower=0).sum())
    st.success(f"Estimated Apollo enrichments avoided through dedupe/low-fit filtering: {saved}")
    st.dataframe(logs[["started_at", "tier_name", "search_type", "page", "result_count", "new_unique_count", "notes"]], use_container_width=True, hide_index=True)
    latest = logs.iloc[0]
    with st.expander("Latest Apollo query parameters"):
        st.json(json.loads(latest["params_json"] or "{}"))


setup_page()
st.title("InternReach AI Dashboard")
st.caption("Local monitoring for Apollo discovery, Gmail readiness, and automation health.")

page = st.sidebar.radio(
    "Navigation",
    [
        "Overview",
        "Daily Review",
        "Lead Pipeline",
        "Lead Table",
        "Errors & Failures",
        "Automation Runs",
        "Apollo Search Debugger",
    ],
)

with st.spinner("Loading workflow data..."):
    if page == "Overview":
        overview()
    elif page == "Daily Review":
        daily_discovery_review()
    elif page == "Lead Pipeline":
        lead_pipeline()
    elif page == "Lead Table":
        lead_table()
    elif page == "Errors & Failures":
        errors_failures()
    elif page == "Automation Runs":
        automation_runs()
    else:
        apollo_debugger()
