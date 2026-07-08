from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .deploy_bundle import BUNDLE_MANIFEST_NAME, DEFAULT_BUNDLE_DIR, verify_deploy_bundle
from .permission import permission_note_summary


def render_author_review_packet(
    payload: dict[str, Any],
    *,
    public_base_url: str,
    probes: tuple[str, ...],
    artifact_manifest: Path | None = None,
    permission_note: Path | None = None,
    deploy_bundle_manifest: Path | None = DEFAULT_BUNDLE_DIR / BUNDLE_MANIFEST_NAME,
) -> str:
    crawl = payload["crawl"]
    corpus = payload["corpus"]
    index = payload["index"]
    validation = payload["validation"]
    launch = payload["launch_check"]
    permission = permission_note_summary(permission_note) if permission_note is not None else None
    manifest = read_manifest(artifact_manifest)
    bundle_manifest = read_manifest(deploy_bundle_manifest)
    bundle_check = verify_deploy_bundle(deploy_bundle_manifest) if deploy_bundle_manifest is not None else None
    base_url = normalize_base_url(public_base_url)
    generated = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    lines = [
        "# Thread Search Author Review Packet",
        "",
        f"Generated: `{generated}`",
        f"Prototype URL: [{base_url}]({base_url})",
        f"Source reader: [{crawl.get('reader_root')}]({crawl.get('reader_root')})",
        "",
        "This packet contains deployment metadata and review links only. It does not include story text, raw HTML, extracted JSONL, or the SQLite database.",
        "",
        "## Scope",
        "",
        f"- Corpus scope: main `Threadmarks` category only, category `{crawl.get('category_id', 1)}`.",
        "- Excluded by default: Sidestory and Apocrypha categories.",
        f"- Extracted threadmarks: `{corpus.get('threadmarks', 0)}`.",
        f"- Indexed chunks: `{index.get('chunks', 0)}`.",
        f"- Indexed words: `{index.get('words', corpus.get('words', 0))}`.",
        f"- Robots allowed for configured crawler: `{crawl.get('robots_allowed')}`.",
        f"- Cached reader pages: `{crawl.get('cached_pages', 0)}` / `{crawl.get('page_count', 0)}`.",
        "",
        "## Public Prototype Contract",
        "",
        "- Public UI/API exposes source-linked search hits grouped by threadmark.",
        "- Full-text threadmark routes stay disabled.",
        "- Raw crawl cache, extracted JSONL, and SQLite database are not public downloads.",
        "- Search-engine indexing stays blocked with `noindex` and a disallow-all `robots.txt`.",
        "- Hosted LLM or embedding API calls with thread text are not part of this prototype.",
        "- Public API caps and per-IP rate limiting remain enabled.",
        "",
        "## Verification",
        "",
        f"- Corpus validation passed: `{validation.get('ok')}`.",
        f"- Launch check passed: `{launch.get('ok')}`.",
        f"- Readiness probes: `{', '.join(probes) if probes else 'default'}`.",
    ]
    if permission_note is not None and permission is not None:
        lines.append(f"- Permission note passed: `{permission.get('ok')}`.")
        if permission.get("sha256"):
            lines.append(f"- Permission note SHA-256: `{permission.get('sha256')}`.")
    if artifact_manifest is not None:
        lines.append(f"- Artifact manifest: `{artifact_manifest}`.")
        if manifest:
            database = manifest.get("database") if isinstance(manifest.get("database"), dict) else {}
            lines.append(f"- Artifact database SHA-256: `{database.get('sha256', '')}`.")
            lines.append(f"- Artifact manifest validation recorded: `{bool(manifest.get('validation'))}`.")
            public_contact = manifest.get("public_contact")
            removal_request_url = manifest.get("removal_request_url")
            if public_contact:
                lines.append(f"- Public contact: `{public_contact}`.")
            if removal_request_url:
                lines.append(f"- Removal request URL: `{removal_request_url}`.")
    if deploy_bundle_manifest is not None:
        lines.append(f"- Deploy bundle manifest: `{deploy_bundle_manifest}`.")
        if bundle_check is not None:
            lines.append(f"- Deploy bundle check passed: `{bundle_check.ok}`.")
        if bundle_manifest:
            app_bundle = bundle_manifest.get("app_bundle") if isinstance(bundle_manifest.get("app_bundle"), dict) else {}
            private_bundle = (
                bundle_manifest.get("private_artifact_bundle")
                if isinstance(bundle_manifest.get("private_artifact_bundle"), dict)
                else {}
            )
            if app_bundle:
                lines.append(f"- Public app bundle SHA-256: `{app_bundle.get('sha256', '')}`.")
            if private_bundle:
                lines.append(f"- Private artifact bundle SHA-256: `{private_bundle.get('sha256', '')}`.")
                lines.append(
                    "- Private artifact bundle handling: keep server-side only; do not publish or put it in a web root."
                )

    lines.extend(["", "## Demo Links", ""])
    lines.append(f"- [Contents view]({url_for(base_url, '/', {'view': 'contents'})})")
    seen_search_terms: set[str] = set()
    for probe in probes:
        append_search_link(lines, base_url, probe, seen_search_terms)

    lines.extend(
        [
            "",
            "## Suggested Review Questions",
            "",
            "- Are source-linked search hits and attribution acceptable?",
            "- Are source links prominent enough?",
            "- Should any topic, term, or section be excluded from public search?",
            "- Should the public prototype remain noindex?",
            "- What contact or removal-request route should be shown publicly?",
            "",
            "## Operator Commands",
            "",
            "```sh",
            ".venv/bin/thread-search public-smoke --base-url "
            f"{base_url} --require-artifact-manifest --probe Soviet --probe Cuba",
            ".venv/bin/thread-search audit --probe Soviet --probe Cuba "
            "--artifact-manifest dist/thread-search-public/manifest.json "
            "--permission-note data/permission-note.md "
            f"--public-base-url {base_url}",
            "```",
        ]
    )
    return "\n".join(lines)


def read_manifest(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def normalize_base_url(value: str) -> str:
    return value.rstrip("/") or "http://127.0.0.1:8765"


def append_search_link(lines: list[str], base_url: str, term: str, seen: set[str]) -> None:
    key = term.casefold()
    if key in seen:
        return
    seen.add(key)
    lines.append(f"- [Search `{term}`]({url_for(base_url, '/', {'q': term})})")


def url_for(base_url: str, path: str, params: dict[str, str]) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{base_url}{normalized_path}?{urlencode(params)}"
