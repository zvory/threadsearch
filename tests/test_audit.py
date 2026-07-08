from pathlib import Path
import json

from planquest.audit import evaluate_audit
from planquest.artifact import ARTIFACT_DB_NAME, DEPLOYMENT_RUNTIME_CONTRACT, PUBLIC_API_ENDPOINTS, sha256_file
from planquest.permission import REQUIRED_CHECKLIST_ITEMS, REQUIRED_SECTIONS


PUBLIC_CONTACT = "mailto:operator@thread-search.example"
REMOVAL_REQUEST_URL = "https://thread-search.example/removal"


def payload(
    *,
    threadmarks: int,
    expected: int,
    categories: list[int] | None = None,
    validation_ok: bool = True,
    launch_ok: bool = True,
) -> dict[str, object]:
    categories = categories or [1]
    errors = [] if validation_ok else [f"expected {expected} threadmarks, found {threadmarks}"]
    launch_errors = [] if launch_ok else list(errors)
    return {
        "crawl": {
            "reader_root": "https://forums.sufficientvelocity.com/threads/example.1/reader/",
            "robots_allowed": True,
            "user_agent": "thread-search-test",
            "page_count": 2,
            "cached_pages": 2,
            "network_pages_if_run_now": 0,
        },
        "corpus": {
            "exists": True,
            "ok": True,
            "path": "records.jsonl",
            "threadmarks": threadmarks,
            "words": 100,
            "categories": categories,
        },
        "index": {
            "exists": True,
            "ok": True,
            "path": "records.sqlite",
            "threadmarks": threadmarks,
            "chunks": max(threadmarks, 1),
            "stored_chunks": max(threadmarks, 1),
            "words": 100,
            "categories": categories,
        },
        "fetch_log": {
            "exists": True,
            "ok": True,
            "path": "data/raw/fetch-log.jsonl",
            "entries": 3,
            "page_fetches": 2,
            "robots_fetches": 1,
            "bytes": 1234,
        },
        "validation": {
            "ok": validation_ok,
            "checks": ["probe 'Cuba': 1 result(s)"] if validation_ok else ["probe 'Cuba': 0 result(s)"],
            "errors": errors,
        },
        "launch_check": {
            "ok": launch_ok,
            "checks": ["public full-text routes: disabled"],
            "errors": launch_errors,
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
        "Permission source": "Author forum PM confirming source-linked search on 2026-07-08.",
        "Permission date": "2026-07-08.",
        "Permission covers public source-linked search": "Author approved source-linked search hits back to Sufficient Velocity.",
        "Permission does not cover public full-text redistribution unless explicitly recorded here": "No public full-text redistribution approved.",
        "Sufficient Velocity rules or policy pages reviewed": "Reviewed Sufficient Velocity terms and rules pages at https://forums.sufficientvelocity.com/ on 2026-07-08.",
        "Review date": "2026-07-08.",
        "Limits affecting deployment, crawling, snippets, indexing, or attribution": "Keep noindex enabled and source links visible.",
        "Public access is source-linked search": "Public UI and API expose source-linked search hits.",
        "Full-text threadmark routes are disabled": "Public server runs without --private-fulltext.",
        "SQLite database remains private server-side, not static/downloadable": "Artifact database is mounted privately behind the server.",
        "Search-engine indexing remains blocked unless explicitly allowed": "X-Robots-Tag noindex and disallow-all robots.txt remain enabled.",
        "Decision to proceed or not proceed": "proceed with public source-linked search.",
        "Operator name or handle": "Test Operator.",
        "Decision date": "2026-07-08.",
    }
    return details[item]


def public_defaults(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "private_fulltext": False,
        "public_search_limit": 30,
        "public_threadmark_limit": 300,
        "max_query_chars": 120,
        "public_rate_limit_per_minute": 60,
    }
    defaults.update(overrides)
    return defaults


def content_handling(**overrides: object) -> dict[str, object]:
    handling: dict[str, object] = {
        "database_must_not_be_static_or_downloadable": True,
        "raw_html_included": False,
        "jsonl_included": False,
        "public_responses_are_source_linked_hits": True,
        "public_ui_source_attribution": True,
        "public_ui_contact_or_removal_notice_supported": True,
    }
    handling.update(overrides)
    return handling


def api_contract(**overrides: object) -> dict[str, object]:
    contract: dict[str, object] = {
        "public_endpoints": list(PUBLIC_API_ENDPOINTS),
        "grouped_search_endpoint_enabled": True,
        "word_variants_always_enabled": True,
        "private_fulltext_endpoint_public": False,
    }
    contract.update(overrides)
    return contract


def deployment_runtime_contract(**overrides: object) -> dict[str, object]:
    contract: dict[str, object] = dict(DEPLOYMENT_RUNTIME_CONTRACT)
    contract.update(overrides)
    return contract


def artifact_database(tmp_path: Path, body: bytes = b"sqlite fixture") -> dict[str, object]:
    db = tmp_path / ARTIFACT_DB_NAME
    db.write_bytes(body)
    return {
        "path": ARTIFACT_DB_NAME,
        "sha256": sha256_file(db),
        "size_bytes": db.stat().st_size,
    }


def test_evaluate_audit_passes_ready_payload() -> None:
    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
    )

    assert report.ok is True
    assert {item.key for item in report.items if item.status == "fail"} == set()
    receipts_item = next(item for item in report.items if item.key == "fetch_receipts")
    assert receipts_item.status == "pass"


