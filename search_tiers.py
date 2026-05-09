"""Apollo search tiers for resilient DMV/remote discovery."""

from __future__ import annotations

from dataclasses import dataclass

from config import Settings


DMV_LOCATIONS = [
    "Washington DC",
    "District of Columbia",
    "Maryland",
    "Virginia",
    "Arlington",
    "Alexandria",
    "Bethesda",
    "Rockville",
    "Silver Spring",
    "College Park",
    "Tysons",
    "Reston",
    "Fairfax",
    "McLean",
]

INTERNSHIP_KEYWORDS = [
    "AI intern",
    "data analyst intern",
    "data scientist intern",
    "machine learning intern",
    "business analyst intern",
    "analytics intern",
    "data engineering intern",
    "summer 2026 internship",
    "early career",
]

BROAD_DATA_KEYWORDS = [
    "AI",
    "analytics",
    "data",
    "machine learning",
    "business intelligence",
    "automation",
    "Python",
    "SQL",
    "cloud",
    "product analytics",
]

COMPANY_KEYWORDS = [
    "fintech",
    "healthcare tech",
    "SaaS",
    "AI",
    "consulting",
    "analytics",
    "edtech",
    "cloud",
    "data",
]


@dataclass(frozen=True)
class SearchTier:
    name: str
    description: str
    person_titles: list[str]
    person_locations: list[str]
    organization_locations: list[str]
    target_job_titles: list[str]
    target_job_locations: list[str]
    keywords: list[str]
    keyword_queries: list[str]
    organization_keywords: list[str]
    organization_first: bool = False


def build_search_tiers(settings: Settings) -> list[SearchTier]:
    contact_titles = settings.apollo_job_titles
    company_sizes = settings.apollo_company_size_ranges
    _ = company_sizes  # Kept here so tier intent stays close to settings usage.

    return [
        SearchTier(
            name="tier_1_strict_dmv_remote",
            description="Strict DMV plus remote internship wording",
            person_titles=contact_titles,
            person_locations=DMV_LOCATIONS,
            organization_locations=DMV_LOCATIONS,
            target_job_titles=INTERNSHIP_KEYWORDS,
            target_job_locations=DMV_LOCATIONS + ["Remote", "Remote United States"],
            keywords=INTERNSHIP_KEYWORDS + ["remote", "hybrid"],
            keyword_queries=[
                "data analyst intern remote",
                "data scientist intern remote",
                "machine learning intern remote",
                "AI intern remote",
                "business analyst intern",
                "analytics intern",
                "data engineering intern",
                "summer 2026 internship",
            ],
            organization_keywords=INTERNSHIP_KEYWORDS,
        ),
        SearchTier(
            name="tier_2_dmv_broader_roles",
            description="DMV companies with broader data and AI signals",
            person_titles=contact_titles,
            person_locations=DMV_LOCATIONS,
            organization_locations=DMV_LOCATIONS,
            target_job_titles=BROAD_DATA_KEYWORDS,
            target_job_locations=DMV_LOCATIONS,
            keywords=BROAD_DATA_KEYWORDS,
            keyword_queries=[
                "data analytics",
                "business intelligence",
                "machine learning",
                "Python SQL",
                "cloud data",
                "product analytics",
                "automation",
            ],
            organization_keywords=BROAD_DATA_KEYWORDS,
        ),
        SearchTier(
            name="tier_3_remote_us_internships",
            description="Remote U.S. internships in data, analytics, AI, and cloud",
            person_titles=contact_titles,
            person_locations=["United States"],
            organization_locations=["United States"],
            target_job_titles=INTERNSHIP_KEYWORDS,
            target_job_locations=["Remote", "Remote United States", "United States"],
            keywords=INTERNSHIP_KEYWORDS + ["remote"],
            keyword_queries=[
                "remote data analyst intern",
                "remote data scientist intern",
                "remote machine learning intern",
                "remote AI intern",
                "remote business analyst intern",
                "remote analytics intern",
                "remote data engineering intern",
            ],
            organization_keywords=INTERNSHIP_KEYWORDS + ["remote"],
        ),
        SearchTier(
            name="tier_4_warm_company_search",
            description="Company-first search for high-fit sectors, then relevant people",
            person_titles=contact_titles,
            person_locations=DMV_LOCATIONS + ["United States"],
            organization_locations=DMV_LOCATIONS + ["Remote", "United States"],
            target_job_titles=BROAD_DATA_KEYWORDS,
            target_job_locations=DMV_LOCATIONS + ["Remote", "Remote United States"],
            keywords=BROAD_DATA_KEYWORDS + COMPANY_KEYWORDS,
            keyword_queries=[
                "fintech data",
                "healthcare analytics",
                "SaaS data",
                "AI startup",
                "cloud consulting",
                "edtech analytics",
                "business intelligence",
            ],
            organization_keywords=COMPANY_KEYWORDS,
            organization_first=True,
        ),
    ]
