"""Apollo search tiers for resilient full-time data/AI job discovery."""

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

US_JOB_LOCATIONS = [
    "United States",
    "Remote",
    "Remote United States",
    "New York",
    "Boston",
    "San Francisco Bay Area",
    "Seattle",
    "Austin",
    "Chicago",
    "Atlanta",
    "Dallas",
    "Denver",
]

FULL_TIME_ROLE_KEYWORDS = [
    "data analyst",
    "business analyst",
    "BI analyst",
    "product analyst",
    "data engineer",
    "analytics engineer",
    "AI engineer",
    "machine learning engineer",
    "junior data scientist",
    "associate data scientist",
    "data science analyst",
    "cloud data engineer",
    "Python SQL analyst",
    "entry-level AI engineer",
    "new grad data analyst",
    "early career data engineer",
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
    "ETL",
    "data pipelines",
    "new grad",
    "entry level",
    "early career",
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
            name="tier_1_dmv_remote_full_time",
            description="DMV plus remote U.S. full-time data, analytics, and AI roles",
            person_titles=contact_titles,
            person_locations=DMV_LOCATIONS,
            organization_locations=DMV_LOCATIONS + ["Remote", "Remote United States"],
            target_job_titles=FULL_TIME_ROLE_KEYWORDS,
            target_job_locations=DMV_LOCATIONS + ["Remote", "Remote United States"],
            keywords=FULL_TIME_ROLE_KEYWORDS + ["full-time", "new grad", "early career", "remote", "hybrid"],
            keyword_queries=[
                "data analyst full-time remote",
                "data engineer full-time remote",
                "AI engineer full-time remote",
                "machine learning engineer entry level",
                "business analyst new grad",
                "analytics engineer early career",
                "product analyst full-time",
                "cloud data engineer junior",
            ],
            organization_keywords=FULL_TIME_ROLE_KEYWORDS,
        ),
        SearchTier(
            name="tier_2_us_remote_broader_roles",
            description="U.S. and remote companies with broader data and AI signals",
            person_titles=contact_titles,
            person_locations=DMV_LOCATIONS + ["United States"],
            organization_locations=DMV_LOCATIONS + US_JOB_LOCATIONS,
            target_job_titles=BROAD_DATA_KEYWORDS,
            target_job_locations=US_JOB_LOCATIONS + DMV_LOCATIONS,
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
            name="tier_3_remote_us_full_time",
            description="Remote U.S. full-time data, analytics, AI, and cloud roles",
            person_titles=contact_titles,
            person_locations=["United States"],
            organization_locations=["United States"],
            target_job_titles=FULL_TIME_ROLE_KEYWORDS,
            target_job_locations=["Remote", "Remote United States", "United States"],
            keywords=FULL_TIME_ROLE_KEYWORDS + ["remote", "full-time", "new grad"],
            keyword_queries=[
                "remote data analyst full-time",
                "remote data engineer full-time",
                "remote machine learning engineer entry level",
                "remote AI engineer new grad",
                "remote business analyst full-time",
                "remote analytics engineer",
                "remote cloud data engineer",
            ],
            organization_keywords=FULL_TIME_ROLE_KEYWORDS + ["remote"],
        ),
        SearchTier(
            name="tier_4_warm_company_search",
            description="Company-first search for high-fit sectors, then recruiting or data leaders",
            person_titles=contact_titles,
            person_locations=DMV_LOCATIONS + ["United States"],
            organization_locations=DMV_LOCATIONS + US_JOB_LOCATIONS,
            target_job_titles=BROAD_DATA_KEYWORDS,
            target_job_locations=US_JOB_LOCATIONS + DMV_LOCATIONS,
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
