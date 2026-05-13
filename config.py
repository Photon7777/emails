"""Configuration helpers for the cold email workflow.

The project reads secrets and settings from a local .env file. Keeping all
configuration here makes the rest of the code easier to read and avoids
hardcoding API keys or credentials.
"""

from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", override=True)


def _get_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int) -> int:
    raw_value = _get_str(name, str(default))
    try:
        return int(raw_value)
    except ValueError:
        raise ValueError(f"{name} must be an integer, but got {raw_value!r}.")


def _get_bool(name: str, default: bool) -> bool:
    raw_value = _get_str(name, str(default)).lower()
    return raw_value in {"1", "true", "yes", "y", "on"}


def _get_list(name: str, default: str = "", delimiter: str = ",") -> list[str]:
    raw_value = _get_str(name, default)
    if not raw_value:
        return []
    return [item.strip() for item in raw_value.split(delimiter) if item.strip()]


def _path_from_env(name: str, default: str) -> Path:
    raw_path = Path(_get_str(name, default)).expanduser()
    if raw_path.is_absolute():
        return raw_path
    return PROJECT_ROOT / raw_path


@dataclass(frozen=True)
class Settings:
    project_root: Path

    apollo_api_key: str
    apollo_auth_mode: str
    apollo_base_url: str
    apollo_job_titles: list[str]
    apollo_target_job_titles: list[str]
    apollo_target_job_locations: list[str]
    apollo_person_locations: list[str]
    apollo_locations: list[str]
    apollo_industries: list[str]
    apollo_keywords: list[str]
    apollo_company_size_ranges: list[str]
    apollo_use_organization_prefilter: bool
    apollo_max_organizations: int
    apollo_fetch_per_page: int
    apollo_fetch_max_pages: int
    apollo_include_similar_titles: bool
    apollo_contact_email_statuses: list[str]
    apollo_enrich_missing_emails: bool
    apollo_reveal_personal_emails: bool
    apollo_daily_credit_limit: int
    apollo_total_credits: int
    apollo_account_credits_used: int
    apollo_credit_renewal: str
    base_monthly_apollo_credits: int
    apollo_credit_reset_day: int
    estimated_credit_cost_per_enrichment: int
    enable_credit_guardrails: bool
    min_apollo_credits_reserve: int
    min_score_to_enrich: int
    min_score_to_send: int
    max_contacts_per_company_per_week: int
    lead_score_threshold: int
    allow_unverified_email_patterns: bool
    umd_ta_ra_max_pages: int
    umd_ta_ra_search_results_per_query: int
    umd_ta_ra_request_delay_seconds: int
    umd_ta_ra_min_fit_score: int
    umd_ta_ra_high_fit_score: int
    umd_ta_ra_send_enabled: bool

    gmail_credentials_file: Path
    gmail_token_file: Path

    sender_name: str
    sender_email: str
    sender_role: str
    sender_location: str
    sender_linkedin: str
    sender_portfolio: str
    sender_physical_address: str
    sender_background: str
    attach_resume: bool
    resume_file: Path

    email_subject: str
    email_template_path: Path
    unsubscribe_text: str

    dry_run: bool
    daily_send_limit: int
    daily_send_target_min: int
    pending_inventory_target: int
    delay_between_emails_seconds: int
    max_retries: int

    database_path: Path
    database_url: str
    leads_csv_path: Path
    email_preview_path: Path
    suppression_list_path: Path
    do_not_contact_path: Path
    already_contacted_path: Path
    log_file: Path


