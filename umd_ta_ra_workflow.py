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
ANY_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}\b")
OFFICE_RE = re.compile(
    r"\b(?:room|rm\.?|office|suite)\s*[A-Z]?\d{2,5}[A-Z]?\b|"
    r"\b\d{3,5}\s+(?:Van Munching|Hornbake|Iribe|A\.?\s*V\.?\s*Williams|Kim|Glenn L\.?\s*Martin|"
    r"Tydings|Kirwan|Marie Mount|Morrill|Symons|Skinner|McKeldin|Tawes|Woods|LeFrak|"
    r"Chincoteague|Susquehanna|Computer Science Instructional Center|CSI|Engineering|Physics|"
    r"Biology[- ]Psychology)[\w\s.-]*(?:Hall|Building|Bldg|Center)?\b",
    re.IGNORECASE,
)
BUILDING_WORD_RE = re.compile(r"\b(?:Hall|Building|Bldg|Center|Room|Office|Suite|Van Munching|Hornbake|Iribe|Tydings|Kirwan)\b", re.IGNORECASE)
HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
LABEL_RE = re.compile(r"\b(?:Contact|Phone|Email|Office|Fax|Location|Address|Room|Website)\b\s*:?", re.IGNORECASE)
DIRECTORY_DUMP_RE = re.compile(
    r"professor\s+of\s+management\s+science\s+decision|"
    r"professor\s+email\s+office\s+phone|"
    r"\bcontact\b.*\b(phone|email|office)\b",
    re.IGNORECASE,
)

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
PERSON_TITLE_RE = re.compile(
    r"\b(?:Tyser\s+)?(?:Assistant|Associate|Clinical|Visiting|Adjunct|Full)?\s*"
    r"(?:Professor|Lecturer|Instructor|Faculty Director|Director|Coordinator|Research Scientist)"
    r"(?:\s+of\s+[A-Z][A-Za-z,&\s-]{2,80})?",
    re.IGNORECASE,
)
FIELD_STOP_RE = re.compile(
    r"\b(?:Contact|Phone|Email|Office|Fax|Location|Address|Biography|Education|Publications|"
    r"Research|Teaching|Courses|Appointments|Awards)\b",
    re.IGNORECASE,
)
RESEARCH_LABEL_RE = re.compile(
    r"\b(?:research interests?|areas? of interest|research areas?|expertise|specialties|topics?)\b\s*:?\s*",
    re.IGNORECASE,
)
COURSE_LABEL_RE = re.compile(r"\b(?:courses taught|teaching|courses?)\b\s*:?\s*", re.IGNORECASE)