def test_evaluate_audit_warns_on_missing_permission_note_before_artifact(tmp_path: Path) -> None:
    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        permission_note=tmp_path / "missing.md",
    )

    permission_item = next(item for item in report.items if item.key == "permission_note")
    assert report.ok is True
    assert permission_item.status == "warn"


def test_evaluate_audit_warns_on_vague_permission_note(tmp_path: Path) -> None:
    note = tmp_path / "permission.md"
    sections = "\n\n".join(f"## {section}\n- [x] Recorded and reviewed." for section in REQUIRED_SECTIONS)
    note.write_text(f"# Thread Search Permission Note\n\n{sections}\n", encoding="utf-8")

    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        permission_note=note,
    )

    permission_item = next(item for item in report.items if item.key == "permission_note")
    assert report.ok is True
    assert permission_item.status == "warn"
    assert "missing required checklist items" in permission_item.summary
    assert "Permission source" in permission_item.evidence["missing_required_items"]


def test_evaluate_audit_warns_on_negative_deployment_decision(tmp_path: Path) -> None:
    note = write_valid_permission_note(tmp_path)
    note.write_text(
        note.read_text(encoding="utf-8").replace(
                "Decision to proceed or not proceed: proceed with public source-linked search.",
            "Decision to proceed or not proceed: do not deploy publicly.",
        ),
        encoding="utf-8",
    )

    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        permission_note=note,
    )

    permission_item = next(item for item in report.items if item.key == "permission_note")
    assert report.ok is True
    assert permission_item.status == "warn"
    assert "deployment decision is not affirmative" in permission_item.summary
    assert permission_item.evidence["deployment_decision"]["reason"] == "negative"


def test_evaluate_audit_fails_partial_payload() -> None:
    report = evaluate_audit(
        payload(threadmarks=1, expected=2, validation_ok=False, launch_ok=False),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
    )

    assert report.ok is False
    failed = {item.key for item in report.items if item.status == "fail"}
    assert {"corpus_size", "validation", "public_launch"}.issubset(failed)


def test_evaluate_audit_fails_excluded_category() -> None:
    report = evaluate_audit(
        payload(threadmarks=2, expected=2, categories=[1, 5]),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
    )

    assert report.ok is False
    category_item = next(item for item in report.items if item.key == "category_scope")
    assert category_item.status == "fail"
    assert category_item.evidence["excluded_present"] == [5]


def test_evaluate_audit_includes_passing_public_smoke_report() -> None:
    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        public_smoke_report={
            "ok": True,
            "base_url": "http://127.0.0.1:8765/",
            "items": [{"key": "healthz", "status": "pass", "summary": "ready", "evidence": {"status": 200}}],
        },
    )

    smoke_item = next(item for item in report.items if item.key == "public_smoke")
    assert report.ok is True
    assert smoke_item.status == "pass"
    assert smoke_item.evidence["base_url"] == "http://127.0.0.1:8765/"


