"""Command-line entry point for the cold email workflow."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from apollo_client import ApolloClient
from config import load_settings, ensure_local_folders
from gmail_client import GmailClient
from logging_setup import setup_logging
import umd_ta_ra_workflow
from workflow import ColdEmailWorkflow


logger = logging.getLogger(__name__)


def _dry_run_from_args(args, settings) -> bool:
    if getattr(args, "live", False):
        return False
    if getattr(args, "dry_run", False):
        return True
    return settings.dry_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch Apollo leads and send personalized Gmail API outreach.",
    )
    parser.add_argument("--verbose", action="store_true", help="Show debug logs.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create the SQLite database tables.")
    subparsers.add_parser("apollo-test", help="Confirm Apollo API key access.")
    subparsers.add_parser("gmail-auth", help="Run Gmail OAuth and create token.json.")

    fetch_parser = subparsers.add_parser("fetch-leads", help="Fetch and store leads from Apollo.")
    fetch_parser.add_argument("--max-pages", type=int, help="Override APOLLO_FETCH_MAX_PAGES.")
    fetch_parser.add_argument("--per-page", type=int, help="Override APOLLO_FETCH_PER_PAGE.")

    discover_parser = subparsers.add_parser(
        "discover",
        help="Nightly workflow: fetch leads, export CSV, and save email previews without sending.",
    )
    discover_parser.add_argument("--max-pages", type=int, help="Override APOLLO_FETCH_MAX_PAGES.")
    discover_parser.add_argument("--per-page", type=int, help="Override APOLLO_FETCH_PER_PAGE.")
    discover_parser.add_argument("--preview-limit", type=int, default=200, help="Number of pending emails to preview.")

    preview_parser = subparsers.add_parser("preview", help="Render pending emails without sending.")
    preview_parser.add_argument("--limit", type=int, default=3, help="Number of emails to preview.")
    preview_parser.add_argument("--output", help="Optional path to save preview text.")

    send_parser = subparsers.add_parser("send", help="Send pending emails through Gmail API.")
    send_parser.add_argument("--limit", type=int, help="Maximum emails to send in this run.")
    send_parser.add_argument("--dry-run", action="store_true", help="Force preview mode.")
    send_parser.add_argument("--live", action="store_true", help="Actually send emails.")

    test_parser = subparsers.add_parser("send-test", help="Send one test email to yourself.")
    test_parser.add_argument("--to", required=True, help="Recipient email for the test.")
    test_parser.add_argument("--dry-run", action="store_true", help="Force preview mode.")
    test_parser.add_argument("--live", action="store_true", help="Actually send the test email.")

    run_parser = subparsers.add_parser(
        "run",
        help="Daily workflow: fetch leads, export CSV, then send pending emails.",
    )
    run_parser.add_argument("--dry-run", action="store_true", help="Force preview mode.")
    run_parser.add_argument("--live", action="store_true", help="Actually send emails.")
    run_parser.add_argument("--limit", type=int, help="Maximum emails to send in this run.")

    subparsers.add_parser("export-csv", help="Export SQLite leads to CSV.")
    subparsers.add_parser("rescore", help="Apply current scoring/dedupe gates to existing queued leads.")
    subparsers.add_parser("status", help="Print status counts and today's send count.")

    umd_discover_parser = subparsers.add_parser(
        "umd-discover",
        help="Search UMD TA/RA/course support pages and draft reviewable emails without sending.",
    )
    umd_discover_parser.add_argument("--max-pages", type=int, help="Limit UMD pages searched in this run.")
    umd_discover_parser.add_argument("--target-contacts", type=int, help="Target Good/High Fit UMD contacts.")
    umd_discover_parser.add_argument("--max-contacts", type=int, help="Maximum new UMD contacts to add.")
    umd_discover_parser.add_argument("--search-depth", choices=["standard", "expanded", "aggressive"], default="standard")
    umd_discover_parser.add_argument("--min-score", type=int, help="Minimum fit score to keep.")

    umd_send_parser = subparsers.add_parser(
        "umd-send-approved",
        help="Send approved UMD TA/RA drafts. Dry-run by default.",
    )
    umd_send_parser.add_argument("--limit", type=int, default=5, help="Maximum approved UMD drafts to process.")
    umd_send_parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode.")
    umd_send_parser.add_argument("--live", action="store_true", help="Actually send approved UMD drafts if enabled.")

    umd_campaign_parser = subparsers.add_parser("umd-create-campaign", help="Create a UMD TA/RA campaign from approved drafts.")
    umd_campaign_parser.add_argument("--name", required=True, help="Campaign name.")
    umd_campaign_parser.add_argument("--semester", default="Both", help="Summer 2026, Fall 2026, Both, or General.")
    umd_campaign_parser.add_argument("--min-score", type=int, default=65)
    umd_campaign_parser.add_argument("--max-emails", type=int, help="Maximum campaign recipients.")

    umd_campaign_send_parser = subparsers.add_parser("umd-send-campaign", help="Dry-run or send a UMD TA/RA campaign with randomized lag.")
    umd_campaign_send_parser.add_argument("--campaign-id", type=int, required=True)
    umd_campaign_send_parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode.")
    umd_campaign_send_parser.add_argument("--live", action="store_true", help="Actually send campaign emails if enabled.")
    umd_campaign_send_parser.add_argument("--min-delay", type=int, help="Minimum delay seconds.")
    umd_campaign_send_parser.add_argument("--max-delay", type=int, help="Maximum delay seconds.")
    umd_campaign_send_parser.add_argument("--daily-limit", type=int, help="Daily send limit.")
    umd_campaign_send_parser.add_argument("--max-emails", type=int, help="Maximum emails to process.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = load_settings()
    ensure_local_folders(settings)
    setup_logging(settings.log_file, verbose=args.verbose)

    workflow = ColdEmailWorkflow(settings)

    try:
        if args.command == "init-db":
            workflow.init_db()
        elif args.command == "apollo-test":
            result = ApolloClient(settings).test_api_key()
            print(result)
        elif args.command == "gmail-auth":
            GmailClient(settings).authenticate()
            print(f"Gmail token is ready: {settings.gmail_token_file}")
        elif args.command == "fetch-leads":
            workflow.fetch_leads(max_pages=args.max_pages, per_page=args.per_page)
        elif args.command == "discover":
            workflow.init_db()
            workflow.fetch_leads(max_pages=args.max_pages, per_page=args.per_page)
            workflow.export_csv()
            workflow.preview_emails(limit=args.preview_limit)
            workflow.status_report()
        elif args.command == "preview":
            workflow.preview_emails(limit=args.limit, output_path=args.output)
        elif args.command == "send":
            dry_run = _dry_run_from_args(args, settings)
            workflow.send_pending(dry_run=dry_run, limit=args.limit)
        elif args.command == "send-test":
            dry_run = _dry_run_from_args(args, settings)
            workflow.send_test(to_email=args.to, dry_run=dry_run)
        elif args.command == "run":
            dry_run = _dry_run_from_args(args, settings)
            workflow.init_db()
            workflow.fetch_leads()
            workflow.export_csv()
            workflow.send_pending(dry_run=dry_run, limit=args.limit)
            workflow.status_report()
        elif args.command == "export-csv":
            workflow.export_csv()
        elif args.command == "rescore":
            counts = workflow.rescore_existing_leads()
            print(counts)
        elif args.command == "status":
            workflow.status_report()
        elif args.command == "umd-discover":
            counts = umd_ta_ra_workflow.run_discovery(
                settings,
                max_pages=args.max_pages,
                target_contacts=args.target_contacts,
                max_contacts=args.max_contacts,
                search_depth=args.search_depth,
                min_score=args.min_score,
            )
            print(counts)
        elif args.command == "umd-send-approved":
            dry_run = _dry_run_from_args(args, settings)
            counts = umd_ta_ra_workflow.send_approved_drafts(
                settings,
                limit=args.limit,
                dry_run=dry_run,
            )
            print(counts)
        elif args.command == "umd-create-campaign":
            campaign_id, counts = umd_ta_ra_workflow.create_campaign(
                settings,
                campaign_name=args.name,
                semester_target=args.semester,
                min_score=args.min_score,
                max_emails=args.max_emails,
            )
            print({"campaign_id": campaign_id, **counts})
        elif args.command == "umd-send-campaign":
            dry_run = _dry_run_from_args(args, settings)
            counts = umd_ta_ra_workflow.run_campaign_send(
                settings,
                campaign_id=args.campaign_id,
                dry_run=dry_run,
                min_delay_seconds=args.min_delay,
                max_delay_seconds=args.max_delay,
                daily_send_limit=args.daily_limit,
                max_emails=args.max_emails,
                sleep_between=not dry_run,
            )
            print(counts)
        else:
            parser.print_help()
            return 2
    except Exception as exc:
        logger.exception("Command failed: %s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