@dataclass
class UmdContact:
    name: str
    email: str
    title: str
    department: str
    phone: str
    office: str
    research_interests: str
    courses_taught: str
    lab_name: str
    profile_url: str
    source_url: str
    research_or_course_area: str
    opportunity_type: str
    semester: str
    fit_score: int
    fit_reason: str
    personalization_notes: str
    personalization_context: str
    personalization_source: str
    personalization_confidence: str
    status: str = "discovered"
    raw_text: str = ""

    @property
    def email_lower(self) -> str:
        return self.email.strip().lower()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def _word_count(value: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+", value or ""))


def _dedupe_words_phrase(value: str) -> str:
    pieces = []
    seen = set()
    for part in re.split(r"\s*(?:,|;|\||/)\s*", value or ""):
        cleaned = _clean_text(part).strip(" .,-")
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            pieces.append(cleaned)
    return ", ".join(pieces)


def clean_personalization_text(raw_text: str, *, max_words: int = 15, allow_long_clean: bool = False) -> str | None:
    """Return email-safe personalization text or None.

    UMD pages often render full faculty cards as one text blob. This function
    rejects those blobs instead of letting directory metadata appear in an email.
    """

    if not raw_text:
        return None
    original = _clean_text(raw_text)
    if not original:
        return None
    original_metadata_hits = sum(
        1
        for pattern in (ANY_EMAIL_RE, PHONE_RE, OFFICE_RE, LABEL_RE)
        if pattern.search(original)
    )
    if original_metadata_hits >= 2 and _word_count(original) > 8:
        return None

    text = TAG_RE.sub(" ", original)
    text = ANY_EMAIL_RE.sub(" ", text)
    text = PHONE_RE.sub(" ", text)
    text = OFFICE_RE.sub(" ", text)
    text = re.sub(r"https?://\S+", " ", text, flags=re.IGNORECASE)
    text = LABEL_RE.sub(" ", text)
    text = PERSON_TITLE_RE.sub(" ", text)
    text = re.sub(r"\b(?:Tel|Telephone|Mail|Website)\b\s*:?", " ", text, flags=re.IGNORECASE)
    text = _clean_text(text).strip(" .,-:;|")
    text = _dedupe_words_phrase(text)

    if not text:
        return None
    if ANY_EMAIL_RE.search(text) or PHONE_RE.search(text) or OFFICE_RE.search(text):
        return None
    if BUILDING_WORD_RE.search(text):
        return None
    if DIRECTORY_DUMP_RE.search(original) or DIRECTORY_DUMP_RE.search(text):
        return None
    if re.search(r"\b\d{3,5}\b", text):
        return None
    metadata_hits = len(re.findall(r"\b(?:professor|contact|phone|email|office|room|hall|building)\b", text, re.IGNORECASE))
    if metadata_hits:
        return None
    word_limit = max_words if not allow_long_clean else max(max_words, 22)
    if _word_count(text) > word_limit:
        return None
    if len(re.split(r"\s+", text)) <= 1 and len(text) < 6:
        return None
    return text


def _department_phrase(department: str) -> str | None:
    clean_department = clean_personalization_text(department, max_words=12, allow_long_clean=True)
    if not clean_department or clean_department.lower() == "university of maryland":
        return None
    if any(word in clean_department.lower() for word in ("school", "college", "institute", "program")):
        return f"the {clean_department}"
    if clean_department.lower().endswith("department"):
        return f"the {clean_department}"
    return f"the {clean_department} department"


def _extract_after_label(text: str, label_re: re.Pattern, *, max_words: int = 15) -> str:
    match = label_re.search(text or "")
    if not match:
        return ""
    fragment = text[match.end() : match.end() + 260]
    fragment = FIELD_STOP_RE.split(fragment, maxsplit=1)[0]
    fragment = re.split(r"(?<=[.!?])\s+", fragment, maxsplit=1)[0]
    return clean_personalization_text(fragment, max_words=max_words, allow_long_clean=True) or ""


def _keyword_context(text: str) -> str:
    lower_text = (text or "").lower()
    priority_phrases = [
        "social impact",
        "operations analytics",
        "business analytics",
        "data-driven decision-making",
        "data driven decision making",
        "machine learning",
        "artificial intelligence",
        "information systems",
        "marketing analytics",
        "operations management",
        "management science",
        "business technology",
        "data science",
    ]
    for phrase in priority_phrases:
        if phrase in lower_text:
            return phrase
    for sentence in _sentences(text):
        cleaned = clean_personalization_text(sentence, max_words=15, allow_long_clean=True)
        if cleaned and any(keyword in cleaned.lower() for keyword in SKILL_KEYWORDS):
            return cleaned
    return ""


def _extract_phone(text: str) -> str:
    match = PHONE_RE.search(text or "")
    return match.group(0) if match else ""


def _extract_office(text: str) -> str:
    match = OFFICE_RE.search(text or "")
    return _clean_text(match.group(0)) if match else ""


def _extract_research_interests(text: str) -> str:
    labeled = _extract_after_label(text, RESEARCH_LABEL_RE, max_words=18)
    if labeled:
        return labeled
    return clean_personalization_text(_keyword_context(text), max_words=15, allow_long_clean=True) or ""


def _extract_courses_taught(text: str) -> str:
    return _extract_after_label(text, COURSE_LABEL_RE, max_words=16)


def _extract_lab_name(title: str, text: str) -> str:
    combined = f"{title} {text[:1500]}"
    match = re.search(r"\b([A-Z][A-Za-z& -]{2,80}\s+(?:Lab|Laboratory|Center|Group))\b", combined)
    if not match:
        return ""
    return clean_personalization_text(match.group(1), max_words=10, allow_long_clean=True) or ""


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


def _title_near_email(email: str, text: str, fallback: str) -> str:
    if not email or not text:
        return fallback
    index = text.lower().find(email.lower())
    segment = text[max(0, index - 360) : min(len(text), index + 180)] if index >= 0 else text[:1200]
    patterns = [
        r"\bTyser Professor of Management Science\b",
        r"\b(?:Assistant|Associate|Clinical|Visiting|Adjunct|Full)\s+Professor\b",
        r"\bProfessor\b",
        r"\bLecturer\b",
        r"\bInstructor\b",
        r"\bResearch Scientist\b",
        r"\bProgram Coordinator\b",
        r"\b(?:Faculty )?Director\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, segment, re.IGNORECASE)
        if match:
            return _clean_text(match.group(0)).title().replace("Of", "of")
    return fallback


def _sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text or "") if sentence.strip()]


