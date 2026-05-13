"""Separate UMD TA/RA outreach workflow.

This module is intentionally independent from the internship Apollo workflow.
It writes only to umd_ta_ra_* tables and never touches leads, send_queue, or
the 8 AM internship sender.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import json
import logging
import re
import time
from typing import Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests

from config import Settings, validate_resume_attachment, validate_sender_settings
import db
from gmail_client import GmailClient
from lead import utc_now_iso


logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; InternReach-UMD-TA-RA/1.0; +https://sai-praneeth-portfolio.netlify.app/)"
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@(?:umd\.edu|[A-Z0-9.-]+\.umd\.edu)\b", re.IGNORECASE)
HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")

SEARCH_QUERIES = [
    "site:umd.edu teaching assistant",
    "site:umd.edu grader",
    "site:umd.edu undergraduate TA",
    "site:umd.edu graduate TA",
    "site:umd.edu research assistant data analytics",
    "site:umd.edu lab assistant machine learning",
    "site:umd.edu course support data",
    "site:umd.edu faculty directory data analytics",
    "site:umd.edu professor business analytics",
    "site:umd.edu information systems faculty",
    "site:umd.edu machine learning faculty",
    "site:umd.edu marketing analytics faculty",
    "site:umd.edu operations analytics faculty",
    "site:umd.edu data science research lab",
    "site:umd.edu assistantship opportunities",
]

SEED_URLS = [
    "https://www.rhsmith.umd.edu/directory",
    "https://ischool.umd.edu/directory/",
    "https://www.cs.umd.edu/people/faculty",
    "https://ece.umd.edu/clark/faculty",
    "https://www.math.umd.edu/people/faculty.html",
    "https://econ.umd.edu/faculty",
    "https://spp.umd.edu/our-community/faculty-staff",
    "https://biology.umd.edu/research.html",
    "https://ischool.umd.edu/research/",
]

DEPARTMENT_KEYWORDS = {
    "Robert H. Smith School of Business": ("rhsmith", "smith school", "business school", "decision sciences", "information systems", "marketing", "operations"),
    "Information Studies / iSchool": ("ischool", "information studies", "information science"),
    "Computer Science": ("cs.umd", "computer science", "machine learning", "artificial intelligence"),
    "Engineering": ("engineering", "ece.umd", "aerospace", "bioengineering", "mechanical", "electrical"),
    "Public Policy": ("public policy", "spp.umd"),
    "Economics": ("economics", "econ.umd"),
    "Statistics / Mathematics": ("statistics", "mathematics", "math.umd"),
    "Data Science / Analytics": ("data science", "analytics", "business analytics", "database", "ai", "ml"),
}

SKILL_KEYWORDS = (
    "analytics",
    "data",
    "dashboard",
    "python",
    "sql",
    "database",
    "machine learning",
    "artificial intelligence",
    "ai",
    "ml",
    "business process",
    "information systems",
    "operations",
    "marketing analytics",
    "visualization",
    "research",
)
OPPORTUNITY_KEYWORDS = {
    "TA": ("teaching assistant", "graduate assistant", "undergraduate ta", "ta position", "ta"),
    "RA": ("research assistant", "research support", "ra position", "research aide"),
    "Grader": ("grader", "grading assistant"),
    "Course Support": ("course support", "course assistant", "instructional assistant", "lab assistant"),
    "Faculty Assistant": ("faculty assistant", "program coordinator", "academic coordinator"),
}
TITLE_KEYWORDS = ("professor", "lecturer", "instructor", "faculty", "coordinator", "director", "research scientist")


@dataclass
class UmdContact:
    name: str
    email: str
    title: str
    department: str
    source_url: str
    research_or_course_area: str
    opportunity_type: str
    semester: str
    fit_score: int
    fit_reason: str
    personalization_notes: str
    status: str = "discovered"
    raw_text: str = ""

    @property
    def email_lower(self) -> str:
        return self.email.strip().lower()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def _html_to_text(html: str) -> str:
    without_scripts = SCRIPT_STYLE_RE.sub(" ", html or "")
    text = TAG_RE.sub(" ", without_scripts)
    return _clean_text(text)


def _title_from_html(html: str) -> str:
    match = TITLE_RE.search(html or "")
    return _clean_text(match.group(1)) if match else ""


def _request_url(url: str, timeout: int = 20) -> str:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        return ""
    return response.text


def _is_umd_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == "umd.edu" or host.endswith(".umd.edu")


def _normalize_url(url: str, base_url: str = "") -> str:
    value = unescape(url or "").strip()
    if not value:
        return ""
    if value.startswith("/l/?") or "duckduckgo.com/l/?" in value:
        parsed = urlparse(value)
        params = parse_qs(parsed.query)
        if params.get("uddg"):
            value = unquote(params["uddg"][0])
    value = urljoin(base_url, value)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return ""
    cleaned = parsed._replace(fragment="").geturl()
    return cleaned.rstrip("/")


def _extract_links(html: str, base_url: str = "") -> list[str]:
    links = []
    seen = set()
    for raw in HREF_RE.findall(html or ""):
        url = _normalize_url(raw, base_url)
        if not url or not _is_umd_url(url):
            continue
        if any(skip in url.lower() for skip in (".pdf", ".jpg", ".png", ".zip", "calendar")):
            continue
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


def _search_urls(query: str, max_results: int) -> list[str]:
    search_url = "https://duckduckgo.com/html/"
    response = requests.get(search_url, params={"q": query}, headers={"User-Agent": USER_AGENT}, timeout=25)
    response.raise_for_status()
    urls = []
    for url in _extract_links(response.text):
        host = urlparse(url).netloc.lower()
        if host.endswith("duckduckgo.com"):
            continue
        urls.append(url)
        if len(urls) >= max_results:
            break
    return urls


def collect_candidate_urls(settings: Settings, limit: int | None = None) -> tuple[list[str], list[dict]]:
    urls = []
    logs = []
    seen = set()
    for url in SEED_URLS:
        if url not in seen:
            seen.add(url)
            urls.append(url)
        if limit and len(urls) >= limit:
            return urls[:limit], logs
    for query in SEARCH_QUERIES:
        try:
            found = _search_urls(query, settings.umd_ta_ra_search_results_per_query)
            logs.append({"query": query, "result_count": len(found), "status": "success"})
            for url in found:
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
                if limit and len(urls) >= limit:
                    return urls[:limit], logs
        except Exception as exc:
            logs.append({"query": query, "result_count": 0, "status": "failed", "error": str(exc)})
    final_limit = limit or settings.umd_ta_ra_max_pages
    return urls[:final_limit], logs


def _infer_department(url: str, title: str, text: str) -> str:
    haystack = f"{url} {title} {text[:2000]}".lower()
    for department, keywords in DEPARTMENT_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return department
    return "University of Maryland"


def _infer_opportunity_type(text: str) -> str:
    haystack = (text or "").lower()
    for label, keywords in OPPORTUNITY_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return label
    return "General"


def _infer_semester(text: str) -> str:
    haystack = (text or "").lower()
    if "summer 2026" in haystack:
        return "Summer 2026"
    if "fall 2026" in haystack:
        return "Fall 2026"
    if "2026" in haystack:
        return "2026"
    return "General"


def _infer_title(text: str) -> str:
    lower = (text or "").lower()
    if "professor" in lower:
        return "Professor"
    if "lecturer" in lower:
        return "Lecturer"
    if "coordinator" in lower:
        return "Program Coordinator"
    if "director" in lower:
        return "Director"
    if "research scientist" in lower:
        return "Research Scientist"
    if "instructor" in lower:
        return "Instructor"
    return "Faculty/Staff"


def _sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text or "") if sentence.strip()]


def _area_from_text(text: str, title: str) -> str:
    for sentence in _sentences(text):
        lower = sentence.lower()
        if any(keyword in lower for keyword in SKILL_KEYWORDS + tuple(sum(OPPORTUNITY_KEYWORDS.values(), ()))) and len(sentence) <= 220:
            return sentence
    return title[:180] or "UMD course or research support"


def _name_from_email_or_page(email: str, text: str, title: str) -> str:
    if email and text:
        index = text.lower().find(email.lower())
        if index >= 0:
            segment = text[max(0, index - 320):index]
            email_splits = EMAIL_RE.split(segment)
            segment = email_splits[-1] if email_splits else segment
            segment = re.sub(r"\b\d{3}[-.) ]+\d{3}[-. ]+\d{4}\b", " ", segment)
            segment = segment.replace("Contact", " ")
            if "Hall" in segment:
                segment = segment.rsplit("Hall", 1)[-1]
            title_match = re.search(
                r"\b(Associate Dean|Assistant Dean|Dean|Faculty Director|Visiting|Clinical Professor|Assistant Professor|Associate Research Professor|Associate Professor|Professor|Lecturer|Instructor|Director|Coordinator|Research Scientist)\b",
                segment,
            )
            if title_match:
                segment = segment[: title_match.start()]
            cleaned = _clean_text(segment).strip(" ,-–—|")
            words = cleaned.split()
            if 2 <= len(words) <= 6 and not any(char.isdigit() for char in cleaned):
                return cleaned
    if email:
        local = email.split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ")
        if local and not any(char.isdigit() for char in local):
            return " ".join(part.capitalize() for part in local.split() if part)
    candidates = []
    for source in (title, text[:800]):
        for part in re.split(r"[|–—,\n]", source or ""):
            cleaned = _clean_text(part)
            words = cleaned.split()
            if 2 <= len(words) <= 5 and all(word[:1].isupper() for word in words[:2]):
                candidates.append(cleaned)
    return candidates[0] if candidates else "UMD Faculty or Staff"


def score_contact(contact: UmdContact) -> tuple[int, str]:
    text = f"{contact.department} {contact.title} {contact.research_or_course_area} {contact.opportunity_type} {contact.raw_text[:2500]}".lower()
    score = 0
    reasons = []

    skill_hits = [keyword for keyword in SKILL_KEYWORDS if keyword in text]
    if skill_hits:
        score += min(30, 12 + len(skill_hits) * 3)
        reasons.append(f"Matches skills/background: {', '.join(skill_hits[:5])}")

    if contact.opportunity_type != "General":
        score += 20
        reasons.append(f"Mentions {contact.opportunity_type} opportunity")

    if contact.department != "University of Maryland":
        score += 15
        reasons.append(f"Relevant UMD department: {contact.department}")

    if contact.email:
        score += 15
        reasons.append("UMD email available")

    if contact.semester in {"Summer 2026", "Fall 2026", "2026"} or any(token in text for token in ("current", "recent", "2025", "2026")):
        score += 10
        reasons.append("Current/recent or 2026-relevant page")

    if contact.research_or_course_area and contact.research_or_course_area != "UMD course or research support":
        score += 10
        reasons.append("Personalization details available")

    return min(score, 100), "; ".join(reasons) or "General UMD contact with limited fit signals"


def extract_contacts_from_page(url: str, html: str) -> list[UmdContact]:
    title = _title_from_html(html)
    text = _html_to_text(html)
    department = _infer_department(url, title, text)
    opportunity_type = _infer_opportunity_type(text)
    semester = _infer_semester(text)
    area = _area_from_text(text, title)
    title_guess = _infer_title(f"{title} {text[:1200]}")
    emails = sorted({email.lower() for email in EMAIL_RE.findall(html + " " + text)})

    contacts = []
    for email in emails[:12]:
        name = _name_from_email_or_page(email, text, title)
        contact = UmdContact(
            name=name,
            email=email,
            title=title_guess,
            department=department,
            source_url=url,
            research_or_course_area=area,
            opportunity_type=opportunity_type,
            semester=semester,
            fit_score=0,
            fit_reason="",
            personalization_notes="",
            status="discovered",
            raw_text=text[:5000],
        )
        contact.fit_score, contact.fit_reason = score_contact(contact)
        contact.personalization_notes = _personalization_notes(contact)
        contact.status = "drafted" if contact.email and contact.fit_score >= 55 else "discovered"
        contacts.append(contact)

    if not contacts and opportunity_type != "General" and any(keyword in text.lower() for keyword in SKILL_KEYWORDS):
        contact = UmdContact(
            name=_name_from_email_or_page("", text, title),
            email="",
            title=title_guess,
            department=department,
            source_url=url,
            research_or_course_area=area,
            opportunity_type=opportunity_type,
            semester=semester,
            fit_score=0,
            fit_reason="",
            personalization_notes="",
            status="missing_email",
            raw_text=text[:5000],
        )
        contact.fit_score, contact.fit_reason = score_contact(contact)
        contact.personalization_notes = _personalization_notes(contact)
        contacts.append(contact)
    return contacts


def _personalization_notes(contact: UmdContact) -> str:
    if contact.opportunity_type in {"TA", "Grader", "Course Support"}:
        return f"Mention course support interest around {contact.research_or_course_area[:140]}."
    if contact.opportunity_type == "RA":
        return f"Mention research support interest around {contact.research_or_course_area[:140]}."
    if "coordinator" in contact.title.lower() or contact.opportunity_type == "Faculty Assistant":
        return "Ask whether they can direct you to TA, RA, grader, or course support openings."
    return f"Connect your analytics, AI/ML, dashboards, and SQL/Python background to {contact.department}."


def _last_name(name: str) -> str:
    pieces = [piece for piece in re.split(r"\s+", name or "") if piece]
    return pieces[-1] if pieces and name != "UMD Faculty or Staff" else ""


def render_umd_email(contact: UmdContact, settings: Settings) -> tuple[str, str]:
    last = _last_name(contact.name)
    greeting = f"Dear Professor {last}," if last and "professor" in contact.title.lower() else f"Dear {contact.name},"
    if contact.name == "UMD Faculty or Staff":
        greeting = "Hello,"

    if contact.opportunity_type in {"TA", "Grader", "Course Support"}:
        focus = f"course support related to {contact.research_or_course_area}"
    elif contact.opportunity_type == "RA":
        focus = f"research support related to {contact.research_or_course_area}"
    elif "coordinator" in contact.title.lower():
        focus = f"TA, RA, grader, or course support opportunities in {contact.department}"
    else:
        focus = f"TA, RA, grader, or course support opportunities connected to {contact.department}"

    technical = any(keyword in contact.department.lower() for keyword in ("computer", "engineering", "information", "data", "math", "statistics"))
    if technical:
        skills = "analytics, Python, SQL, AI/ML projects, dashboards, and data engineering"
    else:
        skills = "analytics, dashboards, business process analysis, stakeholder communication, and client-facing project work"

    subject = "MSIS Student Interested in TA/RA or Course Support Opportunities"
    body = (
        f"{greeting}\n\n"
        "I hope you are doing well. My name is Sai Praneeth Kathi Moksha, and I am currently pursuing my M.S. in Information Systems at the Robert H. Smith School of Business, University of Maryland, College Park.\n\n"
        f"I wanted to reach out to express my interest in any TA, grader, research assistant, or course support opportunities around {focus} for Summer 2026 or Fall 2026.\n\n"
        f"My background includes {skills}. I believe these experiences would allow me to support students, course activities, research work, and analytical tasks effectively.\n\n"
        "I have attached my resume for reference and would be grateful if you would consider me for any current or future opportunities that may be a good fit.\n\n"
        "Thank you for your time, and I hope to stay connected.\n\n"
        "Best regards,\n"
        f"{settings.sender_name or 'Sai Praneeth Kathi Moksha'}\n"
        f"{settings.sender_email}\n"
        f"{settings.sender_linkedin}\n"
        f"{settings.sender_portfolio}"
    )
    lines = [line.rstrip() for line in body.splitlines()]
    return subject, "\n".join(line for line in lines if line.strip() or line == "")


def _start_run(conn, run_type: str) -> int:
    now = utc_now_iso()
    if db.is_postgres_connection(conn):
        row = conn.execute(
            """
            INSERT INTO umd_ta_ra_workflow_runs (run_type, started_at, status)
            VALUES (?, ?, 'running')
            RETURNING id
            """,
            (run_type, now),
        ).fetchone()
        conn.commit()
        return int(row["id"])
    conn.execute(
        "INSERT INTO umd_ta_ra_workflow_runs (run_type, started_at, status) VALUES (?, ?, 'running')",
        (run_type, now),
    )
    conn.commit()
    return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])


def _log(conn, run_id: int, event_type: str, source_url: str = "", message: str = "") -> None:
    conn.execute(
        """
        INSERT INTO umd_ta_ra_outreach_logs (run_id, event_type, source_url, message, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, event_type, source_url[:1000], message[:2000], utc_now_iso()),
    )
    conn.commit()