def load_settings() -> Settings:
    """Load all settings from .env and apply safe defaults."""

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    return Settings(
        project_root=PROJECT_ROOT,
        apollo_api_key=_get_str("APOLLO_API_KEY"),
        apollo_auth_mode=_get_str("APOLLO_AUTH_MODE", "x-api-key").lower(),
        apollo_base_url=_get_str("APOLLO_BASE_URL", "https://api.apollo.io/api/v1").rstrip("/"),
        apollo_job_titles=_get_list("APOLLO_FILTER_JOB_TITLES"),
        apollo_target_job_titles=_get_list("APOLLO_TARGET_JOB_TITLES"),
        apollo_target_job_locations=_get_list("APOLLO_TARGET_JOB_LOCATIONS"),
        apollo_person_locations=_get_list("APOLLO_FILTER_PERSON_LOCATIONS"),
        apollo_locations=_get_list("APOLLO_FILTER_LOCATIONS"),
        apollo_industries=_get_list("APOLLO_FILTER_INDUSTRIES"),
        apollo_keywords=_get_list("APOLLO_FILTER_KEYWORDS"),
        apollo_company_size_ranges=_get_list("APOLLO_FILTER_COMPANY_SIZE_RANGES", delimiter=";"),
        apollo_use_organization_prefilter=_get_bool("APOLLO_USE_ORGANIZATION_PREFILTER", False),
        apollo_max_organizations=_get_int("APOLLO_MAX_ORGANIZATIONS", 50),
        apollo_fetch_per_page=_get_int("APOLLO_FETCH_PER_PAGE", 25),
        apollo_fetch_max_pages=_get_int("APOLLO_FETCH_MAX_PAGES", 2),
        apollo_include_similar_titles=_get_bool("APOLLO_INCLUDE_SIMILAR_TITLES", True),
        apollo_contact_email_statuses=_get_list(
            "APOLLO_CONTACT_EMAIL_STATUSES",
            "verified,likely to engage",
        ),
        apollo_enrich_missing_emails=_get_bool("APOLLO_ENRICH_MISSING_EMAILS", True),
        apollo_reveal_personal_emails=_get_bool("APOLLO_REVEAL_PERSONAL_EMAILS", False),
        apollo_daily_credit_limit=_get_int(
            "DAILY_ENRICH_LIMIT",
            _get_int("APOLLO_DAILY_CREDIT_LIMIT", 25),
        ),
        apollo_total_credits=_get_int("APOLLO_TOTAL_CREDITS", 2630),
        apollo_account_credits_used=_get_int("APOLLO_ACCOUNT_CREDITS_USED", 535),
        apollo_credit_renewal=_get_str("APOLLO_CREDIT_RENEWAL", "Jun 4, 2026, 2:41 AM"),
        base_monthly_apollo_credits=_get_int(
            "BASE_MONTHLY_APOLLO_CREDITS",
            _get_int("APOLLO_TOTAL_CREDITS", 2630),
        ),
        apollo_credit_reset_day=_get_int("APOLLO_CREDIT_RESET_DAY", 1),
        estimated_credit_cost_per_enrichment=_get_int("ESTIMATED_CREDIT_COST_PER_ENRICHMENT", 1),
        enable_credit_guardrails=_get_bool("ENABLE_CREDIT_GUARDRAILS", True),
        min_apollo_credits_reserve=_get_int("MIN_APOLLO_CREDITS_RESERVE", 100),
        min_score_to_enrich=_get_int("MIN_SCORE_TO_ENRICH", 55),
        min_score_to_send=_get_int("MIN_SCORE_TO_SEND", _get_int("LEAD_SCORE_THRESHOLD", 70)),
        max_contacts_per_company_per_week=_get_int("MAX_CONTACTS_PER_COMPANY_PER_WEEK", 2),
        lead_score_threshold=_get_int("MIN_SCORE_TO_SEND", _get_int("LEAD_SCORE_THRESHOLD", 70)),
        allow_unverified_email_patterns=_get_bool("ALLOW_UNVERIFIED_EMAIL_PATTERNS", False),
        umd_ta_ra_max_pages=_get_int("UMD_TA_RA_MAX_PAGES", 30),
        umd_ta_ra_search_results_per_query=_get_int("UMD_TA_RA_SEARCH_RESULTS_PER_QUERY", 5),
        umd_ta_ra_request_delay_seconds=_get_int("UMD_TA_RA_REQUEST_DELAY_SECONDS", 1),
        umd_ta_ra_min_fit_score=_get_int("UMD_TA_RA_MIN_FIT_SCORE", 55),
        umd_ta_ra_high_fit_score=_get_int("UMD_TA_RA_HIGH_FIT_SCORE", 70),
        umd_ta_ra_send_enabled=_get_bool("UMD_TA_RA_SEND_ENABLED", False),
        gmail_credentials_file=_path_from_env("GMAIL_CREDENTIALS_FILE", "credentials.json"),
        gmail_token_file=_path_from_env("GMAIL_TOKEN_FILE", "token.json"),
        sender_name=_get_str("SENDER_NAME"),
        sender_email=_get_str("SENDER_EMAIL"),
        sender_role=_get_str("SENDER_ROLE"),
        sender_location=_get_str("SENDER_LOCATION"),
        sender_linkedin=_get_str("SENDER_LINKEDIN"),
        sender_portfolio=_get_str("SENDER_PORTFOLIO"),
        sender_physical_address=_get_str("SENDER_PHYSICAL_ADDRESS"),
        sender_background=_get_str("SENDER_BACKGROUND"),
        attach_resume=_get_bool("ATTACH_RESUME", False),
        resume_file=_path_from_env("RESUME_FILE", ""),
        email_subject=_get_str(
            "EMAIL_SUBJECT",
            "Interest in data internship opportunities at {company_name}",
        ),
        email_template_path=_path_from_env("EMAIL_TEMPLATE_PATH", "templates/internship_outreach.txt"),
        unsubscribe_text=_get_str(
            "UNSUBSCRIBE_TEXT",
            "",
        ),
        dry_run=_get_bool("DRY_RUN", True),
        daily_send_limit=_get_int("DAILY_SEND_LIMIT", 20),
        daily_send_target_min=_get_int("DAILY_SEND_TARGET_MIN", 0),
        pending_inventory_target=_get_int("PENDING_INVENTORY_TARGET", 0),
        delay_between_emails_seconds=_get_int("DELAY_BETWEEN_EMAILS_SECONDS", 45),
        max_retries=_get_int("MAX_RETRIES", 3),
        database_path=_path_from_env("DATABASE_PATH", "data/leads.sqlite"),
        database_url=_get_str("DATABASE_URL"),
        leads_csv_path=_path_from_env("LEADS_CSV_PATH", "data/leads_export.csv"),
        email_preview_path=_path_from_env("EMAIL_PREVIEW_PATH", "data/email_previews.txt"),
        suppression_list_path=_path_from_env("SUPPRESSION_LIST_PATH", "data/suppression_list.txt"),
        do_not_contact_path=_path_from_env("DO_NOT_CONTACT_PATH", "data/do_not_contact.txt"),
        already_contacted_path=_path_from_env("ALREADY_CONTACTED_PATH", "data/already_contacted.txt"),
        log_file=_path_from_env("LOG_FILE", "logs/cold_email_workflow.log"),
    )