def _area_from_text(text: str, title: str) -> str:
    course = _extract_courses_taught(text)
    if course:
        return course
    research = _extract_research_interests(text)
    if research:
        return research
    for sentence in _sentences(text):
        lower = sentence.lower()
        if any(keyword in lower for keyword in SKILL_KEYWORDS + tuple(sum(OPPORTUNITY_KEYWORDS.values(), ()))) and len(sentence) <= 220:
            cleaned = clean_personalization_text(sentence, max_words=15, allow_long_clean=True)
            if cleaned:
                return cleaned
    return clean_personalization_text(title, max_words=12, allow_long_clean=True) or "UMD course or research support"


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
    text = (
        f"{contact.department} {contact.title} {contact.research_interests} "
        f"{contact.courses_taught} {contact.lab_name} {contact.research_or_course_area} "
        f"{contact.opportunity_type} {contact.raw_text[:2500]}"
    ).lower()
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
    phone = _extract_phone(text)
    office = _extract_office(text)
    research_interests = _extract_research_interests(text)
    courses_taught = _extract_courses_taught(text)
    lab_name = _extract_lab_name(title, text)
    emails = sorted({email.lower() for email in EMAIL_RE.findall(html + " " + text)})

    contacts = []
    for email in emails[:12]:
        name = _name_from_email_or_page(email, text, title)
        contact_title = _title_near_email(email, text, title_guess)
        contact = UmdContact(
            name=name,
            email=email,
            title=contact_title,
            department=department,
            phone=phone,
            office=office,
            research_interests=research_interests,
            courses_taught=courses_taught,
            lab_name=lab_name,
            profile_url=url,
            source_url=url,
            research_or_course_area=area,
            opportunity_type=opportunity_type,
            semester=semester,
            fit_score=0,
            fit_reason="",
            personalization_notes="",
            personalization_context="",
            personalization_source="Fallback",
            personalization_confidence="Low",
            status="discovered",
            raw_text=text[:5000],
        )
        contact.fit_score, contact.fit_reason = score_contact(contact)
        _apply_personalization(contact)
        contact.personalization_notes = _personalization_notes(contact)
        contact.status = "drafted" if contact.email and contact.fit_score >= 55 else "discovered"
        contacts.append(contact)

    if not contacts and opportunity_type != "General" and any(keyword in text.lower() for keyword in SKILL_KEYWORDS):
        contact = UmdContact(
            name=_name_from_email_or_page("", text, title),
            email="",
            title=title_guess,
            department=department,
            phone=phone,
            office=office,
            research_interests=research_interests,
            courses_taught=courses_taught,
            lab_name=lab_name,
            profile_url=url,
            source_url=url,
            research_or_course_area=area,
            opportunity_type=opportunity_type,
            semester=semester,
            fit_score=0,
            fit_reason="",
            personalization_notes="",
            personalization_context="",
            personalization_source="Fallback",
            personalization_confidence="Low",
            status="missing_email",
            raw_text=text[:5000],
        )
        contact.fit_score, contact.fit_reason = score_contact(contact)
        _apply_personalization(contact)
        contact.personalization_notes = _personalization_notes(contact)
        contacts.append(contact)
    return contacts


