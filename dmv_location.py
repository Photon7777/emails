"""DMV-only internship location filtering helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re

from lead import Lead


DMV_STATE_ALIASES = {
    "dc": "District of Columbia",
    "d.c.": "District of Columbia",
    "district of columbia": "District of Columbia",
    "washington dc": "District of Columbia",
    "washington, dc": "District of Columbia",
    "md": "Maryland",
    "maryland": "Maryland",
    "va": "Virginia",
    "virginia": "Virginia",
}

DMV_CITY_ALIASES = {
    "washington": "Washington, DC",
    "washington dc": "Washington, DC",
    "washington, dc": "Washington, DC",
    "arlington": "Arlington, VA",
    "alexandria": "Alexandria, VA",
    "fairfax": "Fairfax, VA",
    "mclean": "McLean, VA",
    "mc lean": "McLean, VA",
    "tysons": "Tysons, VA",
    "tysons corner": "Tysons, VA",
    "reston": "Reston, VA",
    "rockville": "Rockville, MD",
    "bethesda": "Bethesda, MD",
    "college park": "College Park, MD",
    "silver spring": "Silver Spring, MD",
    "baltimore": "Baltimore, MD",
    "gaithersburg": "Gaithersburg, MD",
    "richmond": "Richmond, VA",
}

REMOTE_TERMS = {
    "remote",
    "remote - us",
    "remote us",
    "remote, us",
    "remote united states",
    "united states remote",
    "remote us only",
    "work from home",
}

DMV_SEARCH_TIERS = {
    "tier_1_strict_dmv_remote",
    "tier_2_dmv_broader_roles",
}

REMOTE_SEARCH_TIERS = {
    "tier_3_remote_us_internships",
}


@dataclass(frozen=True)
class LocationDecision:
    is_dmv: bool
    remote_dmv_eligible: bool
    location_match: str
    internship_type: str


def normalize_location_text(*values: str) -> str:
    text = " ".join(value for value in values if value)
    text = text.lower().replace("&nbsp;", " ")
    text = text.replace("d.c.", "dc").replace("d.c", "dc")
    text = re.sub(r"[^a-z0-9,./ -]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def classify_internship_type(*values: str) -> str:
    text = normalize_location_text(*values)
    if "summer 2026" in text or "2026 summer" in text:
        return "summer_2026"
    if "summer" in text:
        return "summer_unspecified"
    if any(term in text for term in {"fall", "spring", "winter", "off cycle", "off-cycle"}):
        return "off_cycle"
    if "intern" in text:
        return "internship_unspecified"
    return ""


def evaluate_dmv_location(lead: Lead) -> LocationDecision:
    """Return whether a lead is DMV-targetable before enrichment or sending."""

    text = normalize_location_text(
        lead.city,
        lead.state,
        lead.country,
        lead.role_title,
        lead.reason_for_outreach,
        lead.raw_json,
    )
    city = normalize_location_text(lead.city)
    state = normalize_location_text(lead.state)
    country = normalize_location_text(lead.country)
    source_tier = " ".join(value for value in (lead.search_tier, lead.source_tier) if value).lower()
    internship_type = classify_internship_type(lead.role_title, lead.raw_json)

    if any(term in text for term in REMOTE_TERMS):
        if not country or country in {"united states", "united states of america", "usa", "us"}:
            return LocationDecision(True, True, "remote_dmv_eligible", internship_type or "remote")

    if country and country not in {"united states", "united states of america", "usa", "us"}:
        return LocationDecision(False, False, "outside_us", internship_type)

    if state in DMV_STATE_ALIASES:
        return LocationDecision(True, False, f"state:{DMV_STATE_ALIASES[state]}", internship_type)

    if city in DMV_CITY_ALIASES and not (
        city == "washington"
        and state
        and state not in {"dc", "district of columbia", "washington dc", "washington, dc"}
    ):
        return LocationDecision(True, False, f"city:{DMV_CITY_ALIASES[city]}", internship_type)

    for phrase, label in DMV_CITY_ALIASES.items():
        if phrase == "washington":
            continue
        if phrase in text:
            return LocationDecision(True, False, f"city:{label}", internship_type)

    for phrase, label in DMV_STATE_ALIASES.items():
        if phrase in {"dc", "md", "va"}:
            continue
        if phrase in text:
            return LocationDecision(True, False, f"state:{label}", internship_type)

    if not city and not state:
        if any(tier in source_tier for tier in DMV_SEARCH_TIERS):
            return LocationDecision(True, False, f"apollo_dmv_search_tier:{lead.search_tier or lead.source_tier}", internship_type)
        if any(tier in source_tier for tier in REMOTE_SEARCH_TIERS):
            return LocationDecision(True, True, f"apollo_remote_search_tier:{lead.search_tier or lead.source_tier}", internship_type or "remote")

    if not city and not state and not any(term in text for term in REMOTE_TERMS):
        return LocationDecision(False, False, "missing_location", internship_type)

    return LocationDecision(False, False, "outside_dmv", internship_type)


def apply_dmv_location(lead: Lead) -> LocationDecision:
    decision = evaluate_dmv_location(lead)
    lead.is_dmv = decision.is_dmv
    lead.remote_dmv_eligible = decision.remote_dmv_eligible
    lead.location_match = decision.location_match
    if decision.internship_type:
        lead.internship_type = decision.internship_type
    return decision
