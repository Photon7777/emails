"""Email template rendering and compliance footer helpers."""

from __future__ import annotations

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
        settings.sender_location,
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
            "risk analytics, reporting automation, forecasting, and data quality workflows",
        ),
        (
            ("health", "medical", "hospital", "clinical", "laboratory", "pharma", "biotech"),
            "healthcare analytics, operational reporting, predictive modeling, and reliable data pipelines",
        ),
        (
            ("software", "information technology", "computer", "internet", "network security", "cybersecurity"),
            "product analytics, ML-enabled workflows, AI applications, and scalable data platforms",
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
    return "analytics, automation, machine learning, and data-informed decision making"


def _role_angle(role: str) -> str:
    normalized = role.lower()
    if any(keyword in normalized for keyword in ("recruit", "talent", "people", "human resources")):
        return "Your recruiting focus made you seem like a good person to ask about data and AI internship opportunities."
    if any(keyword in normalized for keyword in ("data", "analytics", "science", "machine learning", "ml", "ai")):
        return f"Your {role} role stood out because it is close to the kind of analytics, ML, and data engineering work I hope to contribute to."
    if any(keyword in normalized for keyword in ("founder", "co-founder", "owner")):
        return "Your leadership role made you seem like someone who would know where a data-focused intern could be useful."
    if any(keyword in normalized for keyword in ("manager", "director", "head", "lead")):
        return f"Your {role} role made you seem close to teams where data and AI interns could add practical support."
    return "Your role looked connected to hiring or team growth, so I wanted to reach out thoughtfully."


def _company_specific_reason(lead: Lead) -> tuple[str, str]:
    company_name = lead.company_name or "your team"
    industry = lead.company_industry or "your industry"
    role = lead.title or "your role"
    fit_area = _company_fit_area(industry)
    location = ", ".join(part for part in [lead.city, lead.state] if part)

    location_text = f" I also noticed the contact location listed as {location}, which fits my U.S.-focused search." if location else ""
    reason = (
        f"What stood out about {company_name} is the connection between its work in {industry} "
        f"and my background in {fit_area}. {_role_angle(role)}{location_text}"
    )
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
