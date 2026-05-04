"""Apollo API client for searching and enriching leads."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import requests
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import Settings, validate_apollo_settings
from lead import Lead, raw_to_json


logger = logging.getLogger(__name__)


class TransientApiError(RuntimeError):
    """Raised for rate-limit and server errors that should be retried."""


def _first_nonempty(*values) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _add_list_param(params: dict, key: str, values: list[str]) -> None:
    if values:
        params[key] = values


def _domain_from_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.replace("www.", "")


class ApolloClient:
    def __init__(self, settings: Settings):
        validate_apollo_settings(settings)
        self.settings = settings
        self.session = requests.Session()

    def _headers(self) -> dict[str, str]:
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
        }
        if self.settings.apollo_auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {self.settings.apollo_api_key}"
        else:
            headers["X-Api-Key"] = self.settings.apollo_api_key
        return headers

    def _request(self, method: str, url: str, **kwargs) -> dict:
        for attempt in Retrying(
            retry=retry_if_exception_type((requests.RequestException, TransientApiError)),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            stop=stop_after_attempt(self.settings.max_retries),
            reraise=True,
        ):
            with attempt:
                return self._request_once(method, url, **kwargs)
        return {}

    def _request_once(self, method: str, url: str, **kwargs) -> dict:
        response = self.session.request(
            method,
            url,
            headers=self._headers(),
            timeout=30,
            **kwargs,
        )

        if response.status_code in {429, 500, 502, 503, 504}:
            raise TransientApiError(
                f"Apollo returned {response.status_code}: {response.text[:300]}"
            )

        if response.status_code >= 400:
            raise RuntimeError(
                f"Apollo returned {response.status_code}: {response.text[:500]}"
            )

        if not response.text:
            return {}
        return response.json()

    def test_api_key(self) -> dict:
        """Call Apollo's auth health endpoint to confirm the key is accepted."""

        return self._request("GET", "https://api.apollo.io/v1/auth/health")

    def _people_search_params(
        self,
        page: int,
        per_page: int,
        organization_ids=None,
    ) -> dict:
        params = {
            "page": page,
            "per_page": per_page,
            "include_similar_titles": str(self.settings.apollo_include_similar_titles).lower(),
        }
        _add_list_param(params, "person_titles[]", self.settings.apollo_job_titles)
        _add_list_param(params, "person_locations[]", self.settings.apollo_person_locations)
        _add_list_param(params, "q_organization_job_titles[]", self.settings.apollo_target_job_titles)
        _add_list_param(params, "organization_job_locations[]", self.settings.apollo_target_job_locations)
        _add_list_param(params, "organization_locations[]", self.settings.apollo_locations)
        _add_list_param(
            params,
            "organization_num_employees_ranges[]",
            self.settings.apollo_company_size_ranges,
        )
        _add_list_param(params, "contact_email_status[]", self.settings.apollo_contact_email_statuses)
        if organization_ids:
            _add_list_param(params, "organization_ids[]", organization_ids)

        keyword_parts = list(self.settings.apollo_keywords)
        if keyword_parts:
            params["q_keywords"] = " ".join(keyword_parts)
        return params

    def _organization_search_params(self, page: int, per_page: int) -> dict:
        params = {
            "page": page,
            "per_page": per_page,
        }
        _add_list_param(params, "organization_locations[]", self.settings.apollo_locations)
        _add_list_param(
            params,
            "organization_num_employees_ranges[]",
            self.settings.apollo_company_size_ranges,
        )
        keyword_tags = self.settings.apollo_industries + self.settings.apollo_keywords
        _add_list_param(params, "q_organization_keyword_tags[]", keyword_tags)
        return params

    def search_organizations(self) -> list[str]:
        """Find organization IDs for stricter company/industry filtering."""

        organization_ids: list[str] = []
        if not self.settings.apollo_use_organization_prefilter:
            return organization_ids

        url = f"{self.settings.apollo_base_url}/mixed_companies/search"
        logger.info("Searching Apollo organizations before people search")

        for page in range(1, self.settings.apollo_fetch_max_pages + 1):
            data = self._request(
                "POST",
                url,
                params=self._organization_search_params(page, self.settings.apollo_fetch_per_page),
            )
            organizations = data.get("organizations") or data.get("accounts") or []
            if not organizations:
                break

            for organization in organizations:
                organization_id = _first_nonempty(
                    organization.get("organization_id"),
                    organization.get("id"),
                )
                if organization_id:
                    organization_ids.append(organization_id)
                if len(organization_ids) >= self.settings.apollo_max_organizations:
                    return organization_ids

        return organization_ids

    def search_people(self) -> list[dict]:
        """Search Apollo for people matching the filters in .env."""

        url = f"{self.settings.apollo_base_url}/mixed_people/api_search"
        organization_ids = self.search_organizations()
        if self.settings.apollo_use_organization_prefilter and not organization_ids:
            logger.warning("Organization prefilter found no companies; skipping people search")
            return []

        people: list[dict] = []
        for page in range(1, self.settings.apollo_fetch_max_pages + 1):
            logger.info("Searching Apollo people page %s", page)
            data = self._request(
                "POST",
                url,
                params=self._people_search_params(
                    page=page,
                    per_page=self.settings.apollo_fetch_per_page,
                    organization_ids=organization_ids or None,
                ),
            )
            page_people = data.get("people") or data.get("contacts") or []
            if not page_people:
                break
            people.extend(page_people)

        logger.info("Apollo people search returned %s raw people", len(people))
        return people

    def enrich_lead(self, lead: Lead) -> Lead:
        """Ask Apollo for an email address when search did not return one."""

        params = {
            "reveal_personal_emails": str(self.settings.apollo_reveal_personal_emails).lower(),
        }
        if lead.apollo_id:
            params["id"] = lead.apollo_id
        if lead.full_name:
            params["name"] = lead.full_name
        if lead.company_domain:
            params["domain"] = lead.company_domain
        if lead.company_name:
            params["organization_name"] = lead.company_name
        if lead.linkedin_url:
            params["linkedin_url"] = lead.linkedin_url

        if len(params) == 1:
            return lead

        logger.info("Enriching lead through Apollo: %s at %s", lead.full_name, lead.company_name)
        data = self._request(
            "POST",
            f"{self.settings.apollo_base_url}/people/match",
            params=params,
        )
        enriched = data.get("person") or data.get("contact") or data
        if isinstance(enriched, dict):
            enriched_lead = self.normalize_person(enriched)
            for field_name in lead.__dataclass_fields__:
                old_value = getattr(lead, field_name)
                new_value = getattr(enriched_lead, field_name)
                if not old_value and new_value:
                    setattr(lead, field_name, new_value)
        return lead

    def normalize_person(self, person: dict) -> Lead:
        """Convert Apollo's response shape into our Lead dataclass."""

        organization = (
            person.get("organization")
            or person.get("account")
            or person.get("current_organization")
            or {}
        )

        full_name = _first_nonempty(person.get("name"), person.get("full_name"))
        first_name = _first_nonempty(person.get("first_name"))
        last_name = _first_nonempty(person.get("last_name"))
        if not first_name and full_name:
            first_name = full_name.split(" ")[0]
        if not last_name and full_name and len(full_name.split(" ")) > 1:
            last_name = " ".join(full_name.split(" ")[1:])

        company_name = _first_nonempty(
            organization.get("name"),
            person.get("organization_name"),
            person.get("company_name"),
        )
        company_domain = _first_nonempty(
            organization.get("primary_domain"),
            organization.get("domain"),
            person.get("organization_domain"),
            _domain_from_url(_first_nonempty(organization.get("website_url"), person.get("website_url"))),
        )
        company_industry = _first_nonempty(
            organization.get("industry"),
            person.get("organization_industry"),
            person.get("industry"),
        )
        if not company_industry and isinstance(organization.get("industries"), list):
            company_industry = ", ".join(organization["industries"])

        company_size = _first_nonempty(
            organization.get("estimated_num_employees"),
            organization.get("num_employees"),
            person.get("organization_num_employees"),
        )

        title = _first_nonempty(person.get("title"), person.get("headline"))
        reason = self._build_reason(company_name, company_industry, title)

        return Lead(
            apollo_id=_first_nonempty(person.get("id"), person.get("person_id")),
            first_name=first_name,
            last_name=last_name,
            full_name=full_name or f"{first_name} {last_name}".strip(),
            email=_first_nonempty(
                person.get("email"),
                person.get("email_address"),
                person.get("work_email"),
                person.get("primary_email"),
            ),
            title=title,
            company_name=company_name,
            company_domain=company_domain,
            company_industry=company_industry,
            company_size=company_size,
            linkedin_url=_first_nonempty(person.get("linkedin_url"), person.get("person_linkedin_url")),
            city=_first_nonempty(person.get("city")),
            state=_first_nonempty(person.get("state")),
            country=_first_nonempty(person.get("country")),
            apollo_url=_first_nonempty(person.get("apollo_url")),
            reason_for_outreach=reason,
            raw_json=raw_to_json(person),
        )

    def _build_reason(self, company_name: str, industry: str, title: str) -> str:
        if company_name and industry:
            return (
                f"I noticed {company_name}'s work in {industry}, and it seemed relevant "
                "to the kind of practical data work I hope to support."
            )
        if company_name:
            return (
                f"I noticed {company_name}'s work and thought there could be a fit "
                "for analytics, data science, or data engineering support."
            )
        if title:
            return (
                f"Your role as {title} seemed connected to hiring or data team growth, "
                "so I wanted to reach out thoughtfully."
            )
        return "I wanted to reach out because your team looks relevant to data-focused work."
