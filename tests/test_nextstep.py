from pathlib import Path
import json
from types import SimpleNamespace

from planquest.nextstep import recommend_next_step
from planquest.permission import REQUIRED_CHECKLIST_ITEMS, REQUIRED_SECTIONS


def payload(
    *,
    robots_allowed: bool = True,
    cached_pages: int = 2,
    page_count: int = 2,
    corpus_threadmarks: int = 2,
    index_threadmarks: int = 2,
    expected: int = 2,
    validation_ok: bool = True,
    launch_ok: bool = True,
) -> dict[str, object]:
    pages = [
        {"page": page, "url": f"https://example.invalid/reader/page-{page}", "cached": page <= cached_pages}
        for page in range(1, page_count + 1)
    ]
    validation_errors = [] if validation_ok else [f"expected {expected} threadmarks, found {corpus_threadmarks}"]
    return {
        "crawl": {
            "reader_root": "https://example.invalid/reader/",
            "robots_allowed": robots_allowed,
            "user_agent": "thread-search-test",
            "page_count": page_count,
            "cached_pages": cached_pages,
            "network_pages_if_run_now": page_count - cached_pages,
            "pages": pages,
        },
        "corpus": {
            "exists": True,
            "ok": True,
            "path": "records.jsonl",
            "threadmarks": corpus_threadmarks,
            "words": 100,
            "categories": [1],
        },
        "index": {
            "exists": True,
            "ok": True,
            "path": "records.sqlite",
            "threadmarks": index_threadmarks,
            "chunks": max(index_threadmarks, 1),
            "stored_chunks": max(index_threadmarks, 1),
            "words": 100,
            "categories": [1],
        },
        "validation": {
            "ok": validation_ok,
            "checks": ["probe 'Cuba': 1 result(s)"],
            "errors": validation_errors,
        },
        "launch_check": {
            "ok": launch_ok,
            "checks": ["public full-text routes: disabled"],
            "errors": [] if launch_ok else ["launch failed"],
        },
    }


def write_valid_permission_note(tmp_path: Path) -> Path:
    note = tmp_path / "permission.md"
    sections = "\n\n".join(f"## {section}\nReviewed and approved." for section in REQUIRED_SECTIONS)
    checklist_items = [f"- [x] {item}: {concrete_permission_detail(item)}" for item in REQUIRED_CHECKLIST_ITEMS]
    checklist = "\n".join(checklist_items)
    note.write_text(f"# Thread Search Permission Note\n\n{sections}\n\n{checklist}\n", encoding="utf-8")
    return note


def concrete_permission_detail(item: str) -> str:
    details = {
        "Permission source": "Author forum PM confirming snippet search on 2026-07-08.",
        "Permission date": "2026-07-08.",
        "Permission covers public source-linked search": "Author approved source-linked search hits back to Sufficient Velocity.",
        "Permission does not cover public full-text redistribution unless explicitly recorded here": "No public full-text redistribution approved.",
        "Sufficient Velocity rules or policy pages reviewed": "Reviewed Sufficient Velocity terms and rules pages at https://forums.sufficientvelocity.com/ on 2026-07-08.",
        "Review date": "2026-07-08.",
        "Limits affecting deployment, crawling, snippets, indexing, or attribution": "Keep snippets bounded, noindex enabled, and source links visible.",
        "Public access is source-linked search": "Public UI and API expose source-linked search hits.",
        "Full-text threadmark routes are disabled": "Public server runs without --private-fulltext.",
        "SQLite database remains private server-side, not static/downloadable": "Artifact database is mounted privately behind the server.",
        "Search-engine indexing remains blocked unless explicitly allowed": "X-Robots-Tag noindex and disallow-all robots.txt remain enabled.",
        "Decision to proceed or not proceed": "proceed with public source-linked search.",
        "Operator name or handle": "Test Operator.",
        "Decision date": "2026-07-08.",
    }
    return details[item]