def _personalization_notes(contact: UmdContact) -> str:
    source = contact.personalization_source or "Fallback"
    context = contact.personalization_context or ""
    if contact.opportunity_type in {"TA", "Grader", "Course Support"}:
        return f"Use {source.lower()} personalization for course support interest: {context or 'general UMD support'}."
    if contact.opportunity_type == "RA":
        return f"Use {source.lower()} personalization for research support interest: {context or 'general UMD research support'}."
    if "coordinator" in contact.title.lower() or contact.opportunity_type == "Faculty Assistant":
        return "Ask whether they can direct you to TA, RA, grader, or course support openings."
    return f"Connect your analytics, AI/ML, dashboards, and SQL/Python background to {context or contact.department}."


def select_best_personalization(contact: UmdContact) -> tuple[str, str, str]:
    """Choose clean personalization text in course -> research -> department order."""

    course = clean_personalization_text(contact.courses_taught, max_words=16, allow_long_clean=True)
    if course:
        return course, "Course", "High"

    research_candidates = [contact.research_interests, contact.lab_name]
    for raw in research_candidates:
        research = clean_personalization_text(raw, max_words=16, allow_long_clean=True)
        if research:
            return research, "Research", "High" if raw == contact.research_interests else "Medium"

    department = _department_phrase(contact.department)
    if department:
        return department, "Department", "Medium"

    return "", "Fallback", "Low"


def _apply_personalization(contact: UmdContact) -> None:
    text, source, confidence = select_best_personalization(contact)
    contact.personalization_context = text
    contact.personalization_source = source
    contact.personalization_confidence = confidence


def _last_name(name: str) -> str:
    pieces = [piece for piece in re.split(r"\s+", name or "") if piece]
    return pieces[-1] if pieces and name != "UMD Faculty or Staff" else ""


