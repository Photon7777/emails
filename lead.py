"""Lead model shared by Apollo, storage, scoring, and email rendering."""

from dataclasses import dataclass
from datetime import datetime
import json
import re
from urllib.parse import urlparse


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def normalize_company_name(name: str) -> str:
    """Create a stable company key for duplicate checks."""

    cleaned = (name or "").strip().lower()
    cleaned = cleaned.replace("&", " and ")
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", cleaned)
    suffixes = {
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "company",
        "co",
        "group",
        "holdings",
        "the",
    }
    parts = [part for part in cleaned.split() if part not in suffixes]
    return " ".join(parts)


def normalize_domain(domain_or_url: str) -> str:
    """Normalize a domain or website URL for company-level dedupe."""

    value = (domain_or_url or "").strip().lower()
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    domain = parsed.netloc or parsed.path
    domain = domain.split("/")[0].split(":")[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def normalize_linkedin_url(url: str) -> str:
    """Normalize LinkedIn URLs enough to catch common duplicates."""

    value = (url or "").strip().lower()
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    path = parsed.path.rstrip("/")
    return f"linkedin.com{path}" if "linkedin.com" in parsed.netloc else value.rstrip("/")


@dataclass
class Lead:
    apollo_id: str = ""
    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    email: str = ""
    title: str = ""
    company_name: str = ""
    company_domain: str = ""
    company_industry: str = ""
    company_size: str = ""
    linkedin_url: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    apollo_url: str = ""
    reason_for_outreach: str = ""
    source: str = "apollo"
    email_source: str = ""
    normalized_company_name: str = ""
    normalized_domain: str = ""
    normalized_linkedin_url: str = ""
    lead_score: int = 0
    score_breakdown: str = "{}"
    apollo_used: bool = False
    apollo_credits_used: int = 0
    last_contacted_date: str = ""
    email_sent: bool = False
    reply_received: bool = False
    bounced: bool = False
    notes: str = ""
    status: str = "pending"
    error_message: str = ""
    raw_json: str = "{}"

    @property
    def email_lower(self) -> str:
        return self.email.strip().lower()

    def refresh_normalized_fields(self) -> None:
        self.normalized_company_name = normalize_company_name(self.company_name)
        self.normalized_domain = normalize_domain(self.company_domain)
        self.normalized_linkedin_url = normalize_linkedin_url(self.linkedin_url)
        if self.email and not self.email_source:
            self.email_source = self.source or "unknown"

    @classmethod
    def from_row(cls, row) -> "Lead":
        def get(name: str, default=""):
            try:
                value = row[name]
            except (IndexError, KeyError):
                return default
            return default if value is None else value

        def get_bool(name: str) -> bool:
            return bool(get(name, 0))

        return cls(
            apollo_id=get("apollo_id"),
            first_name=get("first_name"),
            last_name=get("last_name"),
            full_name=get("full_name"),
            email=get("email"),
            title=get("title"),
            company_name=get("company_name"),
            company_domain=get("company_domain"),
            company_industry=get("company_industry"),
            company_size=get("company_size"),
            linkedin_url=get("linkedin_url"),
            city=get("city"),
            state=get("state"),
            country=get("country"),
            apollo_url=get("apollo_url"),
            reason_for_outreach=get("reason_for_outreach"),
            source=get("source", "apollo") or "apollo",
            email_source=get("email_source"),
            normalized_company_name=get("normalized_company_name"),
            normalized_domain=get("normalized_domain"),
            normalized_linkedin_url=get("normalized_linkedin_url"),
            lead_score=int(get("lead_score", 0) or 0),
            score_breakdown=get("score_breakdown", "{}") or "{}",
            apollo_used=get_bool("apollo_used"),
            apollo_credits_used=int(get("apollo_credits_used", 0) or 0),
            last_contacted_date=get("last_contacted_date"),
            email_sent=get_bool("email_sent"),
            reply_received=get_bool("reply_received"),
            bounced=get_bool("bounced"),
            notes=get("notes"),
            status=get("status", "pending") or "pending",
            error_message=get("error_message"),
            raw_json=get("raw_json", "{}") or "{}",
        )


def raw_to_json(raw: dict) -> str:
    """Store Apollo's raw response for later debugging without breaking SQLite."""

    try:
        return json.dumps(raw, ensure_ascii=True, sort_keys=True)
    except TypeError:
        return "{}"