def test_evaluate_audit_fails_failed_public_smoke_report() -> None:
    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        public_smoke_report={
            "ok": False,
            "base_url": "http://127.0.0.1:8765/",
            "items": [
                {
                    "key": "private_threadmark_route",
                    "status": "fail",
                    "summary": "full text exposed",
                    "evidence": {"status": 200},
                }
            ],
        },
    )

    smoke_item = next(item for item in report.items if item.key == "public_smoke")
    assert report.ok is False
    assert smoke_item.status == "fail"
    assert smoke_item.evidence["failed_items"] == ["private_threadmark_route"]


def test_evaluate_audit_checks_artifact_manifest(tmp_path: Path) -> None:
    note = write_valid_permission_note(tmp_path)
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    manifest = artifact_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "artifact": "thread-search-public-search-backend",
                "public_contact": PUBLIC_CONTACT,
                "removal_request_url": REMOVAL_REQUEST_URL,
                "database": artifact_database(artifact_dir),
                "index": {"threadmarks": 2},
                "validation": {"ok": True},
                "public_server_defaults": public_defaults(),
                "content_handling": content_handling(),
                "public_api_contract": api_contract(),
                "deployment_runtime_contract": deployment_runtime_contract(),
                "permission_note": {"provided": True, "exists": True, "ok": True, "sha256": "abc"},
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
    )

    artifact_item = next(item for item in report.items if item.key == "artifact_manifest")
    permission_item = next(item for item in report.items if item.key == "permission_note")
    assert report.ok is True
    assert permission_item.status == "pass"
    assert artifact_item.status == "pass"
    assert artifact_item.evidence["artifact_directory"]["ok"] is True
    assert artifact_item.evidence["artifact_directory"]["unexpected_files"] == []
    assert artifact_item.evidence["public_api_contract_ok"] is True
    assert artifact_item.evidence["deployment_runtime_contract_ok"] is True
    assert artifact_item.evidence["public_contact_errors"] == []


def test_evaluate_audit_rejects_unexpected_artifact_directory_files(tmp_path: Path) -> None:
    note = write_valid_permission_note(tmp_path)
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    (artifact_dir / "thread-search-threadmarks.jsonl").write_text("raw extracted text must not be here", encoding="utf-8")
    manifest = artifact_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "artifact": "thread-search-public-search-backend",
                "public_contact": PUBLIC_CONTACT,
                "removal_request_url": REMOVAL_REQUEST_URL,
                "database": artifact_database(artifact_dir),
                "index": {"threadmarks": 2},
                "validation": {"ok": True},
                "public_server_defaults": public_defaults(),
                "content_handling": content_handling(),
                "public_api_contract": api_contract(),
                "deployment_runtime_contract": deployment_runtime_contract(),
                "permission_note": {"provided": True, "exists": True, "ok": True, "sha256": "abc"},
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
    )

    artifact_item = next(item for item in report.items if item.key == "artifact_manifest")
    assert report.ok is False
    assert artifact_item.status == "fail"
    assert artifact_item.evidence["artifact_directory"]["ok"] is False
    assert artifact_item.evidence["artifact_directory"]["unexpected_files"] == ["thread-search-threadmarks.jsonl"]


def test_evaluate_audit_rejects_placeholder_artifact_contact_metadata(tmp_path: Path) -> None:
    note = write_valid_permission_note(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "artifact": "thread-search-public-search-backend",
                "public_contact": "mailto:operator@example.invalid",
                "removal_request_url": "https://search.example.invalid/removal",
                "database": artifact_database(tmp_path),
                "index": {"threadmarks": 2},
                "validation": {"ok": True},
                "public_server_defaults": public_defaults(),
                "content_handling": content_handling(),
                "public_api_contract": api_contract(),
                "deployment_runtime_contract": deployment_runtime_contract(),
                "permission_note": {"provided": True, "exists": True, "ok": True, "sha256": "abc"},
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
    )

    artifact_item = next(item for item in report.items if item.key == "artifact_manifest")
    assert report.ok is False
    assert artifact_item.status == "fail"
    assert artifact_item.evidence["public_contact_errors"] == [
        "public-contact must not be a placeholder for public artifact; got 'mailto:operator@example.invalid'",
        "removal-request-url must not be a placeholder for public artifact; got 'https://search.example.invalid/removal'",
    ]


