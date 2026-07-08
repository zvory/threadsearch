from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from .artifact import (
    ALLOWED_ARTIFACT_FILES,
    ARTIFACT_DB_NAME,
    DEPLOYMENT_RUNTIME_CONTRACT,
    PUBLIC_API_ENDPOINTS,
    sha256_file,
)
from .deploy_policy import public_cap_errors, public_contact_errors
from .permission import permission_note_summary


@dataclass(frozen=True)
class AuditItem:
    key: str
    status: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditReport:
    ok: bool
    generated_at_utc: str
    items: list[AuditItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_audit(
    payload: dict[str, Any],
    *,
    expected_threadmarks: int,
    expected_category: int,
    excluded_categories: tuple[int, ...],
    probes: tuple[str, ...],
    artifact_manifest: Path | None = None,
    permission_note: Path | None = None,
    public_smoke_report: dict[str, Any] | None = None,
) -> AuditReport:
    crawl = payload["crawl"]
    corpus = payload["corpus"]
    index = payload["index"]
    fetch_log = payload.get("fetch_log", {"exists": False})
    validation = payload["validation"]
    launch = payload["launch_check"]

    items = [
        robots_item(crawl),
        fetch_receipts_item(fetch_log),
        reader_plan_item(crawl),
        cache_progress_item(crawl),
        corpus_item(corpus, expected_threadmarks),
        category_scope_item(corpus, expected_category, excluded_categories),
        index_item(index, corpus),
        validation_item(validation),
        probe_item(validation, probes),
        launch_item(launch),
    ]
    if permission_note is not None:
        items.append(permission_note_item(permission_note, required=artifact_manifest is not None))
    if artifact_manifest is not None:
        items.append(artifact_item(artifact_manifest, expected_threadmarks))
    if public_smoke_report is not None:
        items.append(public_smoke_item(public_smoke_report))

    return AuditReport(
        ok=not any(item.status == "fail" for item in items),
        generated_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        items=items,
    )


def robots_item(crawl: dict[str, Any]) -> AuditItem:
    allowed = crawl.get("robots_allowed") is True
    return AuditItem(
        key="robots_allowed",
        status="pass" if allowed else "fail",
        summary="Reader URL is allowed by cached/current robots policy." if allowed else "Reader URL is not allowed.",
        evidence={
            "reader_root": crawl.get("reader_root"),
            "robots_allowed": crawl.get("robots_allowed"),
            "user_agent": crawl.get("user_agent"),
        },
    )


def fetch_receipts_item(fetch_log: dict[str, Any]) -> AuditItem:
    exists = fetch_log.get("exists") is True and fetch_log.get("ok", True) is True
    entries = int(fetch_log.get("entries") or 0)
    return AuditItem(
        key="fetch_receipts",
        status="pass" if exists and entries > 0 else "warn",
        summary=(
            f"Fetch receipt log contains {entries} network receipt(s)."
            if exists
            else "Fetch receipt log is not present yet."
        ),
        evidence={
            "path": fetch_log.get("path"),
            "exists": fetch_log.get("exists"),
            "entries": fetch_log.get("entries"),
            "page_fetches": fetch_log.get("page_fetches"),
            "robots_fetches": fetch_log.get("robots_fetches"),
            "bytes": fetch_log.get("bytes"),
            "first": fetch_log.get("first"),
            "last": fetch_log.get("last"),
        },
    )


def reader_plan_item(crawl: dict[str, Any]) -> AuditItem:
    pages = int(crawl.get("page_count") or 0)
    return AuditItem(
        key="reader_plan",
        status="pass" if pages > 0 else "fail",
        summary=f"Reader plan covers {pages} page(s)." if pages else "Reader page plan is unavailable.",
        evidence={"page_count": pages},
    )


def cache_progress_item(crawl: dict[str, Any]) -> AuditItem:
    pages = int(crawl.get("page_count") or 0)
    cached = int(crawl.get("cached_pages") or 0)
    remaining = int(crawl.get("network_pages_if_run_now") or 0)
    complete = pages > 0 and cached == pages and remaining == 0
    status = "pass" if complete else "warn"
    return AuditItem(
        key="cache_progress",
        status=status,
        summary=(
            "All planned reader pages are cached."
            if complete
            else f"{cached}/{pages} planned reader page(s) cached; {remaining} would require network."
        ),
        evidence={
            "page_count": pages,
            "cached_pages": cached,
            "network_pages_if_run_now": remaining,
        },
    )


def corpus_item(corpus: dict[str, Any], expected_threadmarks: int) -> AuditItem:
    exists = corpus.get("exists") is True and corpus.get("ok", True) is True
    count = int(corpus.get("threadmarks") or 0)
    passed = exists and count == expected_threadmarks
    return AuditItem(
        key="corpus_size",
        status="pass" if passed else "fail",
        summary=(
            f"Extracted corpus has expected {expected_threadmarks} threadmark(s)."
            if passed
            else f"Extracted corpus has {count} of {expected_threadmarks} expected threadmark(s)."
        ),
        evidence={
            "path": corpus.get("path"),
            "exists": corpus.get("exists"),
            "threadmarks": count,
            "expected_threadmarks": expected_threadmarks,
            "words": corpus.get("words"),
        },
    )


def category_scope_item(
    corpus: dict[str, Any],
    expected_category: int,
    excluded_categories: tuple[int, ...],
) -> AuditItem:
    categories = [int(category) for category in corpus.get("categories") or []]
    excluded_present = [category for category in categories if category in excluded_categories]
    passed = categories == [expected_category] and not excluded_present
    return AuditItem(
        key="category_scope",
        status="pass" if passed else "fail",
        summary=(
            f"Corpus contains only category {expected_category}; excluded categories absent."
            if passed
            else f"Corpus categories are {categories}; expected only {expected_category}."
        ),
        evidence={
            "categories": categories,
            "expected_category": expected_category,
            "excluded_categories": list(excluded_categories),
            "excluded_present": excluded_present,
        },
    )


def index_item(index: dict[str, Any], corpus: dict[str, Any]) -> AuditItem:
    exists = index.get("exists") is True and index.get("ok", True) is True
    index_threadmarks = int(index.get("threadmarks") or 0)
    corpus_threadmarks = int(corpus.get("threadmarks") or 0)
    chunks = int(index.get("chunks") or 0)
    stored_chunks = int(index.get("stored_chunks") or 0)
    passed = exists and index_threadmarks == corpus_threadmarks and chunks >= index_threadmarks and chunks == stored_chunks
    return AuditItem(
        key="sqlite_index",
        status="pass" if passed else "fail",
        summary=(
            "SQLite FTS index matches the extracted corpus."
            if passed
            else "SQLite FTS index is missing or does not match the extracted corpus."
        ),
        evidence={
            "path": index.get("path"),
            "exists": index.get("exists"),
            "threadmarks": index_threadmarks,
            "corpus_threadmarks": corpus_threadmarks,
            "chunks": chunks,
            "stored_chunks": stored_chunks,
        },
    )


def validation_item(validation: dict[str, Any]) -> AuditItem:
    passed = validation.get("ok") is True
    return AuditItem(
        key="validation",
        status="pass" if passed else "fail",
        summary="Corpus validation passes." if passed else "Corpus validation fails.",
        evidence={
            "checks": validation.get("checks", []),
            "errors": validation.get("errors", []),
        },
    )


def probe_item(validation: dict[str, Any], probes: tuple[str, ...]) -> AuditItem:
    errors = [error for error in validation.get("errors", []) if "probe search returned no results" in error]
    passed = not errors
    return AuditItem(
        key="probe_searches",
        status="pass" if passed else "fail",
        summary="Required probe searches return results." if passed else "One or more required probe searches failed.",
        evidence={
            "probes": list(probes),
            "checks": [check for check in validation.get("checks", []) if check.startswith("probe ")],
            "errors": errors,
        },
    )


def launch_item(launch: dict[str, Any]) -> AuditItem:
    passed = launch.get("ok") is True
    return AuditItem(
        key="public_launch",
        status="pass" if passed else "fail",
        summary="Public source-linked search launch check passes." if passed else "Public source-linked search launch check fails.",
        evidence={
            "checks": launch.get("checks", []),
            "errors": launch.get("errors", []),
        },
    )


def permission_note_item(path: Path, *, required: bool) -> AuditItem:
    summary = permission_note_summary(path)
    ok = summary.get("ok") is True
    status = "pass" if ok else "fail" if required else "warn"
    missing_sections = summary.get("missing_sections") or []
    missing_required_items = summary.get("missing_required_items") or []
    placeholders = summary.get("placeholders") or []
    unchecked_checkboxes = summary.get("unchecked_checkboxes") or 0
    unchecked_items = summary.get("unchecked_items") or []
    invalid_checklist_details = summary.get("invalid_checklist_details") or []
    deployment_decision = summary.get("deployment_decision")
    deployment_decision_ok = (
        not isinstance(deployment_decision, dict) or deployment_decision.get("ok") is True
    )
    if ok:
        text = "Permission note is complete; evidence is recorded by hash."
    elif summary.get("exists") is not True:
        text = f"Permission note is missing: {path}"
    elif (
        missing_sections
        or missing_required_items
        or placeholders
        or unchecked_checkboxes
        or invalid_checklist_details
        or not deployment_decision_ok
    ):
        problems = []
        if missing_sections:
            problems.append("missing sections")
        if missing_required_items:
            problems.append("missing required checklist items")
        if placeholders:
            problems.append("placeholders")
        if unchecked_checkboxes:
            problems.append("unchecked checklist items")
        if invalid_checklist_details:
            problems.append("invalid checklist details")
        if not deployment_decision_ok:
            problems.append("deployment decision is not affirmative")
        text = f"Permission note exists but still has {', '.join(problems)}."
    else:
        text = "Permission note could not be validated."
    return AuditItem(
        key="permission_note",
        status=status,
        summary=text,
        evidence={
            "path": str(path),
            "required": required,
            "provided": summary.get("provided"),
            "exists": summary.get("exists"),
            "ok": summary.get("ok"),
            "sha256": summary.get("sha256"),
            "bytes": summary.get("bytes"),
            "missing_sections": missing_sections,
            "missing_required_items": missing_required_items,
            "placeholders": placeholders,
            "unchecked_checkboxes": unchecked_checkboxes,
            "unchecked_items": unchecked_items,
            "invalid_checklist_details": invalid_checklist_details,
            "deployment_decision": deployment_decision,
            "error": summary.get("error"),
        },
    )


def artifact_item(path: Path, expected_threadmarks: int) -> AuditItem:
    if not path.exists():
        return AuditItem(
            key="artifact_manifest",
            status="fail",
            summary=f"Artifact manifest is missing: {path}",
            evidence={"path": str(path), "exists": False},
        )

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return AuditItem(
            key="artifact_manifest",
            status="fail",
            summary=f"Artifact manifest could not be read: {exc}",
            evidence={"path": str(path), "exists": True},
        )

    index = manifest.get("index", {})
    database = manifest.get("database", {})
    defaults = manifest.get("public_server_defaults", {})
    handling = manifest.get("content_handling", {})
    api_contract = manifest.get("public_api_contract", {})
    runtime_contract = manifest.get("deployment_runtime_contract", {})
    validation = manifest.get("validation", {})
    permission = manifest.get("permission_note", {})
    cap_errors = public_cap_errors(defaults)
    contact_errors = public_contact_errors(
        str(manifest.get("public_contact") or ""),
        str(manifest.get("removal_request_url") or ""),
        context="public artifact",
    )
    database_evidence = artifact_database_evidence(path, database)
    directory_evidence = artifact_directory_evidence(path)
    public_endpoints = api_contract.get("public_endpoints") if isinstance(api_contract, dict) else None
    endpoint_contract_ok = (
        isinstance(public_endpoints, list)
        and set(PUBLIC_API_ENDPOINTS).issubset(set(public_endpoints))
        and "/api/search" in public_endpoints
        and "/api/threadmarks" in public_endpoints
        and "/api/dossier" not in public_endpoints
        and "/api/evidence-pack" not in public_endpoints
        and "/api/recap" not in public_endpoints
        and "/api/coverage" not in public_endpoints
        and "/api/compare" not in public_endpoints
        and "/api/terms" not in public_endpoints
        and "/api/explain" not in public_endpoints
        and "/api/claim" not in public_endpoints
        and "/api/threadmark/{post_id}" not in public_endpoints
        and api_contract.get("grouped_search_endpoint_enabled") is True
        and api_contract.get("word_variants_always_enabled") is True
        and api_contract.get("private_fulltext_endpoint_public") is False
        and api_contract.get("legacy_evidence_endpoints_public") is False
    )
    runtime_contract_ok = (
        isinstance(runtime_contract, dict)
        and all(runtime_contract.get(key) == value for key, value in DEPLOYMENT_RUNTIME_CONTRACT.items())
    )
    passed = (
        manifest.get("artifact") == "thread-search-public-search-backend"
        and validation.get("ok") is True
        and database_evidence["ok"] is True
        and directory_evidence["ok"] is True
        and int(index.get("threadmarks") or 0) == expected_threadmarks
        and defaults.get("private_fulltext") is False
        and not cap_errors
        and not contact_errors
        and handling.get("database_must_not_be_static_or_downloadable") is True
        and handling.get("raw_html_included") is False
        and handling.get("jsonl_included") is False
        and handling.get("public_responses_are_source_linked_hits") is True
        and handling.get("public_ui_source_attribution") is True
        and handling.get("public_ui_contact_or_removal_notice_supported") is True
        and endpoint_contract_ok
        and runtime_contract_ok
        and permission.get("ok") is True
    )
    return AuditItem(
        key="artifact_manifest",
        status="pass" if passed else "fail",
        summary=(
            "Artifact manifest describes a validated private backend artifact."
            if passed
            else "Artifact manifest does not satisfy the public deployment contract."
        ),
        evidence={
            "path": str(path),
            "artifact": manifest.get("artifact"),
            "artifact_directory": directory_evidence,
            "database": database_evidence,
            "index_threadmarks": index.get("threadmarks"),
            "expected_threadmarks": expected_threadmarks,
            "validation_ok": validation.get("ok"),
            "private_fulltext": defaults.get("private_fulltext"),
            "public_cap_errors": cap_errors,
            "public_contact": manifest.get("public_contact"),
            "removal_request_url": manifest.get("removal_request_url"),
            "public_contact_errors": contact_errors,
            "public_server_defaults": defaults,
            "content_handling": handling,
            "public_api_contract": api_contract,
            "public_api_contract_ok": endpoint_contract_ok,
            "deployment_runtime_contract": runtime_contract,
            "deployment_runtime_contract_ok": runtime_contract_ok,
            "permission_note_ok": permission.get("ok"),
            "permission_note_sha256": permission.get("sha256"),
        },
    )


def artifact_directory_evidence(manifest_path: Path) -> dict[str, Any]:
    directory = manifest_path.parent
    if not directory.exists():
        return {
            "ok": False,
            "path": str(directory),
            "exists": False,
            "error": "artifact directory is missing",
        }
    if not directory.is_dir():
        return {
            "ok": False,
            "path": str(directory),
            "exists": True,
            "error": "artifact path is not a directory",
        }
    unexpected = sorted(
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.relative_to(directory).as_posix() not in ALLOWED_ARTIFACT_FILES
    )
    return {
        "ok": not unexpected,
        "path": str(directory),
        "allowed_files": sorted(ALLOWED_ARTIFACT_FILES),
        "unexpected_files": unexpected,
        "error": "artifact directory contains non-artifact files" if unexpected else None,
    }


def artifact_database_evidence(manifest_path: Path, database: Any) -> dict[str, Any]:
    if not isinstance(database, dict):
        return {"ok": False, "error": "missing database manifest section"}

    declared_path = str(database.get("path") or "")
    path_ok = declared_path == ARTIFACT_DB_NAME
    db_path = manifest_path.parent / ARTIFACT_DB_NAME
    if not path_ok:
        return {
            "ok": False,
            "path": declared_path,
            "expected_path": ARTIFACT_DB_NAME,
            "error": "database path must point to the adjacent artifact database",
        }
    if not db_path.exists():
        return {
            "ok": False,
            "path": str(db_path),
            "exists": False,
            "error": "artifact database is missing",
        }

    actual_sha = sha256_file(db_path)
    actual_size = db_path.stat().st_size
    declared_sha = database.get("sha256")
    declared_size = parse_manifest_int(database.get("size_bytes"))
    ok = declared_sha == actual_sha and declared_size == actual_size
    evidence: dict[str, Any] = {
        "ok": ok,
        "path": str(db_path),
        "exists": True,
        "sha256": actual_sha,
        "declared_sha256": declared_sha,
        "size_bytes": actual_size,
        "declared_size_bytes": declared_size,
    }
    if declared_sha != actual_sha:
        evidence["error"] = "artifact database sha256 does not match manifest"
    elif declared_size != actual_size:
        evidence["error"] = "artifact database size does not match manifest"
    return evidence


def public_smoke_item(report: dict[str, Any]) -> AuditItem:
    items = report.get("items") if isinstance(report.get("items"), list) else []
    failed = [
        item.get("key", "unknown")
        for item in items
        if isinstance(item, dict) and item.get("status") == "fail"
    ]
    passed = report.get("ok") is True and not failed
    return AuditItem(
        key="public_smoke",
        status="pass" if passed else "fail",
        summary=(
            "Live public HTTP smoke check passes."
            if passed
            else "Live public HTTP smoke check fails."
        ),
        evidence={
            "base_url": report.get("base_url"),
            "ok": report.get("ok"),
            "item_count": len(items),
            "failed_items": failed,
            "items": items,
        },
    )


def parse_manifest_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
