"""Lead qualification scoring before any Apollo enrichment is attempted."""

from __future__ import annotations

import json
import re

from config import Settings
from dmv_location import apply_dmv_location
from lead import Lead


ROLE_KEYWORDS = {
    "recruiter",
    "talent acquisition",
    "technical recruiter",
    "early career recruiter",
    "new grad recruiter",
    "early talent",
    "hr",
    "human resources",
    "hiring manager",
    "founder",
    "co-founder",
    "data analytics manager",
    "analytics manager",
    "business intelligence manager",
    "director of analytics",
    "head of data",
    "data science manager",
    "machine learning manager",
    "ai engineering manager",
    "engineering manager",
    "analytics engineering manager",
    "bi manager",
    "ai manager",
    "ml manager",
    "technical lead",
    "data lead",
    "people operations",
}

HIRING_KEYWORDS = {
    "data analyst",
    "business analyst",
    "bi analyst",
    "product analyst",
    "data engineer",
    "analytics engineer",
    "ai engineer",
    "machine learning engineer",
    "junior data scientist",
    "associate data scientist",
    "data science analyst",
    "cloud data engineer",
    "python sql analyst",
    "entry-level",
    "entry level",
    "new grad",
    "junior",
    "associate",
    "early talent",
    "early career",
    "0-2 years",
    "full-time",
    "full time",
}

INDUSTRY_KEYWORDS = {
    "analytics",
    "artificial intelligence",
    "software",
    "information technology",
    "financial services",
    "consulting",
    "healthcare",
    "research",
    "cloud",
    "data",
    "ml",
    "python",
    "sql",
    "automation",
    "fintech",
    "saas",
    "healthcare tech",
    "product analytics",
    "business intelligence",
}

IRRELEVANT_TITLE_KEYWORDS = {
    "account executive",
    "sales",
    "customer success",
    "marketing",
    "brand",
    "partnerships",
    "revenue",
}

EXCLUDED_KEYWORDS = {
    "intern",
    "internship",
    "unpaid",
    "volunteer",
    "contract only",
    "contract-only",
    "principal",
    "staff engineer",
    "staff data",
    "senior director",
    "vp ",
    "vice president",
    "chief ",
}

SENIOR_ONLY_KEYWORDS = {
    "senior data analyst",
    "senior data engineer",
    "senior machine learning",
    "senior ai engineer",
    "lead data engineer",
    "principal data",
    "staff data",
}


def _text(*values: str) -> str:
    return " ".join(value.lower() for value in values if value)


def _contains_any(text: str, keywords: set[str] | list[str]) -> bool:
    return any(keyword.lower() in text for keyword in keywords if keyword)


def _parse_company_size(raw_size: str) -> int | None:
    if not raw_size:
        return None
    numbers = [int(part.replace(",", "")) for part in re.findall(r"\d[\d,]*", raw_size)]
    if not numbers:
        return None
    return max(numbers)


def disqualifying_reason(lead: Lead) -> str:
    """Return why a lead should not enter the full-time queue, or blank."""

    combined = _text(
        lead.title,
        lead.contact_title,
        lead.role_title,
        lead.reason_for_outreach,
        lead.company_industry,
        lead.raw_json,
    )
    if re.search(r"\bintern(ship)?s?\b", combined):
        return "Internship wording found in a full-time workflow"
    if any(keyword in combined for keyword in {"unpaid", "volunteer"}):
        return "Unpaid or volunteer role signal"
    if any(keyword in combined for keyword in {"contract only", "contract-only"}):
        return "Contract-only role signal"
    if _contains_any(combined, SENIOR_ONLY_KEYWORDS) and not _contains_any(combined, {"junior", "associate", "entry", "new grad", "0-2"}):
        return "Senior-only role signal"
    contact_title_text = _text(lead.title, lead.contact_title)
    if _contains_any(contact_title_text, IRRELEVANT_TITLE_KEYWORDS) and not _contains_any(contact_title_text, ROLE_KEYWORDS):
        return "Irrelevant sales/marketing-only contact title"
    return ""