def render_umd_email(contact: UmdContact, settings: Settings) -> tuple[str, str]:
    _apply_personalization(contact)
    last = _last_name(contact.name)
    is_professor = "professor" in (contact.title or "").lower()
    greeting = f"Dear Professor {last}," if last and is_professor else f"Dear {contact.name},"
    if contact.name == "UMD Faculty or Staff":
        greeting = "Hello,"

    context = contact.personalization_context
    source = contact.personalization_source
    if source == "Course" and context:
        interest_sentence = (
            "I wanted to reach out to express my interest in any TA, grader, research assistant, "
            f"or course support opportunities related to your course, {context}, for Summer 2026 or Fall 2026."
        )
    elif source == "Research" and context:
        interest_sentence = (
            "I wanted to reach out to express my interest in any TA, grader, research assistant, "
            f"or course support opportunities related to your research in {context} for Summer 2026 or Fall 2026."
        )
    elif source == "Department" and context:
        interest_sentence = (
            "I wanted to reach out to express my interest in any TA, grader, research assistant, "
            f"or course support opportunities within {context} for Summer 2026 or Fall 2026."
        )
    else:
        interest_sentence = (
            "I wanted to reach out to express my interest in any TA, grader, research assistant, "
            "or course support opportunities for Summer 2026 or Fall 2026."
        )

    technical = any(keyword in contact.department.lower() for keyword in ("computer", "engineering", "information", "data", "math", "statistics"))
    if technical:
        skills = "analytics, Python, SQL, AI/ML projects, dashboards, and data engineering"
    else:
        skills = "analytics, dashboards, business process analysis, stakeholder communication, and client-facing project work"

    subject = "MSIS Student Interested in TA/RA or Course Support Opportunities"
    body = (
        f"{greeting}\n\n"
        "I hope you are doing well. My name is Sai Praneeth Kathi Moksha, and I am currently pursuing my M.S. in Information Systems at the Robert H. Smith School of Business, University of Maryland, College Park.\n\n"
        f"{interest_sentence}\n\n"
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


def _extract_personalization_phrase(body: str) -> str:
    match = re.search(
        r"opportunities\s+(?:related to|within)\s+(.+?)\s+for Summer 2026 or Fall 2026",
        body or "",
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    return _clean_text(match.group(1))


def validate_umd_draft(subject: str, body: str, settings: Settings | None = None) -> tuple[str, list[str]]:
    """Validate UMD drafts before approval or sending.

    Validation is intentionally conservative because these messages go to
    university faculty and staff. Failing drafts are left reviewable, not sent.
    """

    issues = []
    body_for_email_check = body or ""
    if settings and settings.sender_email:
        body_for_email_check = body_for_email_check.replace(settings.sender_email, "")
    if PHONE_RE.search(body_for_email_check):
        issues.append("Body contains a phone number.")
    if ANY_EMAIL_RE.search(body_for_email_check):
        issues.append("Body contains an email address outside the sender signature.")
    if OFFICE_RE.search(body_for_email_check) or BUILDING_WORD_RE.search(_extract_personalization_phrase(body_for_email_check)):
        issues.append("Body contains office, room, address, or building metadata.")
    if re.search(r"\bContact\b", body_for_email_check):
        issues.append('Body contains directory label "Contact".')
    if DIRECTORY_DUMP_RE.search(body_for_email_check):
        issues.append("Body contains a phrase that looks like copied faculty directory metadata.")

    personalization_phrase = _extract_personalization_phrase(body_for_email_check)
    if personalization_phrase:
        if _word_count(personalization_phrase) > 20:
            issues.append("Personalization phrase is longer than 20 words.")
        if not clean_personalization_text(personalization_phrase, max_words=20, allow_long_clean=True):
            issues.append("Personalization phrase is not clean enough for outreach.")
    for line in (body or "").splitlines():
        lowered = line.lower()
        metadata_hits = sum(1 for token in ("contact", "phone", "email", "office", "room", "hall", "building") if token in lowered)
        if metadata_hits >= 2:
            issues.append("A body line looks like copied directory metadata.")
            break

    deduped = []
    seen = set()
    for issue in issues:
        if issue not in seen:
            seen.add(issue)
            deduped.append(issue)
    return ("Needs Review" if deduped else "Passed"), deduped


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
    _apply_personalization(contact)
    existing = _find_existing_contact(conn, contact)
    raw_json = json.dumps(
        {
            "source_url": contact.source_url,
            "profile_url": contact.profile_url,
            "personalization_source": contact.personalization_source,
            "personalization_confidence": contact.personalization_confidence,
        },
        sort_keys=True,
    )
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
                phone = COALESCE(NULLIF(?, ''), phone),
                office = COALESCE(NULLIF(?, ''), office),
                research_interests = COALESCE(NULLIF(?, ''), research_interests),
                courses_taught = COALESCE(NULLIF(?, ''), courses_taught),
                lab_name = COALESCE(NULLIF(?, ''), lab_name),
                profile_url = COALESCE(NULLIF(?, ''), profile_url),
                research_or_course_area = COALESCE(NULLIF(?, ''), research_or_course_area),
                opportunity_type = COALESCE(NULLIF(?, ''), opportunity_type),
                semester = COALESCE(NULLIF(?, ''), semester),
                fit_score = ?,
                fit_reason = COALESCE(NULLIF(?, ''), fit_reason),
                personalization_notes = COALESCE(NULLIF(?, ''), personalization_notes),
                personalization_context = COALESCE(NULLIF(?, ''), personalization_context),
                personalization_source = COALESCE(NULLIF(?, ''), personalization_source),
                personalization_confidence = COALESCE(NULLIF(?, ''), personalization_confidence),
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
                contact.phone,
                contact.office,
                contact.research_interests,
                contact.courses_taught,
                contact.lab_name,
                contact.profile_url,
                contact.research_or_course_area,
                contact.opportunity_type,
                contact.semester,
                fit_score,
                contact.fit_reason,
                contact.personalization_notes,
                contact.personalization_context,
                contact.personalization_source,
                contact.personalization_confidence,
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
        contact.phone,
        contact.office,
        contact.research_interests,
        contact.courses_taught,
        contact.lab_name,
        contact.profile_url,
        contact.source_url,
        contact.research_or_course_area,
        contact.opportunity_type,
        contact.semester,
        contact.fit_score,
        contact.fit_reason,
        contact.personalization_notes,
        contact.personalization_context,
        contact.personalization_source,
        contact.personalization_confidence,
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
                name, email, email_lower, title, department,
                phone, office, research_interests, courses_taught, lab_name, profile_url, source_url,
                research_or_course_area, opportunity_type, semester, fit_score,
                fit_reason, personalization_notes, personalization_context,
                personalization_source, personalization_confidence, status, discovered_at, updated_at,
                raw_text, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            params,
        ).fetchone()
        contact_id = int(row["id"])
    else:
        conn.execute(
            """
            INSERT INTO umd_ta_ra_contacts (
                name, email, email_lower, title, department,
                phone, office, research_interests, courses_taught, lab_name, profile_url, source_url,
                research_or_course_area, opportunity_type, semester, fit_score,
                fit_reason, personalization_notes, personalization_context,
                personalization_source, personalization_confidence, status, discovered_at, updated_at,
                raw_text, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        contact_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
    conn.commit()
    return contact_id, False


def create_or_update_draft(conn, contact_id: int, contact: UmdContact, settings: Settings) -> int:
    subject, body = render_umd_email(contact, settings)
    validation_status, validation_issues = validate_umd_draft(subject, body, settings)
    draft_status = "needs_review" if validation_issues else "drafted"
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
                SET subject = ?, body = ?, updated_at = ?, status = ?,
                    error_message = ?, validation_status = ?, validation_issues = ?
                WHERE id = ?
                """,
                (
                    subject,
                    body,
                    now,
                    draft_status,
                    "; ".join(validation_issues),
                    validation_status,
                    json.dumps(validation_issues),
                    draft_id,
                ),
            )
    else:
        if db.is_postgres_connection(conn):
            row = conn.execute(
                """
                INSERT INTO umd_ta_ra_email_drafts (
                    contact_id, subject, body, status, created_at, updated_at,
                    error_message, validation_status, validation_issues
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    contact_id,
                    subject,
                    body,
                    draft_status,
                    now,
                    now,
                    "; ".join(validation_issues),
                    validation_status,
                    json.dumps(validation_issues),
                ),
            ).fetchone()
            draft_id = int(row["id"])
        else:
            conn.execute(
                """
                INSERT INTO umd_ta_ra_email_drafts (
                    contact_id, subject, body, status, created_at, updated_at,
                    error_message, validation_status, validation_issues
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contact_id,
                    subject,
                    body,
                    draft_status,
                    now,
                    now,
                    "; ".join(validation_issues),
                    validation_status,
                    json.dumps(validation_issues),
                ),
            )
            draft_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
    conn.execute(
        """
        UPDATE umd_ta_ra_contacts
        SET email_draft_id = ?,
            status = CASE WHEN status NOT IN ('approved', 'sent', 'skipped', 'not_relevant')
                THEN ? ELSE status END,
            personalization_context = ?,
            personalization_source = ?,
            personalization_confidence = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            draft_id,
            draft_status,
            contact.personalization_context,
            contact.personalization_source,
            contact.personalization_confidence,
            now,
            contact_id,
        ),
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


def _contact_from_row(row) -> UmdContact:
    contact = UmdContact(
        name=row["name"] or "UMD Faculty or Staff",
        email=row["email"] or "",
        title=row["title"] or "Faculty/Staff",
        department=row["department"] or "University of Maryland",
        phone=row["phone"] or "",
        office=row["office"] or "",
        research_interests=row["research_interests"] or "",
        courses_taught=row["courses_taught"] or "",
        lab_name=row["lab_name"] or "",
        profile_url=row["profile_url"] or row["source_url"] or "",
        source_url=row["source_url"] or "",
        research_or_course_area=row["research_or_course_area"] or "",
        opportunity_type=row["opportunity_type"] or "General",
        semester=row["semester"] or "General",
        fit_score=int(row["fit_score"] or 0),
        fit_reason=row["fit_reason"] or "",
        personalization_notes=row["personalization_notes"] or "",
        personalization_context=row["personalization_context"] or "",
        personalization_source=row["personalization_source"] or "Fallback",
        personalization_confidence=row["personalization_confidence"] or "Low",
        status=row["status"] or "discovered",
        raw_text=row["raw_text"] or "",
    )
    _apply_personalization(contact)
    return contact


def regenerate_clean_draft(settings: Settings, contact_id: int) -> tuple[str, list[str]]:
    with db.connect(settings.database_path, settings.database_url) as conn:
        db.init_db(conn)
        row = conn.execute("SELECT * FROM umd_ta_ra_contacts WHERE id = ? LIMIT 1", (contact_id,)).fetchone()
        if not row:
            raise ValueError(f"UMD contact {contact_id} was not found.")
        contact = _contact_from_row(row)
        create_or_update_draft(conn, contact_id, contact, settings)
        draft = conn.execute(
            "SELECT validation_status, validation_issues FROM umd_ta_ra_email_drafts WHERE contact_id = ? LIMIT 1",
            (contact_id,),
        ).fetchone()
        issues = json.loads(draft["validation_issues"] or "[]") if draft else []
        return (draft["validation_status"] if draft else "Needs Review"), issues


def approve_draft(settings: Settings, contact_id: int) -> tuple[bool, list[str]]:
    now = utc_now_iso()
    with db.connect(settings.database_path, settings.database_url) as conn:
        db.init_db(conn)
        row = conn.execute(
            """
            SELECT d.subject, d.body
            FROM umd_ta_ra_email_drafts d
            WHERE d.contact_id = ?
            LIMIT 1
            """,
            (contact_id,),
        ).fetchone()
        if not row:
            raise ValueError("No draft exists for this contact.")
        validation_status, validation_issues = validate_umd_draft(row["subject"], row["body"], settings)
        if validation_issues:
            conn.execute(
                """
                UPDATE umd_ta_ra_email_drafts
                SET status = 'needs_review', validation_status = ?, validation_issues = ?,
                    error_message = ?, updated_at = ?
                WHERE contact_id = ?
                """,
                (
                    validation_status,
                    json.dumps(validation_issues),
                    "; ".join(validation_issues),
                    now,
                    contact_id,
                ),
            )
            conn.execute(
                "UPDATE umd_ta_ra_contacts SET status = 'needs_review', updated_at = ? WHERE id = ?",
                (now, contact_id),
            )
            conn.commit()
            return False, validation_issues
        conn.execute(
            """
            UPDATE umd_ta_ra_email_drafts
            SET status = 'approved', approved_at = ?, updated_at = ?,
                validation_status = 'Passed', validation_issues = '[]', error_message = ''
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
    return True, []


def update_draft(settings: Settings, contact_id: int, subject: str, body: str) -> tuple[str, list[str]]:
    now = utc_now_iso()
    validation_status, validation_issues = validate_umd_draft(subject, body, settings)
    draft_status = "needs_review" if validation_issues else "drafted"
    with db.connect(settings.database_path, settings.database_url) as conn:
        db.init_db(conn)
        conn.execute(
            """
            UPDATE umd_ta_ra_email_drafts
            SET subject = ?, body = ?, status = ?, updated_at = ?,
                validation_status = ?, validation_issues = ?, error_message = ?
            WHERE contact_id = ?
            """,
            (
                subject[:500],
                body,
                draft_status,
                now,
                validation_status,
                json.dumps(validation_issues),
                "; ".join(validation_issues),
                contact_id,
            ),
        )
        conn.execute("UPDATE umd_ta_ra_contacts SET status = ?, updated_at = ? WHERE id = ?", (draft_status, now, contact_id))
        conn.commit()
    return validation_status, validation_issues


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
            validation_status, validation_issues = validate_umd_draft(row["subject"], row["body"], settings)
            if validation_issues:
                now = utc_now_iso()
                conn.execute(
                    """
                    UPDATE umd_ta_ra_email_drafts
                    SET status = 'needs_review', validation_status = ?, validation_issues = ?,
                        error_message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        validation_status,
                        json.dumps(validation_issues),
                        "; ".join(validation_issues),
                        now,
                        row["draft_id"],
                    ),
                )
                conn.execute(
                    "UPDATE umd_ta_ra_contacts SET status = 'needs_review', updated_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                _log(conn, run_id, "validation_failed", row["source_url"], "; ".join(validation_issues))
                counts["failed"] += 1
                continue
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
