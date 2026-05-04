"""Lead model shared by Apollo, storage, and email rendering."""

from dataclasses import dataclass
from datetime import datetime
import json


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


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
    status: str = "pending"
    error_message: str = ""
    raw_json: str = "{}"

    @property
    def email_lower(self) -> str:
        return self.email.strip().lower()

    @classmethod
    def from_row(cls, row) -> "Lead":
        return cls(
            apollo_id=row["apollo_id"] or "",
            first_name=row["first_name"] or "",
            last_name=row["last_name"] or "",
            full_name=row["full_name"] or "",
            email=row["email"] or "",
            title=row["title"] or "",
            company_name=row["company_name"] or "",
            company_domain=row["company_domain"] or "",
            company_industry=row["company_industry"] or "",
            company_size=row["company_size"] or "",
            linkedin_url=row["linkedin_url"] or "",
            city=row["city"] or "",
            state=row["state"] or "",
            country=row["country"] or "",
            apollo_url=row["apollo_url"] or "",
            reason_for_outreach=row["reason_for_outreach"] or "",
            source=row["source"] or "apollo",
            status=row["status"] or "pending",
            error_message=row["error_message"] or "",
            raw_json=row["raw_json"] or "{}",
        )


def raw_to_json(raw: dict) -> str:
    """Store Apollo's raw response for later debugging without breaking SQLite."""

    try:
        return json.dumps(raw, ensure_ascii=True, sort_keys=True)
    except TypeError:
        return "{}"