def test_recommend_next_step_prefetches_first_uncached_page() -> None:
    step = recommend_next_step(
        payload(cached_pages=1, page_count=3),
        expected_threadmarks=2,
        probes=("Cuba",),
        delay_seconds=45,
    )

    assert step.key == "prefetch_next_page"
    assert "--from-page 2 --to-page 2" in step.command
    assert "--delay 45" in step.command


def test_recommend_next_step_builds_offline_after_cache_complete() -> None:
    step = recommend_next_step(
        payload(corpus_threadmarks=1, index_threadmarks=1, expected=2, validation_ok=False),
        expected_threadmarks=2,
        probes=("Cuba",),
    )

    assert step.key == "build_offline"
    assert step.command == ".venv/bin/thread-search build --offline --probe Cuba"


def test_recommend_next_step_exports_artifact_after_launch_ready() -> None:
    step = recommend_next_step(
        payload(),
        expected_threadmarks=2,
        probes=("Cuba",),
        artifact_manifest=Path("missing.json"),
    )

    assert step.key == "export_artifact"
    assert step.command == (
        '.venv/bin/thread-search artifact --probe Cuba --public-contact "$THREAD_SEARCH_PUBLIC_CONTACT" '
        '--removal-request-url "$THREAD_SEARCH_REMOVAL_REQUEST_URL"'
    )


def test_recommend_next_step_creates_missing_permission_note(tmp_path: Path) -> None:
    note = tmp_path / "permission.md"
    step = recommend_next_step(
        payload(),
        expected_threadmarks=2,
        probes=("Cuba",),
        artifact_manifest=tmp_path / "missing.json",
        permission_note=note,
    )

    assert step.key == "create_permission_note"
    assert step.command == f".venv/bin/thread-search permission-note --out {note}"


def test_recommend_next_step_checks_incomplete_permission_note(tmp_path: Path) -> None:
    note = tmp_path / "permission.md"
    note.write_text("## Author Permission\nTODO\n", encoding="utf-8")

    step = recommend_next_step(
        payload(),
        expected_threadmarks=2,
        probes=("Cuba",),
        artifact_manifest=tmp_path / "missing.json",
        permission_note=note,
    )

    assert step.key == "complete_permission_note"
    assert step.command == f".venv/bin/thread-search permission-note --check --out {note}"
    assert any(reason.startswith("missing_required_items=") for reason in step.reasons)


def test_recommend_next_step_exports_artifact_with_permission_note(tmp_path: Path) -> None:
    note = write_valid_permission_note(tmp_path)
    step = recommend_next_step(
        payload(),
        expected_threadmarks=2,
        probes=("Cuba",),
        artifact_manifest=tmp_path / "missing.json",
        permission_note=note,
    )

    assert step.key == "export_artifact"
    assert step.command == (
        f'.venv/bin/thread-search artifact --probe Cuba --permission-note {note} '
        '--public-contact "$THREAD_SEARCH_PUBLIC_CONTACT" --removal-request-url "$THREAD_SEARCH_REMOVAL_REQUEST_URL"'
    )