def _find_existing_contact(conn, contact: UmdContact):
    if contact.email_lower:
        row = conn.execute(
            "SELECT * FROM umd_ta_ra_contacts WHERE email_lower = ? LIMIT 1",
            (contact.email_lower,),
        ).fetchone()
        if row:
            return row
    return conn.execute(
        """
        SELECT *
        FROM umd_ta_ra_contacts
        WHERE LOWER(name) = LOWER(?)
          AND LOWER(department) = LOWER(?)
          AND source_url = ?
        LIMIT 1
        """,
        (contact.name, contact.department, contact.source_url),
    ).fetchone()


def upsert_contact(conn, contact: UmdContact) -> tuple[int, bool]:
    now = utc_now_iso()
    existing = _find_existing_contact(conn, contact)
    raw_json = json.dumps({"source_url": contact.source_url}, sort_keys=True)
    if existing:
        contact_id = int(existing["id"])
        terminal = (existing["status"] or "") in {"sent", "skipped", "not_relevant", "follow_up_needed"}
        status = existing["status"] if terminal else contact.status
        fit_score = max(int(existing["fit_score"] or 0), int(contact.fit_score or 0))
        conn.execute(
            """
            UPDATE umd_ta_ra_contacts
            SET name = COALESCE(NULLIF(?, ''), name),
                email = COALESCE(NULLIF(?, ''), email),
                email_lower = COALESCE(NULLIF(?, ''), email_lower),
                title = COALESCE(NULLIF(?, ''), title),
                department = COALESCE(NULLIF(?, ''), department),
                research_or_course_area = COALESCE(NULLIF(?, ''), research_or_course_area),
                opportunity_type = COALESCE(NULLIF(?, ''), opportunity_type),
                semester = COALESCE(NULLIF(?, ''), semester),
                fit_score = ?,
                fit_reason = COALESCE(NULLIF(?, ''), fit_reason),
                personalization_notes = COALESCE(NULLIF(?, ''), personalization_notes),
                status = ?,
                updated_at = ?,
                raw_text = COALESCE(NULLIF(?, ''), raw_text),
                raw_json = COALESCE(NULLIF(?, ''), raw_json)
            WHERE id = ?
            """,
            (
                contact.name,
                contact.email,
                contact.email_lower,
                contact.title,
                contact.department,
                contact.research_or_course_area,
                contact.opportunity_type,
                contact.semester,
                fit_score,
                contact.fit_reason,
                contact.personalization_notes,
                status,
                now,
                contact.raw_text,
                raw_json,
                contact_id,
            ),
        )
        conn.commit()
        return contact_id, True

    if not contact.email:
        contact.status = "missing_email"
    params = (
        contact.name,
        contact.email,
        contact.email_lower,
        contact.title,
        contact.department,
        contact.source_url,
        contact.research_or_course_area,
        contact.opportunity_type,
        contact.semester,
        contact.fit_score,
        contact.fit_reason,
        contact.personalization_notes,
        contact.status,
        now,
        now,
        contact.raw_text,
        raw_json,
    )
    if db.is_postgres_connection(conn):
        row = conn.execute(
            """
            INSERT INTO umd_ta_ra_contacts (
                name, email, email_lower, title, department, source_url,
                research_or_course_area, opportunity_type, semester, fit_score,
                fit_reason, personalization_notes, status, discovered_at, updated_at,
                raw_text, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            params,
        ).fetchone()
        contact_id = int(row["id"])
    else:
        conn.execute(
            """
            INSERT INTO umd_ta_ra_contacts (
                name, email, email_lower, title, department, source_url,
                research_or_course_area, opportunity_type, semester, fit_score,
                fit_reason, personalization_notes, status, discovered_at, updated_at,
                raw_text, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        contact_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
    conn.commit()
    return contact_id, False


def create_or_update_draft(conn, contact_id: int, contact: UmdContact, settings: Settings) -> int:
    subject, body = render_umd_email(contact, settings)
    now = utc_now_iso()
    existing = conn.execute(
        "SELECT id, status FROM umd_ta_ra_email_drafts WHERE contact_id = ? LIMIT 1",
        (contact_id,),
    ).fetchone()
    if existing:
        draft_id = int(existing["id"])
        if existing["status"] not in {"approved", "sent"}:
            conn.execute(
                """
                UPDATE umd_ta_ra_email_drafts
                SET subject = ?, body = ?, updated_at = ?, status = 'drafted', error_message = ''
                WHERE id = ?
                """,
                (subject, body, now, draft_id),
            )
    else:
        if db.is_postgres_connection(conn):
            row = conn.execute(
                """
                INSERT INTO umd_ta_ra_email_drafts (contact_id, subject, body, status, created_at, updated_at)
                VALUES (?, ?, ?, 'drafted', ?, ?)
                RETURNING id
                """,
                (contact_id, subject, body, now, now),
            ).fetchone()
            draft_id = int(row["id"])
        else:
            conn.execute(
                """
                INSERT INTO umd_ta_ra_email_drafts (contact_id, subject, body, status, created_at, updated_at)
                VALUES (?, ?, ?, 'drafted', ?, ?)
                """,
                (contact_id, subject, body, now, now),
            )
            draft_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
    conn.execute(
        """
        UPDATE umd_ta_ra_contacts
        SET email_draft_id = ?, status = CASE WHEN status NOT IN ('approved', 'sent', 'skipped', 'not_relevant') THEN 'drafted' ELSE status END, updated_at = ?
        WHERE id = ?
        """,
        (draft_id, now, contact_id),
    )
    conn.commit()
    return draft_id


def run_discovery(settings: Settings, max_pages: int | None = None) -> dict[str, int]:
    """Search UMD pages, store contacts, and draft reviewable emails.

    This does not send email. It only writes to umd_ta_ra_* tables.
    """

    counts = {
        "pages_searched": 0,
        "contacts_discovered": 0,
        "high_fit_contacts": 0,
        "emails_drafted": 0,
        "duplicates_removed": 0,
        "missing_emails": 0,
        "failed_scrapes": 0,
    }
    with db.connect(settings.database_path, settings.database_url) as conn:
        db.init_db(conn)
        run_id = _start_run(conn, "discovery")
        try:
            urls, search_logs = collect_candidate_urls(settings, limit=max_pages or settings.umd_ta_ra_max_pages)
            for item in search_logs:
                _log(conn, run_id, "search", "", json.dumps(item, sort_keys=True))

            for url in urls:
                time.sleep(max(settings.umd_ta_ra_request_delay_seconds, 0))
                try:
                    html = _request_url(url)
                    if not html:
                        _log(conn, run_id, "skip", url, "Non-HTML page skipped")
                        continue
                    counts["pages_searched"] += 1
                    page_contacts = extract_contacts_from_page(url, html)
                    if not page_contacts:
                        _log(conn, run_id, "no_contacts", url, "No relevant UMD contact found on page")
                    for contact in page_contacts:
                        if contact.fit_score < settings.umd_ta_ra_min_fit_score and contact.opportunity_type == "General":
                            _log(conn, run_id, "low_fit", url, f"Skipped low-fit contact candidate: {contact.fit_score}")
                            continue
                        contact_id, was_duplicate = upsert_contact(conn, contact)
                        if was_duplicate:
                            counts["duplicates_removed"] += 1
                        else:
                            counts["contacts_discovered"] += 1
                        if contact.fit_score >= settings.umd_ta_ra_high_fit_score:
                            counts["high_fit_contacts"] += 1
                        if not contact.email:
                            counts["missing_emails"] += 1
                        elif contact.fit_score >= settings.umd_ta_ra_min_fit_score:
                            create_or_update_draft(conn, contact_id, contact, settings)
                            counts["emails_drafted"] += 1
                except Exception as exc:
                    counts["failed_scrapes"] += 1
                    _log(conn, run_id, "scrape_failed", url, str(exc))
                    logger.warning("UMD TA/RA scrape failed for %s: %s", url, exc)

            conn.execute(
                """
                UPDATE umd_ta_ra_workflow_runs
                SET completed_at = ?, status = 'success', pages_searched = ?,
                    contacts_discovered = ?, high_fit_contacts = ?, emails_drafted = ?,
                    duplicates_removed = ?, missing_emails = ?, details_json = ?
                WHERE id = ?
                """,
                (
                    utc_now_iso(),
                    counts["pages_searched"],
                    counts["contacts_discovered"],
                    counts["high_fit_contacts"],
                    counts["emails_drafted"],
                    counts["duplicates_removed"],
                    counts["missing_emails"],
                    json.dumps({"search_logs": search_logs}, sort_keys=True),
                    run_id,
                ),
            )
            conn.commit()
        except Exception as exc:
            conn.execute(
                """
                UPDATE umd_ta_ra_workflow_runs
                SET completed_at = ?, status = 'failed', error_summary = ?
                WHERE id = ?
                """,
                (utc_now_iso(), str(exc)[:2000], run_id),
            )
            conn.commit()
            raise
    return counts


def approve_draft(settings: Settings, contact_id: int) -> None:
    now = utc_now_iso()
    with db.connect(settings.database_path, settings.database_url) as conn:
        db.init_db(conn)
        conn.execute(
            """
            UPDATE umd_ta_ra_email_drafts
            SET status = 'approved', approved_at = ?, updated_at = ?
            WHERE contact_id = ?
            """,
            (now, now, contact_id),
        )
        conn.execute(
            """
            UPDATE umd_ta_ra_contacts
            SET status = 'approved', updated_at = ?
            WHERE id = ?
            """,
            (now, contact_id),
        )
        conn.commit()


def update_draft(settings: Settings, contact_id: int, subject: str, body: str) -> None:
    now = utc_now_iso()
    with db.connect(settings.database_path, settings.database_url) as conn:
        db.init_db(conn)
        conn.execute(
            """
            UPDATE umd_ta_ra_email_drafts
            SET subject = ?, body = ?, status = 'drafted', updated_at = ?
            WHERE contact_id = ?
            """,
            (subject[:500], body, now, contact_id),
        )
        conn.execute(
            "UPDATE umd_ta_ra_contacts SET status = 'drafted', updated_at = ? WHERE id = ?",
            (now, contact_id),
        )
        conn.commit()


def mark_contact_status(settings: Settings, contact_id: int, status: str, note: str = "") -> None:
    now = utc_now_iso()
    with db.connect(settings.database_path, settings.database_url) as conn:
        db.init_db(conn)
        conn.execute(
            """
            UPDATE umd_ta_ra_contacts
            SET status = ?,
                personalization_notes = COALESCE(NULLIF(?, ''), personalization_notes),
                last_contacted_at = CASE WHEN ? = 'contacted' THEN ? ELSE last_contacted_at END,
                updated_at = ?
            WHERE id = ?
            """,
            (status, note[:1000], status, now, now, contact_id),
        )
        if status in {"skipped", "not_relevant"}:
            conn.execute(
                "UPDATE umd_ta_ra_email_drafts SET status = ?, updated_at = ? WHERE contact_id = ? AND status != 'sent'",
                (status, now, contact_id),
            )
        conn.commit()


def send_approved_drafts(settings: Settings, limit: int = 5, dry_run: bool = True) -> dict[str, int]:
    """Send approved UMD TA/RA drafts only when explicitly enabled.

    This sender is separate from the internship morning sender.
    """

    counts = {"sent": 0, "failed": 0, "dry_run": 0}
    validate_sender_settings(settings)
    validate_resume_attachment(settings)
    gmail = None if dry_run else GmailClient(settings)
    attachment_paths = [settings.resume_file] if settings.attach_resume else []

    with db.connect(settings.database_path, settings.database_url) as conn:
        db.init_db(conn)
        run_id = _start_run(conn, "sender")
        rows = conn.execute(
            """
            SELECT c.*, d.id AS draft_id, d.subject, d.body
            FROM umd_ta_ra_email_drafts d
            JOIN umd_ta_ra_contacts c ON c.id = d.contact_id
            WHERE d.status = 'approved'
              AND c.status = 'approved'
              AND c.email_lower IS NOT NULL
              AND c.email_lower != ''
            ORDER BY c.fit_score DESC, d.approved_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            if dry_run or not settings.umd_ta_ra_send_enabled:
                _log(conn, run_id, "dry_run", row["source_url"], f"Would send UMD TA/RA email to {row['email']}")
                counts["dry_run"] += 1
                continue
            try:
                message_id = gmail.send_email(row["email"], row["subject"], row["body"], attachment_paths=attachment_paths)
                now = utc_now_iso()
                conn.execute(
                    """
                    UPDATE umd_ta_ra_email_drafts
                    SET status = 'sent', sent_at = ?, gmail_message_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, message_id, now, row["draft_id"]),
                )
                conn.execute(
                    """
                    UPDATE umd_ta_ra_contacts
                    SET status = 'sent', last_contacted_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, row["id"]),
                )
                counts["sent"] += 1
            except Exception as exc:
                now = utc_now_iso()
                conn.execute(
                    """
                    UPDATE umd_ta_ra_email_drafts
                    SET status = 'failed', error_message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (str(exc)[:1000], now, row["draft_id"]),
                )
                counts["failed"] += 1
        conn.execute(
            """
            UPDATE umd_ta_ra_workflow_runs
            SET completed_at = ?, status = 'success', emails_sent = ?, details_json = ?
            WHERE id = ?
            """,
            (utc_now_iso(), counts["sent"], json.dumps(counts, sort_keys=True), run_id),
        )
        conn.commit()
    return counts