def score_lead(lead: Lead, settings: Settings) -> tuple[int, dict[str, int | str]]:
    """Score a lead from 0 to 100 using the full-time outreach rubric."""

    location_decision = apply_dmv_location(lead)
    industry_text = _text(lead.company_industry, lead.company_name, lead.reason_for_outreach, lead.raw_json)
    contact_title_text = _text(lead.title, lead.contact_title)
    role_text = _text(lead.role_title, lead.reason_for_outreach, lead.raw_json)
    raw_text = _text(lead.raw_json, lead.reason_for_outreach)
    target_industries = {item.lower() for item in settings.apollo_industries} | INDUSTRY_KEYWORDS
    target_contact_roles = {item.lower() for item in settings.apollo_job_titles} | ROLE_KEYWORDS
    target_hiring = {item.lower() for item in settings.apollo_target_job_titles} | HIRING_KEYWORDS

    location_fit = 15 if location_decision.is_dmv else 0
    if location_decision.remote_dmv_eligible:
        location_fit = 15
    elif "city:" in location_decision.location_match or "state:" in location_decision.location_match:
        location_fit = 15
    elif "us_hub:" in location_decision.location_match:
        location_fit = 13
    elif "apollo_us_full_time_tier:" in location_decision.location_match:
        location_fit = 10

    keyword_fit = 0
    if _contains_any(industry_text, target_industries):
        keyword_fit = 25
    elif _contains_any(industry_text, {"technology", "finance", "bank", "biotech", "engineering"}):
        keyword_fit = 18
    elif lead.company_industry:
        keyword_fit = 10

    role_relevance = 0
    if _contains_any(contact_title_text, target_contact_roles):
        role_relevance = 20
    elif _contains_any(contact_title_text, {"manager", "director", "founder", "co-founder", "people"}):
        role_relevance = 14
    elif lead.title or lead.role_title:
        role_relevance = 6

    size = _parse_company_size(lead.company_size)
    if size is None:
        company_size_fit = 5
    elif 11 <= size <= 5000:
        company_size_fit = 10
    elif 11 <= size <= 50 or 5001 <= size <= 20000:
        company_size_fit = 7
    else:
        company_size_fit = 4

    hiring_signal = 0
    if _contains_any(role_text, target_hiring) or _contains_any(raw_text, target_hiring):
        hiring_signal = 15
    elif _contains_any(raw_text, {"data", "analytics", "machine learning", "ai", "cloud", "consulting"}):
        hiring_signal = 10
    elif _contains_any(contact_title_text, {"recruiter", "talent", "hiring"}):
        hiring_signal = 6

    personalization_quality = 0
    if lead.reason_for_outreach and lead.company_name and lead.company_industry:
        personalization_quality = 5
    elif lead.reason_for_outreach or lead.company_name:
        personalization_quality = 3

    email_quality = 0
    if lead.email and lead.email_status.lower() in {"verified", "likely to engage", "likely valid", "valid"}:
        email_quality = 10
    elif lead.email:
        email_quality = 6

    penalties = 0
    disqualified = disqualifying_reason(lead)
    if disqualified:
        penalties -= 35
    if not location_decision.is_dmv:
        penalties -= 25
    if _contains_any(contact_title_text, IRRELEVANT_TITLE_KEYWORDS) and role_relevance < 14:
        penalties -= 20
    if lead.apollo_used and not lead.email:
        penalties -= 30

    breakdown = {
        "location_fit": location_fit,
        "role_relevance": role_relevance,
        "keyword_fit": keyword_fit,
        "hiring_signal": hiring_signal,
        "company_size_fit": company_size_fit,
        "email_quality": email_quality,
        "personalization_quality": personalization_quality,
        "penalties": penalties,
        "disqualifying_reason": disqualified,
    }
    total = max(0, min(100, sum(int(value) for value in breakdown.values() if isinstance(value, int))))
    breakdown["total"] = total
    breakdown["min_score_to_enrich"] = settings.min_score_to_enrich
    breakdown["min_score_to_send"] = settings.min_score_to_send
    return total, breakdown


def breakdown_json(breakdown: dict[str, int | str]) -> str:
    try:
        return json.dumps(breakdown, sort_keys=True)
    except TypeError:
        return "{}"
