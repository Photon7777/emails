"""Email template rendering and compliance footer helpers."""

from __future__ import annotations

import re

from config import Settings
from lead import Lead


class SafeDict(dict):
    """Return a blank string for missing template variables."""

    def __missing__(self, key):
        return ""


def _identity_block(settings: Settings) -> str:
    lines = [
        settings.sender_name,
        settings.sender_role,
        settings.sender_email,
        settings.sender_linkedin,
        settings.sender_portfolio,
        settings.sender_physical_address,
    ]
    return "\n".join(line for line in lines if line)


def _company_fit_area(industry: str) -> str:
    normalized = industry.lower()
    rules = [
        (
            ("financial", "insurance", "bank", "accounting", "fintech"),
            "risk analytics, reporting, forecasting, and data quality",
        ),
        (
            ("health", "medical", "hospital", "clinical", "laboratory", "pharma", "biotech"),
            "healthcare analytics, reporting, modeling, and data pipelines",
        ),
        (
            ("software", "information technology", "computer", "internet", "network security", "cybersecurity"),
            "product analytics, AI workflows, and data platforms",
        ),
        (
            ("staffing", "recruiting", "human resources"),
            "talent analytics, funnel reporting, process automation, and recruiting operations data",
        ),
        (
            ("telecommunications", "utilities", "energy"),
            "usage analytics, forecasting, operations dashboards, and data pipeline reliability",
        ),
        (
            ("manufacturing", "electrical", "construction", "automotive", "industrial"),
            "operations analytics, quality dashboards, forecasting, and process improvement",
        ),
        (
            ("government", "transportation", "public"),
            "public-sector reporting, operational analytics, and data-driven service improvement",
        ),
        (
            ("real estate", "hospitality", "travel", "marketplace"),
            "marketplace analytics, customer behavior analysis, forecasting, and dashboarding",
        ),
    ]

    for keywords, fit_area in rules:
        if any(keyword in normalized for keyword in keywords):
            return fit_area
    return "analytics, automation, ML, and data workflows"


def _role_angle(role: str) -> str:
    normalized = role.lower()
    readable_role = role if len(role) <= 55 else ""
    if any(keyword in normalized for keyword in ("recruit", "talent", "people", "human resources")):
        return "Your recruiting focus made you a good person to ask about data and AI roles."
    if any(keyword in normalized for keyword in ("data", "analytics", "science", "machine learning", "ml", "ai")):
        if readable_role:
            return f"Your {readable_role} role looked close to the work I hope to support."
        return "Your work looked close to the data and AI work I hope to support."
    if any(keyword in normalized for keyword in ("founder", "co-founder", "owner")):
        return "Your leadership role made you seem close to team growth and hiring."
    if any(keyword in normalized for keyword in ("manager", "director", "head", "lead")):
        return "Your role made you seem close to teams where data and AI skills could help."
    return "Your role looked connected to hiring or team growth, so I wanted to reach out thoughtfully."


def _company_specific_reason(lead: Lead) -> tuple[str, str]:
    company_name = lead.company_name or "your team"
    industry = lead.company_industry or "your industry"
    role = lead.title or "your role"
    fit_area = _company_fit_area(industry)
    location = ", ".join(part for part in [lead.city, lead.state] if part)

    if lead.remote_dmv_eligible:
        location_text = " The remote signal also fit my search."
    elif location:
        location_text = f" I also noticed the U.S. location listed as {location}."
    else:
        location_text = ""
    industry_text = industry if industry and industry != "your industry" else "data-relevant work"
    reason = f"{company_name}'s work in {industry_text} connects with my background in {fit_area}. {_role_angle(role)}{location_text}"
    return reason, fit_area


def _context_for(lead: Lead, settings: Settings) -> SafeDict:
    first_name = lead.first_name or "there"
    company_name = lead.company_name or "your team"
    industry = lead.company_industry or "your industry"
    role = lead.title or "your role"
    reason = lead.reason_for_outreach or "Your team looked relevant to data-focused work."
    company_specific_reason, company_fit_area = _company_specific_reason(lead)

    return SafeDict(
        first_name=first_name,
        last_name=lead.last_name,
        full_name=lead.full_name or first_name,
        email=lead.email,
        role=role,
        title=role,
        company_name=company_name,
        company_domain=lead.company_domain,
        industry=industry,
        company_industry=industry,
        company_size=lead.company_size,
        reason_for_outreach=reason,
        company_specific_reason=company_specific_reason,
        company_fit_area=company_fit_area,
        sender_name=settings.sender_name,
        sender_email=settings.sender_email,
        sender_role=settings.sender_role,
        sender_location=settings.sender_location,
        sender_linkedin=settings.sender_linkedin,
        sender_portfolio=settings.sender_portfolio,
        sender_background=settings.sender_background,
    )


def render_email(lead: Lead, settings: Settings) -> tuple[str, str]:
    """Render subject and body for one lead."""

    template_text = settings.email_template_path.read_text(encoding="utf-8")
    context = _context_for(lead, settings)

    subject = settings.email_subject.format_map(context).strip()
    body = template_text.format_map(context).strip()

    footer_parts = []
    identity = _identity_block(settings)
    if identity:
        footer_parts.append(identity)
    if settings.unsubscribe_text:
        footer_parts.append(settings.unsubscribe_text)

    if footer_parts:
        body = body + "\n\n" + "\n\n".join(footer_parts)

    return subject, body


def validate_full_time_email(subject: str, body: str) -> list[str]:
    """Return safety issues that should keep a full-time email from sending."""

    issues = []
    combined = f"{subject}\n{body}".lower()
    if re.search(r"\bintern(ship)?s?\b", combined):
        issues.append("Email contains internship wording.")
    if "unsubscribe" in combined:
        issues.append("Email contains unsubscribe wording, which is disabled for this workflow.")
    if _word_count(body) > 180:
        issues.append("Email is longer than the 180-word target.")
    return issues


def _word_count(value: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+", value or ""))
