from __future__ import annotations

import argparse
from dataclasses import asdict
from ipaddress import ip_address
import json
import os
from pathlib import Path
import sys

from .artifact import (
    ARTIFACT_DB_NAME,
    ArtifactCapError,
    ArtifactContactError,
    ArtifactError,
    ArtifactPermissionError,
    ArtifactValidationError,
    export_public_artifact,
    sha256_file,
)
from .audit import artifact_item, evaluate_audit
from .config import (
    DEFAULT_DB,
    DEFAULT_JSONL,
    DEFAULT_READINESS_PROBES,
    DEFAULT_RAW_DIR,
    MAIN_THREADMARK_CATEGORY_ID,
    TARGET_READER_URL,
    default_user_agent,
)
from .deploy_policy import PUBLIC_CAP_LIMITS, public_cap_errors, public_contact_errors
from .deploy_bundle import BUNDLE_MANIFEST_NAME, DEFAULT_BUNDLE_DIR, DeployBundleError, create_deploy_bundle, verify_deploy_bundle
from .fetch import CacheMiss, PoliteFetcher, RobotsDenied
from .indexer import build_index
from .nextstep import recommend_next_step
from .permission import (
    DEFAULT_PERMISSION_NOTE,
    permission_note_summary,
    render_permission_request_template,
    write_permission_note_template,
    write_permission_request_template,
)
from .preview import (
    DEFAULT_PREVIEW_SERVER_LOG,
    DEFAULT_PREVIEW_STATE,
    DEFAULT_PREVIEW_TUNNEL_LOG,
    PreviewError,
    preview_status,
    start_public_preview,
    stop_public_preview,
)
from .review import render_author_review_packet
from .runbook import render_runbook
from .scrape import (
    discover_categories,
    discover_page_count,
    normalize_reader_root,
    plan_reader_crawl,
    scrape_reader,
    scrape_reader_with_stats,
    select_page_urls,
)
from .search import (
    list_threadmarks_db,
    search_db,
    search_totals_db,
)
from .smoke import run_public_smoke
from .site_policy import make_site_policy_review, render_site_policy_review_markdown
from .status import corpus_summary, db_summary, fetch_log_summary
from .validate import validate_corpus, validate_launch_ready
from .web import serve


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RobotsDenied as exc:
        print(f"Refusing to fetch: {exc}", file=sys.stderr)
        return 2
    except CacheMiss as exc:
        print(f"Offline cache miss: {exc}", file=sys.stderr)
        return 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="thread-search")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scout = subparsers.add_parser("scout", help="Check robots and report reader structure.")
    add_fetch_args(scout)
    scout.add_argument("url", nargs="?", default=TARGET_READER_URL)
    scout.set_defaults(func=cmd_scout)

    site_review = subparsers.add_parser(
        "site-review",
        help="Snapshot robots decisions and official policy URLs for the deployment permission review.",
    )
    add_fetch_args(site_review)
    site_review.add_argument("url", nargs="?", default=TARGET_READER_URL)
    site_review.add_argument("--category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    site_review.add_argument("--out", type=Path)
    site_review.add_argument("--format", choices=["markdown", "json"], default="markdown")
    site_review.set_defaults(func=cmd_site_review)

    plan = subparsers.add_parser("plan", help="Dry-run the reader crawl and list planned page URLs.")
    add_fetch_args(plan)
    plan.add_argument("url", nargs="?", default=TARGET_READER_URL)
    plan.add_argument("--category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    plan.add_argument("--category-name", default="Threadmarks")
    plan.add_argument("--max-pages", type=int)
    plan.add_argument("--manifest", type=Path, help="Write crawl plan JSON to this path.")
    plan.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    plan.set_defaults(func=cmd_plan)

    permission = subparsers.add_parser("permission-note", help="Create or inspect the local public-deployment permission note.")
    permission.add_argument("--out", type=Path, default=DEFAULT_PERMISSION_NOTE)
    permission.add_argument("--overwrite", action="store_true")
    permission.add_argument("--check", action="store_true", help="Check an existing permission note instead of writing a template.")
    permission.add_argument("--json", action="store_true")
    permission.set_defaults(func=cmd_permission_note)

    permission_request = subparsers.add_parser(
        "permission-request",
        help="Draft an author/admin permission request for public source-linked search.",
    )
    permission_request.add_argument("--out", type=Path, help="Write the request draft to this path instead of stdout.")
    permission_request.add_argument("--overwrite", action="store_true")
    permission_request.add_argument("--source-reader-url", default=TARGET_READER_URL)
    permission_request.add_argument("--public-base-url", default="not deployed yet")
    permission_request.add_argument("--operator", default="local operator")
    permission_request.add_argument("--contact", default="")
    permission_request.set_defaults(func=cmd_permission_request)

    prefetch = subparsers.add_parser("prefetch", help="Fetch selected reader pages into cache without extracting text.")
    add_fetch_args(prefetch)
    prefetch.add_argument("url", nargs="?", default=TARGET_READER_URL)
    prefetch.add_argument("--category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    prefetch.add_argument("--category-name", default="Threadmarks")
    prefetch.add_argument("--from-page", type=int, default=1)
    prefetch.add_argument("--to-page", type=int)
    prefetch.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum uncached reader pages to fetch in this run. Use 0 to report only.",
    )
    prefetch.set_defaults(func=cmd_prefetch)

    scrape = subparsers.add_parser("scrape", help="Download and extract reader threadmarks.")
    add_fetch_args(scrape)
    scrape.add_argument("url", nargs="?", default=TARGET_READER_URL)
    scrape.add_argument("--out", type=Path, default=DEFAULT_JSONL)
    scrape.add_argument("--category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    scrape.add_argument("--category-name", default="Threadmarks")
    scrape.add_argument("--max-pages", type=int)
    scrape.add_argument("--quiet", action="store_true", help="Suppress per-page progress output.")
    scrape.set_defaults(func=cmd_scrape)

    build = subparsers.add_parser("build", help="Scrape, index, and validate in one controlled run.")
    add_fetch_args(build)
    build.add_argument("url", nargs="?", default=TARGET_READER_URL)
    build.add_argument("--out", type=Path, default=DEFAULT_JSONL)
    build.add_argument("--db", type=Path, default=DEFAULT_DB)
    build.add_argument("--category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    build.add_argument("--category-name", default="Threadmarks")
    build.add_argument("--max-pages", type=int)
    build.add_argument("--expected-threadmarks", type=int, default=269)
    build.add_argument("--expected-category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    build.add_argument("--excluded-categories", type=int, nargs="*", default=[4, 5])
    build.add_argument("--probe", action="append", default=[], help="Require a search term to return a result.")
    build.add_argument("--quiet", action="store_true", help="Suppress per-page progress output.")
    build.set_defaults(func=cmd_build)

    index = subparsers.add_parser("index", help="Build a SQLite FTS index from extracted JSONL.")
    index.add_argument("--input", type=Path, default=DEFAULT_JSONL)
    index.add_argument("--db", type=Path, default=DEFAULT_DB)
    index.set_defaults(func=cmd_index)

    search = subparsers.add_parser("search", help="Search the local SQLite FTS index.")
    search.add_argument("query")
    search.add_argument("--db", type=Path, default=DEFAULT_DB)
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--mode", choices=["all", "any"], default="all")
    search.add_argument("--from-order", type=int, dest="order_min")
    search.add_argument("--to-order", type=int, dest="order_max")
    search.add_argument("--all-chunks", action="store_true", help="Show multiple chunk hits per threadmark.")
    search.add_argument("--sort", choices=["relevance", "timeline"], default="relevance")
    search.add_argument("--format", choices=["text", "json"], default="text")
    search.set_defaults(func=cmd_search)

    toc = subparsers.add_parser("toc", help="List threadmark metadata without body text.")
    toc.add_argument("--db", type=Path, default=DEFAULT_DB)
    toc.add_argument("--limit", type=int, default=300)
    toc.add_argument("--from-order", type=int, dest="order_min")
    toc.add_argument("--to-order", type=int, dest="order_max")
    toc.add_argument("--format", choices=["markdown", "json", "text"], default="markdown")
    toc.set_defaults(func=cmd_toc)

    status = subparsers.add_parser("status", help="Show crawl, corpus, index, and launch readiness state.")
    add_fetch_args(status)
    status.add_argument("url", nargs="?", default=TARGET_READER_URL)
    status.add_argument("--input", type=Path, default=DEFAULT_JSONL)
    status.add_argument("--db", type=Path, default=DEFAULT_DB)
    status.add_argument("--category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    status.add_argument("--category-name", default="Threadmarks")
    status.add_argument("--expected-threadmarks", type=int, default=269)
    status.add_argument("--expected-category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    status.add_argument("--excluded-categories", type=int, nargs="*", default=[4, 5])
    status.add_argument("--probe", action="append", default=[])
    status.add_argument("--json", action="store_true")
    status.add_argument("--strict", action="store_true", help="Exit nonzero if validation fails.")
    status.set_defaults(func=cmd_status)

    next_step = subparsers.add_parser("next-step", help="Print the next safest command for the cautious crawl/deploy workflow.")
    add_fetch_args(next_step)
    next_step.add_argument("url", nargs="?", default=TARGET_READER_URL)
    next_step.add_argument("--input", type=Path, default=DEFAULT_JSONL)
    next_step.add_argument("--db", type=Path, default=DEFAULT_DB)
    next_step.add_argument("--category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    next_step.add_argument("--category-name", default="Threadmarks")
    next_step.add_argument("--expected-threadmarks", type=int, default=269)
    next_step.add_argument("--expected-category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    next_step.add_argument("--excluded-categories", type=int, nargs="*", default=[4, 5])
    next_step.add_argument("--probe", action="append", default=[])
    next_step.add_argument("--artifact-manifest", type=Path, default=Path("dist/thread-search-public/manifest.json"))
    next_step.add_argument("--permission-note", type=Path, default=DEFAULT_PERMISSION_NOTE)
    next_step.add_argument("--public-base-url", help="Include this live base URL in final audit recommendations.")
    next_step.add_argument("--audit-report", type=Path, default=Path("data/final-audit.json"))
    next_step.add_argument("--deploy-bundle-manifest", type=Path, default=DEFAULT_BUNDLE_DIR / BUNDLE_MANIFEST_NAME)
    next_step.add_argument("--prefetch-delay", type=int, default=30)
    next_step.add_argument("--json", action="store_true")
    next_step.set_defaults(func=cmd_next_step)

    runbook = subparsers.add_parser("runbook", help="Render a Markdown runbook for the cautious crawl/deploy workflow.")
    add_fetch_args(runbook)
    runbook.add_argument("url", nargs="?", default=TARGET_READER_URL)
    runbook.add_argument("--input", type=Path, default=DEFAULT_JSONL)
    runbook.add_argument("--db", type=Path, default=DEFAULT_DB)
    runbook.add_argument("--category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    runbook.add_argument("--category-name", default="Threadmarks")
    runbook.add_argument("--expected-threadmarks", type=int, default=269)
    runbook.add_argument("--expected-category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    runbook.add_argument("--excluded-categories", type=int, nargs="*", default=[4, 5])
    runbook.add_argument("--probe", action="append", default=[])
    runbook.add_argument("--artifact-manifest", type=Path, default=Path("dist/thread-search-public/manifest.json"))
    runbook.add_argument("--permission-note", type=Path, default=DEFAULT_PERMISSION_NOTE)
    runbook.add_argument("--public-base-url", default="http://127.0.0.1:8765")
    runbook.add_argument("--audit-report", type=Path, default=Path("data/final-audit.json"))
    runbook.add_argument("--deploy-bundle-manifest", type=Path, default=DEFAULT_BUNDLE_DIR / BUNDLE_MANIFEST_NAME)
    runbook.add_argument("--prefetch-delay", type=int, default=30)
    runbook.add_argument("--out", type=Path, help="Write the runbook to this path.")
    runbook.set_defaults(func=cmd_runbook)

    author_review = subparsers.add_parser(
        "author-review",
        help="Render a no-story-text author review packet for the public-safe prototype.",
    )
    add_fetch_args(author_review)
    author_review.add_argument("url", nargs="?", default=TARGET_READER_URL)
    author_review.add_argument("--input", type=Path, default=DEFAULT_JSONL)
    author_review.add_argument("--db", type=Path, default=DEFAULT_DB)
    author_review.add_argument("--category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    author_review.add_argument("--category-name", default="Threadmarks")
    author_review.add_argument("--expected-threadmarks", type=int, default=269)
    author_review.add_argument("--expected-category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    author_review.add_argument("--excluded-categories", type=int, nargs="*", default=[4, 5])
    author_review.add_argument("--probe", action="append", default=[])
    author_review.add_argument("--artifact-manifest", type=Path, default=Path("dist/thread-search-public/manifest.json"))
    author_review.add_argument("--permission-note", type=Path, default=DEFAULT_PERMISSION_NOTE)
    author_review.add_argument("--deploy-bundle-manifest", type=Path, default=DEFAULT_BUNDLE_DIR / BUNDLE_MANIFEST_NAME)
    author_review.add_argument("--public-base-url", default="http://127.0.0.1:8765")
    author_review.add_argument("--out", type=Path, help="Write the author review packet to this path.")
    author_review.set_defaults(func=cmd_author_review)

    audit = subparsers.add_parser("audit", help="Print a completion and deployment readiness checklist.")
    add_fetch_args(audit)
    audit.add_argument("url", nargs="?", default=TARGET_READER_URL)
    audit.add_argument("--input", type=Path, default=DEFAULT_JSONL)
    audit.add_argument("--db", type=Path, default=DEFAULT_DB)
    audit.add_argument("--category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    audit.add_argument("--category-name", default="Threadmarks")
    audit.add_argument("--expected-threadmarks", type=int, default=269)
    audit.add_argument("--expected-category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    audit.add_argument("--excluded-categories", type=int, nargs="*", default=[4, 5])
    audit.add_argument("--probe", action="append", default=[])
    audit.add_argument("--artifact-manifest", type=Path)
    audit.add_argument("--permission-note", type=Path, default=DEFAULT_PERMISSION_NOTE)
    audit.add_argument("--public-base-url", help="Also run live public-smoke checks against this base URL.")
    audit.add_argument("--smoke-timeout", type=float, default=5.0)
    audit.add_argument("--json", action="store_true")
    audit.add_argument("--out", type=Path, help="Write the audit report to this path.")
    audit.set_defaults(func=cmd_audit)

    validate = subparsers.add_parser("validate", help="Validate extracted corpus and optional SQLite index.")
    validate.add_argument("--input", type=Path, default=DEFAULT_JSONL)
    validate.add_argument("--db", type=Path, default=DEFAULT_DB)
    validate.add_argument("--no-db", action="store_true", help="Only validate the extracted JSONL corpus.")
    validate.add_argument("--expected-threadmarks", type=int, default=269)
    validate.add_argument("--expected-category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    validate.add_argument("--excluded-categories", type=int, nargs="*", default=[4, 5])
    validate.add_argument("--probe", action="append", default=[], help="Require a search term to return a result.")
    validate.set_defaults(func=cmd_validate)

    launch = subparsers.add_parser("launch-check", help="Validate that a public source-linked search launch is ready.")
    launch.add_argument("--input", type=Path, default=DEFAULT_JSONL)
    launch.add_argument("--db", type=Path, default=DEFAULT_DB)
    launch.add_argument("--db-only", action="store_true", help="Validate only the SQLite deployment artifact.")
    launch.add_argument("--expected-threadmarks", type=int, default=269)
    launch.add_argument("--expected-category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    launch.add_argument("--excluded-categories", type=int, nargs="*", default=[4, 5])
    launch.add_argument("--probe", action="append", default=[], help="Require a search term to return a result.")
    launch.add_argument(
        "--private-fulltext",
        action="store_true",
        help="Fails the public launch check; included to catch unsafe serve settings.",
    )
    launch.set_defaults(func=cmd_launch_check)

    smoke = subparsers.add_parser("public-smoke", help="Smoke-test a running public source-linked search server.")
    smoke.add_argument("--base-url", default="http://127.0.0.1:8765")
    smoke.add_argument("--probe", action="append", default=[], help="Require a live search probe to return results.")
    smoke.add_argument(
        "--require-artifact-manifest",
        action="store_true",
        help="Require /api/stats to report that the server validated an artifact manifest at startup.",
    )
    smoke.add_argument("--timeout", type=float, default=5.0)
    smoke.add_argument("--json", action="store_true")
    smoke.set_defaults(func=cmd_public_smoke)

    preview_start = subparsers.add_parser(
        "preview-start",
        help="Start a manifest-gated local server and optional localtunnel URL for author review.",
    )
    preview_start.add_argument("--db", type=Path, default=Path("dist/thread-search-public/thread-search.sqlite"))
    preview_start.add_argument("--host", default="127.0.0.1")
    preview_start.add_argument("--port", type=int, default=8765)
    preview_start.add_argument("--artifact-manifest", type=Path, default=Path("dist/thread-search-public/manifest.json"))
    preview_start.add_argument("--probe", action="append", default=[])
    preview_start.add_argument(
        "--public-contact",
        default=os.environ.get("THREAD_SEARCH_PUBLIC_CONTACT") or os.environ.get("PLANQUEST_PUBLIC_CONTACT", ""),
        help="Public operator contact exposed in the preview stats/notice.",
    )
    preview_start.add_argument(
        "--removal-request-url",
        default=os.environ.get("THREAD_SEARCH_REMOVAL_REQUEST_URL")
        or os.environ.get("PLANQUEST_REMOVAL_REQUEST_URL", ""),
        help="Public removal/takedown URL or mailto link exposed in the preview stats/notice.",
    )
    preview_start.add_argument("--state", type=Path, default=DEFAULT_PREVIEW_STATE)
    preview_start.add_argument("--server-log", type=Path, default=DEFAULT_PREVIEW_SERVER_LOG)
    preview_start.add_argument("--tunnel-log", type=Path, default=DEFAULT_PREVIEW_TUNNEL_LOG)
    preview_start.add_argument("--timeout", type=float, default=20.0)
    preview_start.add_argument("--skip-server", action="store_true", help="Reuse an already-running loopback server.")
    preview_start.add_argument("--no-tunnel", action="store_true", help="Start only the local loopback preview.")
    preview_start.add_argument("--subdomain", help="Optional localtunnel subdomain request.")
    preview_start.add_argument("--force", action="store_true", help="Overwrite an existing running preview state.")
    preview_start.add_argument("--json", action="store_true")
    preview_start.set_defaults(func=cmd_preview_start)

    preview_status_parser = subparsers.add_parser(
        "preview-status",
        help="Report the recorded public preview URL and process state.",
    )
    preview_status_parser.add_argument("--state", type=Path, default=DEFAULT_PREVIEW_STATE)
    preview_status_parser.add_argument("--tunnel-log", type=Path, default=DEFAULT_PREVIEW_TUNNEL_LOG)
    preview_status_parser.add_argument("--smoke", action="store_true", help="Also run public-smoke against the preview URL.")
    preview_status_parser.add_argument("--probe", action="append", default=[])
    preview_status_parser.add_argument("--timeout", type=float, default=5.0)
    preview_status_parser.add_argument("--json", action="store_true")
    preview_status_parser.set_defaults(func=cmd_preview_status)

    preview_stop = subparsers.add_parser("preview-stop", help="Stop processes recorded by preview-start.")
    preview_stop.add_argument("--state", type=Path, default=DEFAULT_PREVIEW_STATE)
    preview_stop.add_argument("--json", action="store_true")
    preview_stop.set_defaults(func=cmd_preview_stop)

    artifact = subparsers.add_parser("artifact", help="Export a private backend artifact for public source-linked search.")
    artifact.add_argument("--db", type=Path, default=DEFAULT_DB)
    artifact.add_argument("--out-dir", type=Path, default=Path("dist/thread-search-public"))
    artifact.add_argument("--expected-threadmarks", type=int, default=269)
    artifact.add_argument("--expected-category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    artifact.add_argument("--excluded-categories", type=int, nargs="*", default=[4, 5])
    artifact.add_argument("--probe", action="append", default=[], help="Require a search term to return a result.")
    artifact.add_argument("--public-search-limit", type=int, default=30)
    artifact.add_argument("--public-threadmark-limit", type=int, default=300)
    artifact.add_argument("--max-query-chars", type=int, default=120)
    artifact.add_argument("--public-rate-limit-per-minute", type=int, default=60)
    artifact.add_argument(
        "--public-contact",
        default=os.environ.get("THREAD_SEARCH_PUBLIC_CONTACT") or os.environ.get("PLANQUEST_PUBLIC_CONTACT", ""),
        help="Optional public operator contact shown in stats/manifest for questions or removal requests.",
    )
    artifact.add_argument(
        "--removal-request-url",
        default=os.environ.get("THREAD_SEARCH_REMOVAL_REQUEST_URL")
        or os.environ.get("PLANQUEST_REMOVAL_REQUEST_URL", ""),
        help="Optional URL or mailto link for public removal/takedown requests.",
    )
    artifact.add_argument(
        "--allow-unsafe-public-caps",
        action="store_true",
        help="Allow disabled or unusually large public API caps in the exported deployment manifest.",
    )
    artifact.add_argument("--permission-note", type=Path, default=DEFAULT_PERMISSION_NOTE)
    artifact.set_defaults(func=cmd_artifact)

    deploy_bundle = subparsers.add_parser(
        "deploy-bundle",
        help="Create public app and private artifact tarballs for production handoff.",
    )
    deploy_bundle.add_argument("--artifact-dir", type=Path, default=Path("dist/thread-search-public"))
    deploy_bundle.add_argument("--out-dir", type=Path, default=DEFAULT_BUNDLE_DIR)
    deploy_bundle.add_argument("--expected-threadmarks", type=int, default=269)
    deploy_bundle.add_argument("--no-tests", action="store_true", help="Do not include tests in the public app bundle.")
    deploy_bundle.add_argument("--json", action="store_true")
    deploy_bundle.set_defaults(func=cmd_deploy_bundle)

    deploy_bundle_check = subparsers.add_parser(
        "deploy-bundle-check",
        help="Verify deployment bundle checksums, tarball contents, and public/private separation.",
    )
    deploy_bundle_check.add_argument("--manifest", type=Path, default=DEFAULT_BUNDLE_DIR / BUNDLE_MANIFEST_NAME)
    deploy_bundle_check.add_argument("--json", action="store_true")
    deploy_bundle_check.set_defaults(func=cmd_deploy_bundle_check)

    server = subparsers.add_parser("serve", help="Serve the local web search UI.")
    server.add_argument("--db", type=Path, default=DEFAULT_DB)
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument(
        "--private-fulltext",
        action="store_true",
        help="Enable local full-text threadmark pages. Keep off for public deployments.",
    )
    server.add_argument("--public-search-limit", type=int, default=30)
    server.add_argument("--public-threadmark-limit", type=int, default=300)
    server.add_argument("--max-query-chars", type=int, default=120)
    server.add_argument("--public-rate-limit-per-minute", type=int, default=60)
    server.add_argument(
        "--public-contact",
        default=os.environ.get("THREAD_SEARCH_PUBLIC_CONTACT") or os.environ.get("PLANQUEST_PUBLIC_CONTACT", ""),
        help="Optional public operator contact shown in the page notice and /api/stats.",
    )
    server.add_argument(
        "--removal-request-url",
        default=os.environ.get("THREAD_SEARCH_REMOVAL_REQUEST_URL")
        or os.environ.get("PLANQUEST_REMOVAL_REQUEST_URL", ""),
        help="Optional URL or mailto link for public removal/takedown requests.",
    )
    server.add_argument(
        "--require-launch-ready",
        action="store_true",
        help="Refuse to start unless public launch validation passes.",
    )
    server.add_argument(
        "--require-artifact-manifest",
        action="store_true",
        help="Refuse to start unless the adjacent artifact manifest validates with permission evidence.",
    )
    server.add_argument(
        "--artifact-manifest",
        type=Path,
        help="Artifact manifest to validate when --require-artifact-manifest is set. Defaults next to --db.",
    )
    server.add_argument("--expected-threadmarks", type=int, default=269)
    server.add_argument("--expected-category", type=int, default=MAIN_THREADMARK_CATEGORY_ID)
    server.add_argument("--excluded-categories", type=int, nargs="*", default=[4, 5])
    server.add_argument("--probe", action="append", default=[], help="Require a search term to return a result.")
    server.add_argument(
        "--allow-unguarded-public-bind",
        action="store_true",
        help="Allow binding a non-loopback host without --require-launch-ready. Not recommended.",
    )
    server.add_argument(
        "--allow-unmanifested-public-bind",
        action="store_true",
        help="Allow binding a non-loopback host without --require-artifact-manifest. Not recommended.",
    )
    server.add_argument(
        "--allow-public-fulltext",
        action="store_true",
        help="Allow --private-fulltext on a non-loopback host. Requires explicit redistribution permission.",
    )
    server.add_argument(
        "--allow-unsafe-public-caps",
        action="store_true",
        help="Allow disabled or unusually large public API caps on a non-loopback host. Not recommended.",
    )
    server.set_defaults(func=cmd_serve)

    return parser


def add_fetch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--delay", type=float, default=8.0, help="Seconds between network requests.")
    parser.add_argument("--retries", type=int, default=2, help="Retries for transient 429/5xx/network failures.")
    parser.add_argument("--retry-delay", type=float, default=30.0, help="Base seconds to wait between retries.")
    parser.add_argument("--refresh", action="store_true", help="Ignore cached pages and fetch again.")
    parser.add_argument("--offline", action="store_true", help="Use cached robots/page files only; never make network requests.")
    parser.add_argument("--user-agent", default=default_user_agent())


def make_fetcher(args: argparse.Namespace) -> PoliteFetcher:
    return PoliteFetcher(
        cache_dir=args.cache_dir,
        user_agent=args.user_agent,
        delay_seconds=args.delay,
        refresh=args.refresh,
        retries=args.retries,
        retry_delay_seconds=args.retry_delay,
        offline=args.offline,
    )


def cmd_scout(args: argparse.Namespace) -> int:
    fetcher = make_fetcher(args)
    reader_root = normalize_reader_root(args.url)
    allowed = fetcher.can_fetch(reader_root)
    print(f"robots: {'allowed' if allowed else 'blocked'}")
    print(f"robots_url: {fetcher.robots_url(reader_root)}")
    print(f"user_agent: {fetcher.user_agent}")
    if not allowed:
        return 2

    fetched = fetcher.fetch_text(reader_root)
    page_count = discover_page_count(fetched.text)
    print(f"reader_root: {reader_root}")
    print(f"reader_pages: {page_count}")
    print(f"from_cache: {fetched.from_cache}")
    print("categories:")
    for category in discover_categories(fetched.text):
        count = category["count"] if category["count"] is not None else "unknown"
        reader_url = category["reader_url"] or ""
        print(f"  - {category['id']}: {category['name']} ({count}) {reader_url}")
    return 0


def cmd_site_review(args: argparse.Namespace) -> int:
    fetcher = make_fetcher(args)
    review = make_site_policy_review(fetcher, url=args.url, category_id=args.category)
    rendered = (
        json.dumps(review.to_dict(), ensure_ascii=False, indent=2)
        if args.format == "json"
        else render_site_policy_review_markdown(review)
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
        print(f"wrote: {args.out}")
    else:
        print(rendered)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    fetcher = make_fetcher(args)
    reader_root = normalize_reader_root(args.url, category_id=args.category)
    if not fetcher.can_fetch(reader_root):
        raise RobotsDenied(f"robots.txt disallows {reader_root!r} for user agent {fetcher.user_agent!r}")

    fetched = fetcher.fetch_text(reader_root)
    plan = plan_reader_crawl(
        fetched.text,
        reader_root=reader_root,
        category_id=args.category,
        category_name=args.category_name,
        max_pages=args.max_pages,
    )
    payload = crawl_plan_payload(fetcher, plan.page_urls, plan.reader_root, plan.category_id, plan.category_name)
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote: {args.manifest}")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"reader_root: {payload['reader_root']}")
        print(f"category: {payload['category_id']} {payload['category_name']}")
        print(f"pages: {payload['page_count']}")
        print(f"cached_pages: {payload['cached_pages']}")
        print(f"network_pages_if_run_now: {payload['network_pages_if_run_now']}")
        print("urls:")
        for item in payload["pages"]:
            cached = "cached" if item["cached"] else "network"
            print(f"  {item['page']:>2}. {item['url']} ({cached})")
    return 0


def cmd_permission_note(args: argparse.Namespace) -> int:
    if args.check:
        summary = permission_note_summary(args.out)
        rendered = (
            json.dumps(summary, ensure_ascii=False, indent=2)
            if args.json
            else format_permission_note_summary(summary)
        )
        print(rendered)
        return 0 if summary.get("ok") is True else 1

    try:
        write_permission_note_template(args.out, overwrite=args.overwrite)
    except FileExistsError:
        print(f"permission-note: {args.out} already exists; use --overwrite to replace it", file=sys.stderr)
        return 1
    print(f"wrote: {args.out}")
    return 0


def cmd_permission_request(args: argparse.Namespace) -> int:
    if args.out is None:
        print(
            render_permission_request_template(
                source_reader_url=args.source_reader_url,
                public_base_url=args.public_base_url,
                operator=args.operator,
                contact=args.contact,
            )
        )
        return 0
    try:
        write_permission_request_template(
            args.out,
            overwrite=args.overwrite,
            source_reader_url=args.source_reader_url,
            public_base_url=args.public_base_url,
            operator=args.operator,
            contact=args.contact,
        )
    except FileExistsError:
        print(f"permission-request: {args.out} already exists; use --overwrite to replace it", file=sys.stderr)
        return 1
    print(f"wrote: {args.out}")
    return 0


def cmd_prefetch(args: argparse.Namespace) -> int:
    if args.limit < 0:
        print("prefetch: --limit must be zero or greater", file=sys.stderr)
        return 1
    if args.refresh and args.limit == 0:
        print("prefetch: --limit 0 cannot be combined with --refresh", file=sys.stderr)
        return 1

    fetcher = make_fetcher(args)
    reader_root = normalize_reader_root(args.url, category_id=args.category)
    if not fetcher.can_fetch(reader_root):
        raise RobotsDenied(f"robots.txt disallows {reader_root!r} for user agent {fetcher.user_agent!r}")

    if args.limit == 0 and not fetcher.is_cached(reader_root):
        print("prefetch: first reader page is not cached; cannot discover plan with --limit 0", file=sys.stderr)
        return 1

    first = fetcher.fetch_text(reader_root)
    plan = plan_reader_crawl(
        first.text,
        reader_root=reader_root,
        category_id=args.category,
        category_name=args.category_name,
    )
    try:
        selected_pages = select_page_urls(plan.page_urls, from_page=args.from_page, to_page=args.to_page)
    except ValueError as exc:
        print(f"prefetch: {exc}", file=sys.stderr)
        return 1

    network_pages = 0 if first.from_cache else 1
    fetched_pages: list[tuple[int, str, str]] = []
    if not first.from_cache:
        reason = "selected" if any(page == 1 for page, _url in selected_pages) else "planning"
        fetched_pages.append((1, reader_root, reason))

    for page, page_url in selected_pages:
        if page == 1:
            continue
        if fetcher.is_cached(page_url) and not args.refresh:
            continue
        if network_pages >= args.limit:
            continue
        fetched = fetcher.fetch_text(page_url)
        if not fetched.from_cache:
            network_pages += 1
            fetched_pages.append((page, page_url, "selected"))

    uncached_selected = [(page, url) for page, url in selected_pages if not fetcher.is_cached(url)]
    cached_selected = len(selected_pages) - len(uncached_selected)
    selected_label = page_selection_label(args.from_page, args.to_page, plan.page_count)

    print(f"reader_root: {reader_root}")
    print(f"category: {plan.category_id} {plan.category_name}")
    print(f"total_pages: {plan.page_count}")
    print(f"selected_pages: {selected_label}")
    print(f"network_limit: {args.limit}")
    print(f"network_pages: {network_pages}")
    print(f"cached_selected_pages: {cached_selected}")
    print(f"remaining_selected_uncached: {len(uncached_selected)}")
    if fetched_pages:
        print("fetched:")
        for page, page_url, reason in fetched_pages:
            print(f"  - {page:>2}. {page_url} ({reason})")
    if uncached_selected:
        next_page, next_url = uncached_selected[0]
        print(f"next_uncached: {next_page}. {next_url}")
    return 0


def cmd_scrape(args: argparse.Namespace) -> int:
    fetcher = make_fetcher(args)
    _records, stats = scrape_reader_with_stats(
        fetcher=fetcher,
        url=args.url,
        out_path=args.out,
        category_id=args.category,
        category_name=args.category_name,
        max_pages=args.max_pages,
        progress=None if args.quiet else print_scrape_progress,
    )
    print(f"wrote: {args.out}")
    print(f"threadmarks: {stats.threadmarks}")
    print(f"words: {stats.words}")
    print(f"pages: {stats.plan.page_count}")
    print(f"network_pages: {stats.network_pages}")
    print(f"cached_pages: {stats.cached_pages}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    fetcher = make_fetcher(args)
    _records, scrape_stats = scrape_reader_with_stats(
        fetcher=fetcher,
        url=args.url,
        out_path=args.out,
        category_id=args.category,
        category_name=args.category_name,
        max_pages=args.max_pages,
        progress=None if args.quiet else print_scrape_progress,
    )
    print(f"scrape_out: {args.out}")
    print(f"scrape_threadmarks: {scrape_stats.threadmarks}")
    print(f"scrape_words: {scrape_stats.words}")
    print(f"scrape_pages: {scrape_stats.plan.page_count}")
    print(f"scrape_network_pages: {scrape_stats.network_pages}")
    print(f"scrape_cached_pages: {scrape_stats.cached_pages}")

    records, chunks = build_index(args.out, args.db)
    print(f"index_db: {args.db}")
    print(f"index_threadmarks: {records}")
    print(f"index_chunks: {chunks}")

    probes = tuple(args.probe or DEFAULT_READINESS_PROBES)
    result = validate_corpus(
        jsonl_path=args.out,
        db_path=args.db,
        expected_threadmarks=args.expected_threadmarks,
        expected_category=args.expected_category,
        excluded_categories=tuple(args.excluded_categories),
        probes=probes,
    )
    for check in result.checks:
        print(f"ok: {check}")
    for error in result.errors:
        print(f"error: {error}", file=sys.stderr)
    if not result.ok:
        print("build: validation failed", file=sys.stderr)
        return 1
    print("build: passed")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    records, chunks = build_index(args.input, args.db)
    print(f"wrote: {args.db}")
    print(f"threadmarks: {records}")
    print(f"chunks: {chunks}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    results = search_db(
        args.db,
        args.query,
        limit=args.limit,
        mode=args.mode,
        order_min=args.order_min,
        order_max=args.order_max,
        grouped=not args.all_chunks,
        sort=args.sort,
    )
    if args.format == "json":
        totals = search_totals_db(
            args.db,
            args.query,
            mode=args.mode,
            order_min=args.order_min,
            order_max=args.order_max,
        )
        payload = {
            "query": args.query,
            "mode": args.mode,
            "sort": args.sort,
            "limit": args.limit,
            "grouped": not args.all_chunks,
            "order_min": args.order_min,
            "order_max": args.order_max,
            "match_kind": totals.match_kind,
            "match_query": totals.match_query,
            "result_count": len(results),
            "total_threadmarks": totals.total_threadmarks,
            "total_chunks": totals.total_chunks,
            "results": [asdict(result) for result in results],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    for index, result in enumerate(results, start=1):
        snippet = result.snippet.replace("\x01", "[").replace("\x02", "]")
        print(f"{index}. {result.title}")
        print(f"   {result.source_url}")
        print(f"   {snippet}")
    if not results:
        print("No results")
    return 0


def cmd_toc(args: argparse.Namespace) -> int:
    items = list_threadmarks_db(
        args.db,
        limit=args.limit,
        order_min=args.order_min,
        order_max=args.order_max,
    )
    if args.format == "json":
        print(json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2))
    elif args.format == "text":
        for item in items:
            date = f" {item.published_at}" if item.published_at else ""
            print(f"#{item.threadmark_order} {item.title}{date}")
            print(f"    {item.source_url}")
            print(f"    {item.word_count} words")
    else:
        print("# Threadmark Table Of Contents")
        print()
        for item in items:
            print(f"## #{item.threadmark_order} {item.title}")
            print()
            print(f"Source: {item.source_url}")
            print()
            print(f"Words: {item.word_count}")
            print()
    if not items:
        print("No threadmarks found", file=sys.stderr)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    probes = tuple(args.probe or DEFAULT_READINESS_PROBES)
    payload = make_status_payload(args, probes=probes)
    validation = payload["validation"]
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_status(payload)
    return 0 if validation["ok"] or not args.strict else 1


def make_status_payload(args: argparse.Namespace, probes: tuple[str, ...]) -> dict[str, object]:
    fetcher = make_fetcher(args)
    reader_root = normalize_reader_root(args.url, category_id=args.category)
    robots_allowed = fetcher.can_fetch(reader_root)
    crawl_payload: dict[str, object] = {
        "reader_root": reader_root,
        "robots_allowed": robots_allowed,
        "user_agent": fetcher.user_agent,
    }
    if robots_allowed:
        fetched = fetcher.fetch_text(reader_root)
        plan = plan_reader_crawl(
            fetched.text,
            reader_root=reader_root,
            category_id=args.category,
            category_name=args.category_name,
        )
        crawl_payload.update(crawl_plan_payload(fetcher, plan.page_urls, plan.reader_root, plan.category_id, plan.category_name))

    validation = validate_corpus(
        jsonl_path=args.input,
        db_path=args.db,
        expected_threadmarks=args.expected_threadmarks,
        expected_category=args.expected_category,
        excluded_categories=tuple(args.excluded_categories),
        probes=probes,
    )
    launch = validate_launch_ready(
        jsonl_path=args.input,
        db_path=args.db,
        expected_threadmarks=args.expected_threadmarks,
        expected_category=args.expected_category,
        excluded_categories=tuple(args.excluded_categories),
        probes=probes,
    )
    return {
        "crawl": crawl_payload,
        "corpus": corpus_summary(args.input),
        "index": db_summary(args.db),
        "fetch_log": fetch_log_summary(fetcher.receipt_log_path()),
        "validation": asdict(validation),
        "launch_check": asdict(launch),
    }


def cmd_audit(args: argparse.Namespace) -> int:
    probes = tuple(args.probe or DEFAULT_READINESS_PROBES)
    payload = make_status_payload(args, probes=probes)
    smoke_report = None
    if args.public_base_url:
        try:
            smoke_report = run_public_smoke(
                args.public_base_url,
                probes=probes,
                timeout=args.smoke_timeout,
                require_artifact_manifest=args.artifact_manifest is not None,
            ).to_dict()
        except ValueError as exc:
            print(f"audit: {exc}", file=sys.stderr)
            return 1
    report = evaluate_audit(
        payload,
        expected_threadmarks=args.expected_threadmarks,
        expected_category=args.expected_category,
        excluded_categories=tuple(args.excluded_categories),
        probes=probes,
        artifact_manifest=args.artifact_manifest,
        permission_note=args.permission_note,
        public_smoke_report=smoke_report,
    )
    rendered = (
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
        if args.json
        else format_audit_report(report.to_dict())
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
        print(f"wrote: {args.out}")
    else:
        print(rendered)
    return 0 if report.ok else 1


def cmd_next_step(args: argparse.Namespace) -> int:
    probes = tuple(args.probe or DEFAULT_READINESS_PROBES)
    payload = make_status_payload(args, probes=probes)
    step = recommend_next_step(
        payload,
        expected_threadmarks=args.expected_threadmarks,
        probes=probes,
        artifact_manifest=args.artifact_manifest,
        permission_note=args.permission_note,
        public_base_url=args.public_base_url,
        audit_report=args.audit_report,
        deploy_bundle_manifest=args.deploy_bundle_manifest,
        delay_seconds=args.prefetch_delay,
    )
    if args.json:
        print(json.dumps(step.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"next_step: {step.key}")
        print(f"summary: {step.summary}")
        if step.command:
            print(f"command: {step.command}")
        else:
            print("command: none")
        if step.reasons:
            print("reasons:")
            for reason in step.reasons:
                print(f"  - {reason}")
    return 0 if step.command is not None else 1


def cmd_runbook(args: argparse.Namespace) -> int:
    probes = tuple(args.probe or DEFAULT_READINESS_PROBES)
    payload = make_status_payload(args, probes=probes)
    step = recommend_next_step(
        payload,
        expected_threadmarks=args.expected_threadmarks,
        probes=probes,
        artifact_manifest=args.artifact_manifest,
        permission_note=args.permission_note,
        public_base_url=args.public_base_url,
        audit_report=args.audit_report,
        deploy_bundle_manifest=args.deploy_bundle_manifest,
        delay_seconds=args.prefetch_delay,
    )
    rendered = render_runbook(
        payload,
        step,
        expected_threadmarks=args.expected_threadmarks,
        probes=probes,
        artifact_manifest=args.artifact_manifest,
        permission_note=args.permission_note,
        public_base_url=args.public_base_url,
        audit_report=args.audit_report,
        deploy_bundle_manifest=args.deploy_bundle_manifest,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
        print(f"wrote: {args.out}")
    else:
        print(rendered)
    return 0


def cmd_author_review(args: argparse.Namespace) -> int:
    probes = tuple(args.probe or DEFAULT_READINESS_PROBES)
    payload = make_status_payload(args, probes=probes)
    rendered = render_author_review_packet(
        payload,
        public_base_url=args.public_base_url,
        probes=probes,
        artifact_manifest=args.artifact_manifest,
        permission_note=args.permission_note,
        deploy_bundle_manifest=args.deploy_bundle_manifest,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
        print(f"wrote: {args.out}")
    else:
        print(rendered)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    result = validate_corpus(
        jsonl_path=args.input,
        db_path=None if args.no_db else args.db,
        expected_threadmarks=args.expected_threadmarks,
        expected_category=args.expected_category,
        excluded_categories=tuple(args.excluded_categories),
        probes=tuple(args.probe),
    )
    for check in result.checks:
        print(f"ok: {check}")
    for error in result.errors:
        print(f"error: {error}", file=sys.stderr)
    if result.ok:
        print("validation: passed")
        return 0
    print("validation: failed", file=sys.stderr)
    return 1


def cmd_launch_check(args: argparse.Namespace) -> int:
    result = validate_launch_ready(
        jsonl_path=args.input,
        db_path=args.db,
        expected_threadmarks=args.expected_threadmarks,
        expected_category=args.expected_category,
        excluded_categories=tuple(args.excluded_categories),
        probes=tuple(args.probe or DEFAULT_READINESS_PROBES),
        private_fulltext=args.private_fulltext,
        db_only=args.db_only,
    )
    for check in result.checks:
        print(f"ok: {check}")
    for error in result.errors:
        print(f"error: {error}", file=sys.stderr)
    if result.ok:
        print("launch-check: passed")
        return 0
    print("launch-check: failed", file=sys.stderr)
    return 1


def cmd_public_smoke(args: argparse.Namespace) -> int:
    probes = tuple(args.probe or DEFAULT_READINESS_PROBES)
    try:
        report = run_public_smoke(
            args.base_url,
            probes=probes,
            timeout=args.timeout,
            require_artifact_manifest=args.require_artifact_manifest,
        )
    except ValueError as exc:
        print(f"public-smoke: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0 if report.ok else 1
    print(format_public_smoke_report(report.to_dict()))
    return 0 if report.ok else 1


def cmd_preview_start(args: argparse.Namespace) -> int:
    contact_errors = public_contact_errors(
        args.public_contact,
        args.removal_request_url,
        context="public preview",
    )
    if contact_errors:
        for error in contact_errors:
            print(f"preview-start: {error}", file=sys.stderr)
        return 1

    probes = tuple(args.probe or DEFAULT_READINESS_PROBES)
    try:
        state = start_public_preview(
            db=args.db,
            host=args.host,
            port=args.port,
            artifact_manifest=args.artifact_manifest,
            public_contact=args.public_contact,
            removal_request_url=args.removal_request_url,
            probes=probes,
            state_path=args.state,
            server_log=args.server_log,
            tunnel_log=args.tunnel_log,
            timeout_seconds=args.timeout,
            skip_server=args.skip_server,
            skip_tunnel=args.no_tunnel,
            force=args.force,
            subdomain=args.subdomain,
        )
    except PreviewError as exc:
        print(f"preview-start: {exc}", file=sys.stderr)
        return 1

    payload = state.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_preview_state(payload))
        if state.public_base_url:
            probe_args = " ".join(f"--probe {probe}" for probe in probes)
            print(
                "audit_command: "
                f".venv/bin/thread-search audit {probe_args} "
                f"--artifact-manifest {args.artifact_manifest} --permission-note {DEFAULT_PERMISSION_NOTE} "
                f"--public-base-url {state.public_base_url} "
                "--json --out data/public-preview-audit.json"
            )
    return 0


def cmd_preview_status(args: argparse.Namespace) -> int:
    status = preview_status(args.state, args.tunnel_log)
    smoke_report = None
    if args.smoke:
        public_url = status.get("public_base_url")
        if not public_url:
            print("preview-status: no public preview URL is recorded", file=sys.stderr)
            return 1
        try:
            smoke_report = run_public_smoke(
                str(public_url),
                probes=tuple(args.probe or DEFAULT_READINESS_PROBES),
                timeout=args.timeout,
                require_artifact_manifest=True,
            )
        except ValueError as exc:
            print(f"preview-status: {exc}", file=sys.stderr)
            return 1
        status["smoke"] = smoke_report.to_dict()

    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print(format_preview_status(status))
        if smoke_report is not None:
            print("")
            print(format_public_smoke_report(smoke_report.to_dict()))
    return 0 if smoke_report is None or smoke_report.ok else 1


def cmd_preview_stop(args: argparse.Namespace) -> int:
    result = stop_public_preview(args.state)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"state_path: {result['state_path']}")
        if not result["state_exists"]:
            print("state: missing")
            return 0
        for item in result["stopped"]:
            signal_label = item["signal_sent"] or "none"
            print(f"{item['name']}: pid={item['pid']} running_before={item['running_before']} signal={signal_label}")
    return 0


def cmd_artifact(args: argparse.Namespace) -> int:
    probes = tuple(args.probe or DEFAULT_READINESS_PROBES)
    cap_errors = public_cap_errors(vars(args))
    if cap_errors and not args.allow_unsafe_public_caps:
        for error in cap_errors:
            print(f"artifact: {error}", file=sys.stderr)
        print("artifact: use --allow-unsafe-public-caps only if this is a deliberate deployment decision", file=sys.stderr)
        return 1

    try:
        result = export_public_artifact(
            db_path=args.db,
            out_dir=args.out_dir,
            expected_threadmarks=args.expected_threadmarks,
            expected_category=args.expected_category,
            excluded_categories=tuple(args.excluded_categories),
            probes=probes,
            public_search_limit=args.public_search_limit,
            public_threadmark_limit=args.public_threadmark_limit,
            max_query_chars=args.max_query_chars,
            public_rate_limit_per_minute=args.public_rate_limit_per_minute,
            allow_unsafe_public_caps=args.allow_unsafe_public_caps,
            permission_note=args.permission_note,
            public_contact=args.public_contact,
            removal_request_url=args.removal_request_url,
        )
    except ArtifactValidationError as exc:
        for check in exc.result.checks:
            print(f"ok: {check}")
        for error in exc.result.errors:
            print(f"error: {error}", file=sys.stderr)
        print("artifact: validation failed", file=sys.stderr)
        return 1
    except ArtifactPermissionError as exc:
        print(f"artifact: permission note is incomplete or invalid: {exc.path}", file=sys.stderr)
        print(format_permission_note_summary(exc.summary), file=sys.stderr)
        print(f"artifact: run .venv/bin/thread-search permission-note --check --out {exc.path}", file=sys.stderr)
        return 1
    except ArtifactCapError as exc:
        for error in exc.errors:
            print(f"artifact: {error}", file=sys.stderr)
        print("artifact: use --allow-unsafe-public-caps only if this is a deliberate deployment decision", file=sys.stderr)
        return 1
    except ArtifactContactError as exc:
        for error in exc.errors:
            print(f"artifact: {error}", file=sys.stderr)
        print("artifact: set --public-contact and --removal-request-url before exporting", file=sys.stderr)
        return 1
    except ArtifactError as exc:
        print(f"artifact: {exc}", file=sys.stderr)
        return 1

    print(f"artifact_dir: {result.output_dir}")
    print(f"database: {result.database_path}")
    print(f"manifest: {result.manifest_path}")
    print(f"readme: {result.readme_path}")
    print(f"sha256: {result.sha256}")
    print(f"size_bytes: {result.size_bytes}")
    return 0


def cmd_deploy_bundle(args: argparse.Namespace) -> int:
    try:
        result = create_deploy_bundle(
            artifact_dir=args.artifact_dir,
            out_dir=args.out_dir,
            expected_threadmarks=args.expected_threadmarks,
            include_tests=not args.no_tests,
        )
    except DeployBundleError as exc:
        print(f"deploy-bundle: {exc}", file=sys.stderr)
        return 1

    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"bundle_dir: {result.output_dir}")
        print(f"app_bundle: {result.app_bundle.path}")
        print(f"app_sha256: {result.app_bundle.sha256}")
        print(f"app_size_bytes: {result.app_bundle.size_bytes}")
        print(f"private_artifact_bundle: {result.private_artifact_bundle.path}")
        print(f"private_artifact_sha256: {result.private_artifact_bundle.sha256}")
        print(f"private_artifact_size_bytes: {result.private_artifact_bundle.size_bytes}")
        print(f"manifest: {result.manifest_path}")
        print("warning: private_artifact_bundle contains the server-side full-text SQLite index; do not publish it.")
    return 0


def cmd_deploy_bundle_check(args: argparse.Namespace) -> int:
    result = verify_deploy_bundle(args.manifest)
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"deploy-bundle-check: {'passed' if result.ok else 'failed'}")
        print(f"manifest: {result.manifest_path}")
        for check in result.checks:
            print(f"ok: {check}")
        for error in result.errors:
            print(f"error: {error}")
    return 0 if result.ok else 1


def cmd_serve(args: argparse.Namespace) -> int:
    serve_errors = public_serve_safety_errors(args)
    if serve_errors:
        for error in serve_errors:
            print(f"serve: {error}", file=sys.stderr)
        return 1

    artifact_manifest_validated = False
    artifact_fingerprints: dict[str, str] = {}
    if args.require_launch_ready:
        result = validate_launch_ready(
            jsonl_path=DEFAULT_JSONL,
            db_path=args.db,
            expected_threadmarks=args.expected_threadmarks,
            expected_category=args.expected_category,
            excluded_categories=tuple(args.excluded_categories),
            probes=tuple(args.probe or DEFAULT_READINESS_PROBES),
            private_fulltext=args.private_fulltext,
            db_only=True,
        )
        for check in result.checks:
            print(f"ok: {check}")
        for error in result.errors:
            print(f"error: {error}", file=sys.stderr)
        if not result.ok:
            print("serve: launch readiness check failed", file=sys.stderr)
            return 1

    if args.require_artifact_manifest:
        manifest_errors = validate_serve_artifact_manifest(
            args.db,
            args.artifact_manifest,
            args.expected_threadmarks,
            args,
        )
        if manifest_errors:
            for error in manifest_errors:
                print(f"serve: {error}", file=sys.stderr)
            return 1
        manifest_path = args.artifact_manifest or args.db.with_name("manifest.json")
        print(f"ok: artifact manifest validated: {manifest_path}")
        artifact_manifest_validated = True
        artifact_fingerprints = read_serve_artifact_fingerprints(manifest_path)

    serve(
        args.db,
        args.host,
        args.port,
        private_fulltext=args.private_fulltext,
        public_search_limit=args.public_search_limit,
        public_threadmark_limit=args.public_threadmark_limit,
        max_query_chars=args.max_query_chars,
        public_rate_limit_per_minute=args.public_rate_limit_per_minute,
        public_contact=args.public_contact,
        removal_request_url=args.removal_request_url,
        artifact_manifest_validated=artifact_manifest_validated,
        artifact_manifest_sha256=artifact_fingerprints.get("artifact_manifest_sha256", ""),
        artifact_database_sha256=artifact_fingerprints.get("artifact_database_sha256", ""),
        artifact_created_at_utc=artifact_fingerprints.get("artifact_created_at_utc", ""),
    )
    return 0


def read_serve_artifact_fingerprints(manifest_path: Path) -> dict[str, str]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    database = manifest.get("database") if isinstance(manifest.get("database"), dict) else {}
    return {
        "artifact_manifest_sha256": sha256_file(manifest_path),
        "artifact_database_sha256": str(database.get("sha256") or ""),
        "artifact_created_at_utc": str(manifest.get("created_at_utc") or ""),
    }


def validate_serve_artifact_manifest(
    db_path: Path,
    manifest_path: Path | None,
    expected_threadmarks: int,
    serve_args: argparse.Namespace | None = None,
) -> list[str]:
    manifest_path = manifest_path or db_path.with_name("manifest.json")
    expected_db = manifest_path.parent / ARTIFACT_DB_NAME
    if db_path.resolve() != expected_db.resolve():
        return [
            "artifact manifest must be adjacent to the served artifact database; "
            f"expected db {expected_db}, got {db_path}"
        ]

    item = artifact_item(manifest_path, expected_threadmarks)
    if item.status != "pass":
        details = json.dumps(item.evidence, ensure_ascii=False, sort_keys=True)
        return [f"{item.summary} evidence={details}"]
    if serve_args is None:
        return []
    return serve_runtime_contract_errors(manifest_path, serve_args)


def serve_runtime_contract_errors(manifest_path: Path, serve_args: argparse.Namespace) -> list[str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    defaults = manifest.get("public_server_defaults", {})
    errors: list[str] = []

    if defaults.get("private_fulltext") is False and serve_args.private_fulltext:
        errors.append("artifact manifest requires --private-fulltext to remain disabled")

    for name in PUBLIC_CAP_LIMITS:
        if not hasattr(serve_args, name):
            continue
        manifest_value = parse_int_value(defaults.get(name))
        runtime_value = parse_int_value(getattr(serve_args, name))
        if manifest_value is None or runtime_value is None:
            continue
        if runtime_value > manifest_value:
            errors.append(
                f"{name.replace('_', '-')} exceeds artifact manifest default {manifest_value}; got {runtime_value}"
            )
    return errors


def parse_int_value(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def public_serve_safety_errors(args: argparse.Namespace) -> list[str]:
    if is_loopback_bind_host(args.host):
        return []

    errors: list[str] = []
    if not args.require_launch_ready and not args.allow_unguarded_public_bind:
        errors.append(
            "refusing to bind a non-loopback host without --require-launch-ready; "
            "use --allow-unguarded-public-bind only for a deliberate private-network override"
        )
    if not args.require_artifact_manifest and not args.allow_unmanifested_public_bind:
        errors.append(
            "refusing to bind a non-loopback host without --require-artifact-manifest; "
            "use --allow-unmanifested-public-bind only for a deliberate private-network override"
        )
    if args.private_fulltext and not args.allow_public_fulltext:
        errors.append(
            "refusing to expose --private-fulltext on a non-loopback host; "
            "use --allow-public-fulltext only if redistribution permission explicitly covers full text"
        )
    if not args.allow_unsafe_public_caps:
        errors.extend(public_cap_errors(vars(args)))
    errors.extend(
        public_contact_errors(
            args.public_contact,
            args.removal_request_url,
            context="non-loopback serving",
        )
    )
    return errors


def is_loopback_bind_host(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    if not normalized:
        return False
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def crawl_plan_payload(
    fetcher: PoliteFetcher,
    page_urls: list[str],
    reader_root: str,
    category_id: int,
    category_name: str,
) -> dict[str, object]:
    pages = [
        {"page": index, "url": url, "cached": fetcher.is_cached(url)}
        for index, url in enumerate(page_urls, start=1)
    ]
    cached_pages = sum(1 for page in pages if page["cached"])
    return {
        "reader_root": reader_root,
        "category_id": category_id,
        "category_name": category_name,
        "page_count": len(page_urls),
        "cached_pages": cached_pages,
        "network_pages_if_run_now": len(page_urls) - cached_pages,
        "pages": pages,
    }


def page_selection_label(from_page: int, to_page: int | None, total_pages: int) -> str:
    if to_page is None:
        return f"{from_page}-{total_pages}"
    return f"{from_page}-{min(to_page, total_pages)}"


def print_scrape_progress(page: int, total: int, url: str, from_cache: bool, records: int) -> None:
    source = "cache" if from_cache else "network"
    print(f"[{page}/{total}] {source}: {records} threadmarks <- {url}")


def print_status(payload: dict[str, object]) -> None:
    crawl = payload["crawl"]
    corpus = payload["corpus"]
    index = payload["index"]
    fetch_log = payload["fetch_log"]
    validation = payload["validation"]
    launch = payload["launch_check"]

    print("crawl:")
    print(f"  robots_allowed: {crawl.get('robots_allowed')}")
    print(f"  reader_root: {crawl.get('reader_root')}")
    if "page_count" in crawl:
        print(f"  pages: {crawl['page_count']}")
        print(f"  cached_pages: {crawl['cached_pages']}")
        print(f"  network_pages_if_run_now: {crawl['network_pages_if_run_now']}")

    print("corpus:")
    if corpus.get("exists"):
        print(f"  path: {corpus['path']}")
        print(f"  threadmarks: {corpus.get('threadmarks')}")
        print(f"  words: {corpus.get('words')}")
        print(f"  categories: {corpus.get('categories')}")
    else:
        print(f"  missing: {corpus['path']}")

    print("index:")
    if index.get("exists"):
        print(f"  path: {index['path']}")
        print(f"  threadmarks: {index.get('threadmarks')}")
        print(f"  chunks: {index.get('chunks')}")
        print(f"  words: {index.get('words')}")
        print(f"  categories: {index.get('categories')}")
    else:
        print(f"  missing: {index['path']}")

    print("fetch_log:")
    if fetch_log.get("exists"):
        print(f"  path: {fetch_log['path']}")
        print(f"  entries: {fetch_log.get('entries')}")
        print(f"  page_fetches: {fetch_log.get('page_fetches')}")
        print(f"  robots_fetches: {fetch_log.get('robots_fetches')}")
        print(f"  bytes: {fetch_log.get('bytes')}")
    else:
        print(f"  missing: {fetch_log['path']}")

    print(f"validation: {'passed' if validation['ok'] else 'failed'}")
    for error in validation["errors"]:
        print(f"  error: {error}")
    print(f"launch_check: {'passed' if launch['ok'] else 'failed'}")
    for error in launch["errors"]:
        print(f"  error: {error}")


def format_preview_state(state: dict[str, object]) -> str:
    lines = [
        "preview: started",
        f"local_base_url: {state.get('local_base_url')}",
        f"public_base_url: {state.get('public_base_url') or 'none'}",
        f"server_pid: {state.get('server_pid') or 'none'}",
        f"tunnel_pid: {state.get('tunnel_pid') or 'none'}",
        f"server_log: {state.get('server_log')}",
        f"tunnel_log: {state.get('tunnel_log')}",
    ]
    return "\n".join(lines)


def format_preview_status(status: dict[str, object]) -> str:
    lines = [
        "preview_status:",
        f"  state_path: {status.get('state_path')}",
        f"  state_exists: {status.get('state_exists')}",
        f"  started_at_utc: {status.get('started_at_utc')}",
        f"  local_base_url: {status.get('local_base_url')}",
        f"  public_base_url: {status.get('public_base_url')}",
        f"  server_pid: {status.get('server_pid')}",
        f"  server_running: {status.get('server_running')}",
        f"  tunnel_pid: {status.get('tunnel_pid')}",
        f"  tunnel_running: {status.get('tunnel_running')}",
        f"  server_log: {status.get('server_log')}",
        f"  tunnel_log: {status.get('tunnel_log')}",
    ]
    return "\n".join(lines)


def format_audit_report(report: dict[str, object]) -> str:
    lines = [f"audit: {'passed' if report['ok'] else 'failed'}", f"generated_at_utc: {report['generated_at_utc']}"]
    for item in report["items"]:
        status = item["status"]
        lines.append(f"{status}: {item['key']} - {item['summary']}")
        evidence = item.get("evidence", {})
        for key in interesting_evidence_keys(item["key"]):
            if key in evidence and evidence[key] not in (None, [], {}):
                lines.append(f"  {key}: {evidence[key]}")
        errors = evidence.get("errors")
        if errors:
            for error in errors:
                lines.append(f"  error: {error}")
    return "\n".join(lines)


def format_public_smoke_report(report: dict[str, object]) -> str:
    lines = [f"public-smoke: {'passed' if report['ok'] else 'failed'}", f"base_url: {report['base_url']}"]
    for item in report["items"]:
        status = item["status"]
        lines.append(f"{status}: {item['key']} - {item['summary']}")
        evidence = item.get("evidence", {})
        for key in (
            "status",
            "query",
            "result_count",
            "total_threadmarks",
            "private_fulltext",
            "artifact_manifest_validated",
            "artifact_manifest_sha256",
            "artifact_database_sha256",
            "artifact_created_at_utc",
            "require_artifact_manifest",
            "public_contact",
            "removal_request_url",
            "evidence_proximity_ok",
            "exposed_paths",
        ):
            if key in evidence and evidence[key] not in (None, [], {}):
                lines.append(f"  {key}: {evidence[key]}")
        forbidden = evidence.get("forbidden_keys")
        if forbidden:
            lines.append(f"  forbidden_keys: {forbidden}")
    return "\n".join(lines)


def format_permission_note_summary(summary: dict[str, object]) -> str:
    lines = [
        f"permission_note: {'passed' if summary.get('ok') is True else 'failed'}",
        f"path: {summary.get('path')}",
        f"exists: {summary.get('exists')}",
    ]
    if summary.get("sha256"):
        lines.append(f"sha256: {summary['sha256']}")
    if summary.get("bytes"):
        lines.append(f"bytes: {summary['bytes']}")
    missing = summary.get("missing_sections") or []
    if missing:
        lines.append(f"missing_sections: {missing}")
    missing_required = summary.get("missing_required_items") or []
    if missing_required:
        lines.append("missing_required_items:")
        for item in missing_required:
            lines.append(f"  - {item}")
    placeholders = summary.get("placeholders") or []
    if placeholders:
        lines.append(f"placeholders: {placeholders}")
    unchecked_checkboxes = summary.get("unchecked_checkboxes") or 0
    if unchecked_checkboxes:
        lines.append(f"unchecked_checkboxes: {unchecked_checkboxes}")
    unchecked_items = summary.get("unchecked_items") or []
    if unchecked_items:
        lines.append("unchecked_items:")
        for item in unchecked_items:
            lines.append(f"  - {item}")
    invalid_details = summary.get("invalid_checklist_details") or []
    if invalid_details:
        lines.append("invalid_checklist_details:")
        for item in invalid_details:
            if isinstance(item, dict):
                lines.append(f"  - {item.get('label')}: {item.get('reason')} ({item.get('detail')})")
            else:
                lines.append(f"  - {item}")
    deployment_decision = summary.get("deployment_decision")
    if isinstance(deployment_decision, dict) and deployment_decision.get("ok") is not True:
        lines.append("deployment_decision:")
        lines.append(f"  reason: {deployment_decision.get('reason')}")
        detail = deployment_decision.get("detail")
        if detail:
            lines.append(f"  detail: {detail}")
    if summary.get("error"):
        lines.append(f"error: {summary['error']}")
    return "\n".join(lines)


def interesting_evidence_keys(item_key: str) -> tuple[str, ...]:
    keys = {
        "robots_allowed": ("reader_root", "robots_allowed", "user_agent"),
        "fetch_receipts": ("path", "entries", "page_fetches", "robots_fetches", "bytes"),
        "reader_plan": ("page_count",),
        "cache_progress": ("cached_pages", "page_count", "network_pages_if_run_now"),
        "corpus_size": ("path", "threadmarks", "expected_threadmarks", "words"),
        "category_scope": ("categories", "expected_category", "excluded_present"),
        "sqlite_index": ("path", "threadmarks", "corpus_threadmarks", "chunks", "stored_chunks"),
        "validation": (),
        "probe_searches": ("probes", "checks"),
        "public_launch": (),
        "permission_note": (
            "path",
            "required",
            "provided",
            "exists",
            "ok",
            "sha256",
            "missing_sections",
            "missing_required_items",
            "placeholders",
            "unchecked_checkboxes",
            "unchecked_items",
            "invalid_checklist_details",
            "deployment_decision",
        ),
        "artifact_manifest": (
            "path",
            "artifact",
            "artifact_directory",
            "index_threadmarks",
            "expected_threadmarks",
            "validation_ok",
            "permission_note_ok",
            "permission_note_sha256",
        ),
        "public_smoke": ("base_url", "ok", "item_count", "failed_items"),
    }
    return keys.get(item_key, ())


if __name__ == "__main__":
    raise SystemExit(main())