def test_recommend_next_step_runs_final_audit_when_manifest_exists(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    step = recommend_next_step(
        payload(),
        expected_threadmarks=2,
        probes=("Cuba",),
        artifact_manifest=manifest,
    )

    assert step.key == "final_audit"
    assert "--artifact-manifest" in step.command


def test_recommend_next_step_runs_final_audit_with_permission_note(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"permission_note": {"ok": true, "sha256": "abc"}}', encoding="utf-8")
    note = write_valid_permission_note(tmp_path)

    step = recommend_next_step(
        payload(),
        expected_threadmarks=2,
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
    )

    assert step.key == "final_audit"
    assert f"--permission-note {note}" in step.command


def test_recommend_next_step_can_include_public_base_url_in_final_audit(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"permission_note": {"ok": true, "sha256": "abc"}}', encoding="utf-8")
    note = write_valid_permission_note(tmp_path)

    step = recommend_next_step(
        payload(),
        expected_threadmarks=2,
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
        public_base_url="https://search.example.invalid",
    )

    assert step.key == "final_audit"
    assert step.command == (
        f".venv/bin/thread-search audit --probe Cuba --artifact-manifest {manifest} "
        f"--permission-note {note} --public-base-url https://search.example.invalid"
    )


def test_recommend_next_step_writes_json_audit_report_when_requested(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"permission_note": {"ok": true, "sha256": "abc"}}', encoding="utf-8")
    note = write_valid_permission_note(tmp_path)
    audit_report = tmp_path / "final-audit.json"

    step = recommend_next_step(
        payload(),
        expected_threadmarks=2,
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
        public_base_url="https://search.example.invalid",
        audit_report=audit_report,
    )

    assert step.key == "final_audit"
    assert step.command == (
        f".venv/bin/thread-search audit --probe Cuba --artifact-manifest {manifest} "
        f"--permission-note {note} --public-base-url https://search.example.invalid "
        f"--json --out {audit_report}"
    )


def test_recommend_next_step_ready_after_passing_audit_report(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"permission_note": {"ok": true, "sha256": "abc"}}', encoding="utf-8")
    note = write_valid_permission_note(tmp_path)
    audit_report = tmp_path / "final-audit.json"
    audit_report.write_text(
        json.dumps(
            {
                "ok": True,
                "generated_at_utc": "2026-07-08T00:00:00Z",
                "items": [
                    {
                        "key": "artifact_manifest",
                        "status": "pass",
                        "summary": "ok",
                        "evidence": {"path": str(manifest)},
                    },
                    {
                        "key": "public_smoke",
                        "status": "pass",
                        "summary": "ok",
                        "evidence": {"base_url": "https://search.example.invalid/"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    step = recommend_next_step(
        payload(),
        expected_threadmarks=2,
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
        public_base_url="https://search.example.invalid",
        audit_report=audit_report,
    )

    assert step.key == "ready_for_author_review"
    assert step.command == (
        f".venv/bin/thread-search author-review --offline --public-base-url https://search.example.invalid "
        f"--probe Cuba --artifact-manifest {manifest} --permission-note {note} --out data/author-review.md"
    )
    assert any(reason == f"audit_report={audit_report}" for reason in step.reasons)


def test_recommend_next_step_creates_deploy_bundle_after_passing_audit_report(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"permission_note": {"ok": true, "sha256": "abc"}}', encoding="utf-8")
    note = write_valid_permission_note(tmp_path)
    audit_report = write_passing_audit_report(tmp_path, manifest, public_base_url="https://search.example.invalid")
    bundle_manifest = tmp_path / "bundle" / "deploy-bundle-manifest.json"

    step = recommend_next_step(
        payload(),
        expected_threadmarks=2,
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
        public_base_url="https://search.example.invalid",
        audit_report=audit_report,
        deploy_bundle_manifest=bundle_manifest,
    )

    assert step.key == "create_deploy_bundle"
    assert step.command == ".venv/bin/thread-search deploy-bundle"
    assert any(reason == f"deploy_bundle_manifest={bundle_manifest}" for reason in step.reasons)


def test_recommend_next_step_ready_after_passing_audit_and_bundle_check(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"permission_note": {"ok": true, "sha256": "abc"}}', encoding="utf-8")
    note = write_valid_permission_note(tmp_path)
    audit_report = write_passing_audit_report(tmp_path, manifest, public_base_url="https://search.example.invalid")
    bundle_manifest = tmp_path / "bundle" / "deploy-bundle-manifest.json"
    bundle_manifest.parent.mkdir()
    bundle_manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("planquest.nextstep.verify_deploy_bundle", lambda _path: SimpleNamespace(ok=True, errors=()))

    step = recommend_next_step(
        payload(),
        expected_threadmarks=2,
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
        public_base_url="https://search.example.invalid",
        audit_report=audit_report,
        deploy_bundle_manifest=bundle_manifest,
    )

    assert step.key == "ready_for_author_review"
    assert any(reason == f"deploy_bundle_manifest={bundle_manifest}" for reason in step.reasons)
    assert f"--deploy-bundle-manifest {bundle_manifest}" in step.command


def test_recommend_next_step_refreshes_failing_deploy_bundle(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"permission_note": {"ok": true, "sha256": "abc"}}', encoding="utf-8")
    note = write_valid_permission_note(tmp_path)
    audit_report = write_passing_audit_report(tmp_path, manifest, public_base_url="https://search.example.invalid")
    bundle_manifest = tmp_path / "bundle" / "deploy-bundle-manifest.json"
    bundle_manifest.parent.mkdir()
    bundle_manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "planquest.nextstep.verify_deploy_bundle",
        lambda _path: SimpleNamespace(ok=False, errors=("app_bundle sha256 mismatch",)),
    )

    step = recommend_next_step(
        payload(),
        expected_threadmarks=2,
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
        public_base_url="https://search.example.invalid",
        audit_report=audit_report,
        deploy_bundle_manifest=bundle_manifest,
    )

    assert step.key == "refresh_deploy_bundle"
    assert step.command == ".venv/bin/thread-search deploy-bundle"
    assert "app_bundle sha256 mismatch" in step.reasons


def test_recommend_next_step_ignores_wrong_public_base_audit_report(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"permission_note": {"ok": true, "sha256": "abc"}}', encoding="utf-8")
    note = write_valid_permission_note(tmp_path)
    audit_report = tmp_path / "final-audit.json"
    audit_report.write_text(
        json.dumps(
            {
                "ok": True,
                "generated_at_utc": "2026-07-08T00:00:00Z",
                "items": [
                    {
                        "key": "artifact_manifest",
                        "status": "pass",
                        "summary": "ok",
                        "evidence": {"path": str(manifest)},
                    },
                    {
                        "key": "public_smoke",
                        "status": "pass",
                        "summary": "ok",
                        "evidence": {"base_url": "https://other.example.invalid/"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    step = recommend_next_step(
        payload(),
        expected_threadmarks=2,
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
        public_base_url="https://search.example.invalid",
        audit_report=audit_report,
    )

    assert step.key == "final_audit"
    assert f"--json --out {audit_report}" in step.command


def test_recommend_next_step_reexports_manifest_without_permission_evidence(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    note = write_valid_permission_note(tmp_path)

    step = recommend_next_step(
        payload(),
        expected_threadmarks=2,
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
    )

    assert step.key == "reexport_artifact"
    assert step.command == (
        f'.venv/bin/thread-search artifact --probe Cuba --permission-note {note} '
        '--public-contact "$THREAD_SEARCH_PUBLIC_CONTACT" --removal-request-url "$THREAD_SEARCH_REMOVAL_REQUEST_URL"'
    )


def test_recommend_next_step_stops_when_robots_block() -> None:
    step = recommend_next_step(payload(robots_allowed=False), expected_threadmarks=2, probes=("Cuba",))

    assert step.key == "blocked_by_robots"
    assert step.command is None


def write_passing_audit_report(tmp_path: Path, manifest: Path, *, public_base_url: str) -> Path:
    audit_report = tmp_path / "final-audit.json"
    audit_report.write_text(
        json.dumps(
            {
                "ok": True,
                "generated_at_utc": "2026-07-08T00:00:00Z",
                "items": [
                    {
                        "key": "artifact_manifest",
                        "status": "pass",
                        "summary": "ok",
                        "evidence": {"path": str(manifest)},
                    },
                    {
                        "key": "public_smoke",
                        "status": "pass",
                        "summary": "ok",
                        "evidence": {"base_url": public_base_url.rstrip("/") + "/"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return audit_report
