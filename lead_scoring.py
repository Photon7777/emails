"""Lead qualification scoring before any Apollo enrichment is attempted."""

from __future__ import annotations

import json
import re

from config import Settings
from lead import Lead


ROLE_KEYWORDS = {
    "recruiter",
    "talent acquisition",
    "university recruiter",
    "campus recruiter",
    "early talent",
    "hr",
    "human resources",
    "hiring manager",
    "data analytics manager",
    "analytics manager",
    "business intelligence manager",
    "director of analytics",
    "head of data",
    "data science manager",
    "machine learning manager",
    "ai engineering manager",
}

HIRING_KEYWORDS = {
    "data analyst intern",
    "business analyst intern",
    "analytics intern",
    "data science intern",
    "ai engineer intern",
    "machine learning intern",
    "cloud intern",
    "consulting intern",
    "early talent",
    "campus",
    "internship",
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


def score_lead(lead: Lead, settings: Settings) -> tuple[int, dict[str, int | str]]:
    """Score a lead from 0 to 100 before paid enrichment.

    Breakdown:
    - Industry fit: 0-25
    - Role relevance: 0-25
    - Location fit: 0-15
    - Company size fit: 0-10
    - Hiring signal: 0-15
    - Contact quality: 0-10
    """

    industry_text = _text(lead.company_industry, lead.company_name, lead.reason_for_outreach)
    title_text = _text(lead.title)
    location_text = _text(lead.city, lead.state, lead.country)
    raw_text = _text(lead.raw_json, lead.reason_for_outreach)
    target_industries = {item.lower() for item in settings.apollo_industries} | INDUSTRY_KEYWORDS
    target_roles = {item.lower() for item in settings.apollo_job_titles} | ROLE_KEYWORDS
    target_hiring = {item.lower() for item in settings.apollo_target_job_titles} | HIRING_KEYWORDS

    industry_fit = 0
    if _contains_any(industry_text, target_industries):
        industry_fit = 25
    elif _contains_any(industry_text, {"technology", "finance", "bank", "biotech", "engineering"}):
        industry_fit = 18
    elif lead.company_industry:
        industry_fit = 10

    role_relevance = 0
    if _contains_any(title_text, target_roles):
        role_relevance = 25
    elif _contains_any(title_text, {"manager", "director", "founder", "co-founder", "people"}):
        role_relevance = 16
    elif lead.title:
        role_relevance = 8

    location_fit = 0
    if lead.country.lower() in {"united states", "united states of america", "usa", "us"}:
        location_fit = 15
    elif not lead.country and "united states" in {item.lower() for item in settings.apollo_person_locations}:
        location_fit = 10
    elif _contains_any(location_text, {"united states", "usa"}):
        location_fit = 12

    size = _parse_company_size(lead.company_size)
    if size is None:
        company_size_fit = 4
    elif 51 <= size <= 5000:
        company_size_fit = 10
    elif 11 <= size <= 50 or 5001 <= size <= 20000:
        company_size_fit = 7
    else:
        company_size_fit = 4

    hiring_signal = 0
    if _contains_any(raw_text, target_hiring):
        hiring_signal = 15
    elif _contains_any(raw_text, {"data", "analytics", "machine learning", "ai", "cloud", "consulting"}):
        hiring_signal = 10
    elif _contains_any(title_text, {"recruiter", "talent", "hiring"}):
        hiring_signal = 6

    contact_quality = 0
    if lead.full_name or lead.first_name:
        contact_quality += 3
    if lead.linkedin_url:
        contact_quality += 2
    if lead.email:
        contact_quality += 3
    if role_relevance >= 16:
        contact_quality += 2
    contact_quality = min(contact_quality, 10)

    breakdown = {
        "industry_fit": industry_fit,
        "role_relevance": role_relevance,
        "location_fit": location_fit,
        "company_size_fit": company_size_fit,
        "hiring_signal": hiring_signal,
        "contact_quality": contact_quality,
    }
    total = sum(int(value) for value in breakdown.values())
    breakdown["total"] = total
    breakdown["threshold"] = settings.lead_score_threshold
    return total, breakdown


def breakdown_json(breakdown: dict[str, int | str]) -> str:
    try:
        return json.dumps(breakdown, sort_keys=True)
    except TypeError:
        return "{}"
