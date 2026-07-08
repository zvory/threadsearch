from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil

from .config import (
    DEFAULT_READINESS_PROBES,
    KNOWN_EXCLUDED_CATEGORIES,
    MAIN_THREADMARK_CATEGORY_ID,
    TARGET_READER_URL,
)
from .deploy_policy import public_cap_errors, public_contact_errors
from .permission import permission_note_summary
from .status import db_summary
from .validate import ValidationResult, validate_launch_ready

ARTIFACT_DB_NAME = "thread-search.sqlite"
ARTIFACT_MANIFEST_NAME = "manifest.json"
ARTIFACT_README_NAME = "README.deploy.txt"
PUBLIC_API_ENDPOINTS = (
    "/healthz",
    "/robots.txt",
    "/api/stats",
    "/api/threadmarks",
    "/api/terms",
    "/api/explain",
    "/api/suggest",
    "/api/search",
    "/api/report",
    "/api/mentions",
    "/api/dossier",
    "/api/evidence-pack",
    "/api/recap",
    "/api/coverage",
    "/api/compare",
    "/api/claim",
)
DEPLOYMENT_RUNTIME_CONTRACT = {
    "serve_requires_launch_ready": True,
    "serve_requires_artifact_manifest": True,
    "artifact_manifest_must_be_adjacent_to_database": True,
    "public_bind_requires_launch_ready": True,
    "public_private_fulltext_allowed": False,
    "public_chunk_results_allowed": False,
    "public_caps_may_only_be_lowered_at_runtime": True,
}
ALLOWED_ARTIFACT_FILES = {
    ARTIFACT_DB_NAME,
    ARTIFACT_MANIFEST_NAME,
    ARTIFACT_README_NAME,
}


class ArtifactError(RuntimeError):
    pass


class ArtifactValidationError(ArtifactError):
    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        super().__init__("artifact validation failed")


class ArtifactPermissionError(ArtifactError):
    def __init__(self, path: Path, summary: dict[str, object]) -> None:
        self.path = path
        self.summary = summary
        super().__init__(f"permission note is incomplete or invalid: {path}")