def ensure_local_folders(settings: Settings) -> None:
    """Create local data/log folders if they do not exist yet."""

    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.leads_csv_path.parent.mkdir(parents=True, exist_ok=True)
    settings.email_preview_path.parent.mkdir(parents=True, exist_ok=True)
    settings.suppression_list_path.parent.mkdir(parents=True, exist_ok=True)
    settings.do_not_contact_path.parent.mkdir(parents=True, exist_ok=True)
    settings.already_contacted_path.parent.mkdir(parents=True, exist_ok=True)
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)


def validate_apollo_settings(settings: Settings) -> None:
    if not settings.apollo_api_key or settings.apollo_api_key == "replace_with_your_apollo_api_key":
        raise ValueError("Set APOLLO_API_KEY in .env before calling Apollo.")
    if settings.apollo_auth_mode not in {"x-api-key", "bearer"}:
        raise ValueError('APOLLO_AUTH_MODE must be either "x-api-key" or "bearer".')


def validate_gmail_settings(settings: Settings) -> None:
    if not settings.gmail_credentials_file.exists():
        raise FileNotFoundError(
            f"Missing Gmail OAuth file: {settings.gmail_credentials_file}. "
            "Download it from Google Cloud and save it as credentials.json."
        )


def validate_sender_settings(settings: Settings) -> None:
    missing = []
    for label, value in {
        "SENDER_NAME": settings.sender_name,
        "SENDER_EMAIL": settings.sender_email,
        "SENDER_ROLE": settings.sender_role,
    }.items():
        if not value or value.startswith("your."):
            missing.append(label)
    if missing:
        raise ValueError(f"Fill in these sender identity fields in .env: {', '.join(missing)}")


def validate_resume_attachment(settings: Settings) -> None:
    if settings.attach_resume and not settings.resume_file.exists():
        raise FileNotFoundError(
            f"ATTACH_RESUME=true, but RESUME_FILE does not exist: {settings.resume_file}"
        )