def test_evaluate_audit_requires_permission_note_for_artifact_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "artifact": "thread-search-public-search-backend",
                "public_contact": PUBLIC_CONTACT,
                "removal_request_url": REMOVAL_REQUEST_URL,
                "database": artifact_database(tmp_path),
                "index": {"threadmarks": 2},
                "validation": {"ok": True},
                "public_server_defaults": public_defaults(),
                "content_handling": content_handling(),
                "public_api_contract": api_contract(),
                "deployment_runtime_contract": deployment_runtime_contract(),
                "permission_note": {"provided": False, "exists": False, "ok": False},
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=tmp_path / "missing.md",
    )

    failed = {item.key for item in report.items if item.status == "fail"}
    assert {"permission_note", "artifact_manifest"}.issubset(failed)


def test_evaluate_audit_rejects_missing_artifact_database(tmp_path: Path) -> None:
    note = write_valid_permission_note(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "artifact": "thread-search-public-search-backend",
                "public_contact": PUBLIC_CONTACT,
                "removal_request_url": REMOVAL_REQUEST_URL,
                "database": {"path": ARTIFACT_DB_NAME, "sha256": "missing", "size_bytes": 1},
                "index": {"threadmarks": 2},
                "validation": {"ok": True},
                "public_server_defaults": public_defaults(),
                "content_handling": content_handling(),
                "public_api_contract": api_contract(),
                "deployment_runtime_contract": deployment_runtime_contract(),
                "permission_note": {"provided": True, "exists": True, "ok": True, "sha256": "abc"},
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
    )

    artifact_item = next(item for item in report.items if item.key == "artifact_manifest")
    assert report.ok is False
    assert artifact_item.status == "fail"
    assert artifact_item.evidence["database"]["error"] == "artifact database is missing"


def test_evaluate_audit_rejects_artifact_database_checksum_mismatch(tmp_path: Path) -> None:
    note = write_valid_permission_note(tmp_path)
    database = artifact_database(tmp_path)
    database["sha256"] = "wrong"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "artifact": "thread-search-public-search-backend",
                "public_contact": PUBLIC_CONTACT,
                "removal_request_url": REMOVAL_REQUEST_URL,
                "database": database,
                "index": {"threadmarks": 2},
                "validation": {"ok": True},
                "public_server_defaults": public_defaults(),
                "content_handling": content_handling(),
                "public_api_contract": api_contract(),
                "deployment_runtime_contract": deployment_runtime_contract(),
                "permission_note": {"provided": True, "exists": True, "ok": True, "sha256": "abc"},
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
    )

    artifact_item = next(item for item in report.items if item.key == "artifact_manifest")
    assert report.ok is False
    assert artifact_item.status == "fail"
    assert artifact_item.evidence["database"]["error"] == "artifact database sha256 does not match manifest"


def test_evaluate_audit_rejects_unsafe_artifact_caps(tmp_path: Path) -> None:
    note = write_valid_permission_note(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "artifact": "thread-search-public-search-backend",
                "public_contact": PUBLIC_CONTACT,
                "removal_request_url": REMOVAL_REQUEST_URL,
                "database": artifact_database(tmp_path),
                "index": {"threadmarks": 2},
                "validation": {"ok": True},
                "public_server_defaults": public_defaults(public_rate_limit_per_minute=0),
                "content_handling": content_handling(),
                "public_api_contract": api_contract(),
                "deployment_runtime_contract": deployment_runtime_contract(),
                "permission_note": {"provided": True, "exists": True, "ok": True, "sha256": "abc"},
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
    )

    artifact_item = next(item for item in report.items if item.key == "artifact_manifest")
    assert report.ok is False
    assert artifact_item.status == "fail"
    assert artifact_item.evidence["public_cap_errors"] == [
        "public-rate-limit-per-minute must be at least 1 for public deployment; got 0"
    ]