class ArtifactCapError(ArtifactError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("public API caps are unsafe for a public artifact")


class ArtifactContactError(ArtifactError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("public contact/removal metadata is incomplete")


@dataclass(frozen=True)
class ArtifactResult:
    output_dir: Path
    database_path: Path
    manifest_path: Path
    readme_path: Path
    sha256: str
    size_bytes: int
    validation: ValidationResult


def export_public_artifact(
    *,
    db_path: Path,
    out_dir: Path,
    expected_threadmarks: int = 269,
    expected_category: int = MAIN_THREADMARK_CATEGORY_ID,
    excluded_categories: tuple[int, ...] = tuple(KNOWN_EXCLUDED_CATEGORIES),
    probes: tuple[str, ...] = DEFAULT_READINESS_PROBES,
    public_search_limit: int = 30,
    public_report_limit: int = 100,
    public_mention_limit: int = 50,
    public_threadmark_limit: int = 300,
    max_query_chars: int = 120,
    mention_window_chars: int = 320,
    public_snippet_budget_chars: int = 6000,
    public_rate_limit_per_minute: int = 60,
    allow_unsafe_public_caps: bool = False,
    permission_note: Path | None = None,
    source_reader_url: str = TARGET_READER_URL,
    public_contact: str = "",
    removal_request_url: str = "",
) -> ArtifactResult:
    cap_errors = public_cap_errors(
        {
            "public_search_limit": public_search_limit,
            "public_report_limit": public_report_limit,
            "public_mention_limit": public_mention_limit,
            "public_threadmark_limit": public_threadmark_limit,
            "max_query_chars": max_query_chars,
            "mention_window_chars": mention_window_chars,
            "public_snippet_budget_chars": public_snippet_budget_chars,
            "public_rate_limit_per_minute": public_rate_limit_per_minute,
        }
    )
    if cap_errors and not allow_unsafe_public_caps:
        raise ArtifactCapError(cap_errors)

    validation = validate_launch_ready(
        jsonl_path=Path(),
        db_path=db_path,
        expected_threadmarks=expected_threadmarks,
        expected_category=expected_category,
        excluded_categories=excluded_categories,
        probes=probes,
        private_fulltext=False,
        db_only=True,
    )
    if not validation.ok:
        raise ArtifactValidationError(validation)

    target_db = out_dir / ARTIFACT_DB_NAME
    if db_path.resolve() == target_db.resolve():
        raise ArtifactError("output directory would overwrite the source sqlite database")

    permission_summary = permission_note_summary(permission_note) if permission_note is not None else None
    if permission_summary is not None and not permission_summary.get("ok"):
        raise ArtifactPermissionError(permission_note, permission_summary)
    contact_errors = public_contact_errors(public_contact, removal_request_url)
    if contact_errors:
        raise ArtifactContactError(contact_errors)

    ensure_artifact_dir(out_dir)
    shutil.copy2(db_path, target_db)

    digest = sha256_file(target_db)
    size_bytes = target_db.stat().st_size
    manifest = build_manifest(
        database_path=target_db,
        sha256=digest,
        size_bytes=size_bytes,
        validation=validation,
        expected_threadmarks=expected_threadmarks,
        expected_category=expected_category,
        excluded_categories=excluded_categories,
        probes=probes,
        public_search_limit=public_search_limit,
        public_report_limit=public_report_limit,
        public_mention_limit=public_mention_limit,
        public_threadmark_limit=public_threadmark_limit,
        max_query_chars=max_query_chars,
        mention_window_chars=mention_window_chars,
        public_snippet_budget_chars=public_snippet_budget_chars,
        public_rate_limit_per_minute=public_rate_limit_per_minute,
        permission_summary=permission_summary,
        source_reader_url=source_reader_url,
        public_contact=public_contact,
        removal_request_url=removal_request_url,
    )

    manifest_path = out_dir / ARTIFACT_MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    readme_path = out_dir / ARTIFACT_README_NAME
    readme_path.write_text(render_artifact_readme(probes), encoding="utf-8")

    return ArtifactResult(
        output_dir=out_dir,
        database_path=target_db,
        manifest_path=manifest_path,
        readme_path=readme_path,
        sha256=digest,
        size_bytes=size_bytes,
        validation=validation,
    )


def ensure_artifact_dir(out_dir: Path) -> None:
    if not out_dir.exists():
        out_dir.mkdir(parents=True)
        return
    if not out_dir.is_dir():
        raise ArtifactError(f"artifact output path is not a directory: {out_dir}")

    unexpected = [
        path.relative_to(out_dir).as_posix()
        for path in out_dir.rglob("*")
        if path.relative_to(out_dir).as_posix() not in ALLOWED_ARTIFACT_FILES
    ]
    if unexpected:
        listed = ", ".join(sorted(unexpected)[:10])
        raise ArtifactError(f"artifact output directory contains non-artifact files: {listed}")


def build_manifest(
    *,
    database_path: Path,
    sha256: str,
    size_bytes: int,
    validation: ValidationResult,
    expected_threadmarks: int,
    expected_category: int,
    excluded_categories: tuple[int, ...],
    probes: tuple[str, ...],
    public_search_limit: int,
    public_report_limit: int,
    public_mention_limit: int,
    public_threadmark_limit: int,
    max_query_chars: int,
    mention_window_chars: int,
    public_snippet_budget_chars: int,
    public_rate_limit_per_minute: int,
    permission_summary: dict[str, object] | None,
    source_reader_url: str,
    public_contact: str,
    removal_request_url: str,
) -> dict[str, object]:
    summary = db_summary(database_path)
    summary.pop("path", None)
    return {
        "artifact": "thread-search-public-search-backend",
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_reader_url": source_reader_url,
        "public_contact": public_contact,
        "removal_request_url": removal_request_url,
        "database": {
            "path": ARTIFACT_DB_NAME,
            "sha256": sha256,
            "size_bytes": size_bytes,
        },
        "index": summary,
        "validation": asdict(validation),
        "validation_contract": {
            "expected_threadmarks": expected_threadmarks,
            "expected_category": expected_category,
            "excluded_categories": list(excluded_categories),
            "probes": list(probes),
        },
        "public_server_defaults": {
            "private_fulltext": False,
            "public_search_limit": public_search_limit,
            "public_report_limit": public_report_limit,
            "public_mention_limit": public_mention_limit,
            "public_threadmark_limit": public_threadmark_limit,
            "max_query_chars": max_query_chars,
            "mention_window_chars": mention_window_chars,
            "public_snippet_budget_chars": public_snippet_budget_chars,
            "public_rate_limit_per_minute": public_rate_limit_per_minute,
            "allow_public_chunk_results": False,
        },
        "content_handling": {
            "contains_full_text_server_side": True,
            "database_must_not_be_static_or_downloadable": True,
            "raw_html_included": False,
            "jsonl_included": False,
            "public_responses_are_snippets_and_source_links": True,
            "public_ui_source_attribution": True,
            "public_ui_contact_or_removal_notice_supported": True,
        },
        "public_api_contract": {
            "public_endpoints": list(PUBLIC_API_ENDPOINTS),
            "dossier_endpoint_enabled": True,
            "evidence_pack_endpoint_enabled": True,
            "recap_endpoint_enabled": True,
            "coverage_endpoint_enabled": True,
            "compare_endpoint_enabled": True,
            "terms_endpoint_metadata_only": True,
            "explain_endpoint_metadata_only": True,
            "explain_term_breakdown_metadata_only": True,
            "claim_endpoint_enabled": True,
            "claim_negation_cues_enabled": True,
            "claim_cautions_enabled": True,
            "private_fulltext_endpoint_public": False,
            "responses_are_bounded_by_public_caps": True,
        },
        "deployment_runtime_contract": dict(DEPLOYMENT_RUNTIME_CONTRACT),
        "permission_note": permission_summary or {"provided": False, "exists": False, "ok": False},
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def render_artifact_readme(probes: tuple[str, ...] = DEFAULT_READINESS_PROBES) -> str:
    probe_args = " ".join(f"--probe {probe}" for probe in probes)
    return f"""Thread Search public search backend artifact

This directory is for server-side deployment only. The SQLite database contains
the indexed thread text so the HTTP server can produce snippets and source links.

Do not serve this directory as static files. Do not put thread-search.sqlite behind
a public download URL unless you have explicit redistribution permission.

Recommended public server command:

  thread-search serve --db /path/to/thread-search.sqlite --host 0.0.0.0 --port 8765 --require-launch-ready --require-artifact-manifest --artifact-manifest /path/to/manifest.json {probe_args}

After the server is reachable, verify the live HTTP surface:

  thread-search public-smoke --base-url https://your-public-host.example --require-artifact-manifest {probe_args} --claim-pair Cuba communist

For a combined final evidence record, rerun audit with the live base URL:

  thread-search audit --artifact-manifest /path/to/manifest.json --public-base-url https://your-public-host.example {probe_args} --claim-pair Cuba communist

Keep --private-fulltext off for public deployments. The launch-check and
manifest validation assume snippet-search mode. Keep the public API caps and
request rate limit enabled, including the aggregate per-response snippet budget,
unless explicit redistribution permission allows a broader service.

The public-smoke command's --require-artifact-manifest flag checks that
/api/stats reports artifact_manifest_validated: true, proving this process
started after validating the adjacent manifest.

Keep the public UI source attribution and Sufficient Velocity reader links
visible unless the permission and site-rule review explicitly say otherwise.
Set --public-contact or THREAD_SEARCH_PUBLIC_CONTACT, and set --removal-request-url
or THREAD_SEARCH_REMOVAL_REQUEST_URL, for public deployments so readers and rights
holders have a visible operator/removal path.

Public endpoint contract:

{chr(10).join(f"  - {endpoint}" for endpoint in PUBLIC_API_ENDPOINTS)}
"""