def test_evaluate_audit_rejects_missing_public_source_attribution(tmp_path: Path) -> None:
    note = write_valid_permission_note(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "artifact": "thread-search-public-search-backend",
                "public_contact": PUBLIC_CONTACT,
                "removal_request_url": REMOVAL_REQUEST_URL,
                "database": artifact_database(tmp_path),
                "index": {"threadmarks": 2},
                "validation": {"ok": True},
                "public_server_defaults": public_defaults(),
                "content_handling": content_handling(public_ui_source_attribution=False),
                "public_api_contract": api_contract(),
                "deployment_runtime_contract": deployment_runtime_contract(),
                "permission_note": {"provided": True, "exists": True, "ok": True, "sha256": "abc"},
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
    )

    artifact_item = next(item for item in report.items if item.key == "artifact_manifest")
    assert report.ok is False
    assert artifact_item.status == "fail"
    assert artifact_item.evidence["content_handling"]["public_ui_source_attribution"] is False


def test_evaluate_audit_requires_grouped_search_contract(tmp_path: Path) -> None:
    note = write_valid_permission_note(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "artifact": "thread-search-public-search-backend",
                "public_contact": PUBLIC_CONTACT,
                "removal_request_url": REMOVAL_REQUEST_URL,
                "database": artifact_database(tmp_path),
                "index": {"threadmarks": 2},
                "validation": {"ok": True},
                "public_server_defaults": public_defaults(),
                "content_handling": content_handling(),
                "public_api_contract": api_contract(grouped_search_endpoint_enabled=False),
                "deployment_runtime_contract": deployment_runtime_contract(),
                "permission_note": {"provided": True, "exists": True, "ok": True, "sha256": "abc"},
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
    )

    artifact_item = next(item for item in report.items if item.key == "artifact_manifest")
    assert report.ok is False
    assert artifact_item.status == "fail"
    assert artifact_item.evidence["public_api_contract_ok"] is False


def test_evaluate_audit_requires_word_variants_contract(tmp_path: Path) -> None:
    note = write_valid_permission_note(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "artifact": "thread-search-public-search-backend",
                "public_contact": PUBLIC_CONTACT,
                "removal_request_url": REMOVAL_REQUEST_URL,
                "database": artifact_database(tmp_path),
                "index": {"threadmarks": 2},
                "validation": {"ok": True},
                "public_server_defaults": public_defaults(),
                "content_handling": content_handling(),
                "public_api_contract": api_contract(word_variants_always_enabled=False),
                "deployment_runtime_contract": deployment_runtime_contract(),
                "permission_note": {"provided": True, "exists": True, "ok": True, "sha256": "abc"},
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
    )

    artifact_item = next(item for item in report.items if item.key == "artifact_manifest")
    assert report.ok is False
    assert artifact_item.status == "fail"
    assert artifact_item.evidence["public_api_contract_ok"] is False


def test_evaluate_audit_requires_deployment_runtime_contract(tmp_path: Path) -> None:
    note = write_valid_permission_note(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "artifact": "thread-search-public-search-backend",
                "public_contact": PUBLIC_CONTACT,
                "removal_request_url": REMOVAL_REQUEST_URL,
                "database": artifact_database(tmp_path),
                "index": {"threadmarks": 2},
                "validation": {"ok": True},
                "public_server_defaults": public_defaults(),
                "content_handling": content_handling(),
                "public_api_contract": api_contract(),
                "deployment_runtime_contract": deployment_runtime_contract(
                    public_private_fulltext_allowed=True,
                ),
                "permission_note": {"provided": True, "exists": True, "ok": True, "sha256": "abc"},
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_audit(
        payload(threadmarks=2, expected=2),
        expected_threadmarks=2,
        expected_category=1,
        excluded_categories=(4, 5),
        probes=("Cuba",),
        artifact_manifest=manifest,
        permission_note=note,
    )

    artifact_item = next(item for item in report.items if item.key == "artifact_manifest")
    assert report.ok is False
    assert artifact_item.status == "fail"
    assert artifact_item.evidence["deployment_runtime_contract_ok"] is False
