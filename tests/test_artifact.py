from pathlib import Path
import json

import pytest

from planquest.artifact import (
    DEPLOYMENT_RUNTIME_CONTRACT,
    PUBLIC_API_ENDPOINTS,
    ArtifactCapError,
    ArtifactContactError,
    ArtifactError,
    ArtifactValidationError,
    export_public_artifact,
    sha256_file,
)
from planquest.indexer import build_index
from planquest.models import Threadmark
from planquest.permission import REQUIRED_CHECKLIST_ITEMS, REQUIRED_SECTIONS
from planquest.scrape import write_jsonl


PUBLIC_CONTACT = "mailto:operator@thread-search.example"
REMOVAL_REQUEST_URL = "https://thread-search.example/removal"


def record(order: int, text: str = "Cuba appears in this fixture.") -> Threadmark:
    return Threadmark(
        order=order,
        category_id=1,
        category_name="Threadmarks",
        threadmark_id=str(order),
        post_id=str(3000 + order),
        title=f"Turn {order}",
        author="Blackstar",
        published_at="2020-01-01T00:00:00-0500",
        source_url=f"https://forums.sufficientvelocity.com/threads/example.1/#post-{3000 + order}",
        reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
        text=text,
        word_count=len(text.split()),
    )


def build_fixture_db(tmp_path: Path, records: list[Threadmark]) -> Path:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(records, jsonl)
    build_index(jsonl, db)
    return db


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


def test_export_public_artifact_copies_db_and_writes_manifest(tmp_path: Path) -> None:
    db = build_fixture_db(tmp_path, [record(1), record(2)])
    out_dir = tmp_path / "artifact"

    result = export_public_artifact(
        db_path=db,
        out_dir=out_dir,
        expected_threadmarks=2,
        probes=("Cuba",),
        public_search_limit=12,
        public_report_limit=34,
        public_mention_limit=45,
        public_threadmark_limit=46,
        max_query_chars=56,
        mention_window_chars=67,
        public_snippet_budget_chars=89,
        public_rate_limit_per_minute=78,
        public_contact=PUBLIC_CONTACT,
        removal_request_url=REMOVAL_REQUEST_URL,
    )

    assert result.database_path == out_dir / "thread-search.sqlite"
    assert result.database_path.exists()
    assert result.sha256 == sha256_file(result.database_path)
    assert {path.name for path in out_dir.iterdir()} == {
        "thread-search.sqlite",
        "manifest.json",
        "README.deploy.txt",
    }

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifact"] == "thread-search-public-search-backend"
    assert manifest["public_contact"] == PUBLIC_CONTACT
    assert manifest["removal_request_url"] == REMOVAL_REQUEST_URL
    assert manifest["index"]["threadmarks"] == 2
    assert manifest["database"]["sha256"] == result.sha256
    assert manifest["public_server_defaults"]["private_fulltext"] is False
    assert manifest["public_server_defaults"]["public_search_limit"] == 12
    assert manifest["public_server_defaults"]["public_report_limit"] == 34
    assert manifest["public_server_defaults"]["public_mention_limit"] == 45
    assert manifest["public_server_defaults"]["public_threadmark_limit"] == 46
    assert manifest["public_server_defaults"]["max_query_chars"] == 56
    assert manifest["public_server_defaults"]["mention_window_chars"] == 67
    assert manifest["public_server_defaults"]["public_snippet_budget_chars"] == 89
    assert manifest["public_server_defaults"]["public_rate_limit_per_minute"] == 78
    assert manifest["content_handling"]["raw_html_included"] is False
    assert manifest["content_handling"]["jsonl_included"] is False
    assert manifest["content_handling"]["database_must_not_be_static_or_downloadable"] is True
    assert manifest["content_handling"]["public_responses_are_source_linked_hits"] is True
    assert manifest["content_handling"]["public_ui_source_attribution"] is True
    assert manifest["content_handling"]["public_ui_contact_or_removal_notice_supported"] is True
    assert manifest["public_api_contract"]["public_endpoints"] == list(PUBLIC_API_ENDPOINTS)
    assert "/api/search" in manifest["public_api_contract"]["public_endpoints"]
    assert "/api/threadmarks" in manifest["public_api_contract"]["public_endpoints"]
    assert "/api/dossier" not in manifest["public_api_contract"]["public_endpoints"]
    assert "/api/evidence-pack" not in manifest["public_api_contract"]["public_endpoints"]
    assert "/api/recap" not in manifest["public_api_contract"]["public_endpoints"]
    assert "/api/coverage" not in manifest["public_api_contract"]["public_endpoints"]
    assert "/api/compare" not in manifest["public_api_contract"]["public_endpoints"]
    assert "/api/terms" not in manifest["public_api_contract"]["public_endpoints"]
    assert "/api/explain" not in manifest["public_api_contract"]["public_endpoints"]
    assert "/api/claim" not in manifest["public_api_contract"]["public_endpoints"]
    assert manifest["public_api_contract"]["grouped_search_endpoint_enabled"] is True
    assert manifest["public_api_contract"]["word_variants_always_enabled"] is True
    assert manifest["public_api_contract"]["private_fulltext_endpoint_public"] is False
    assert manifest["public_api_contract"]["legacy_evidence_endpoints_public"] is False
    assert manifest["deployment_runtime_contract"] == DEPLOYMENT_RUNTIME_CONTRACT
    assert manifest["permission_note"] == {"exists": False, "ok": False, "provided": False}
    readme = result.readme_path.read_text(encoding="utf-8")
    assert "--require-launch-ready" in readme
    assert "--require-artifact-manifest" in readme
    assert "--probe Cuba" in readme
    assert "public-smoke" in readme
    assert "--require-artifact-manifest" in readme
    assert "artifact_manifest_validated: true" in readme
    assert "--claim-pair Cuba communist" not in readme
    assert "audit --artifact-manifest" in readme
    assert "--public-base-url" in readme
    assert "source attribution" in readme
    assert "THREAD_SEARCH_PUBLIC_CONTACT" in readme
    assert "THREAD_SEARCH_REMOVAL_REQUEST_URL" in readme
    assert "/api/search" in readme
    assert "/api/threadmarks" in readme
    assert "/api/dossier" not in readme
    assert "/api/coverage" not in readme
    assert "/api/compare" not in readme
    assert "/api/terms" not in readme
    assert "/api/explain" not in readme


def test_export_public_artifact_records_permission_note_hash(tmp_path: Path) -> None:
    db = build_fixture_db(tmp_path, [record(1), record(2)])
    note = write_valid_permission_note(tmp_path)
    out_dir = tmp_path / "artifact"

    result = export_public_artifact(
        db_path=db,
        out_dir=out_dir,
        expected_threadmarks=2,
        probes=("Cuba",),
        permission_note=note,
        public_contact=PUBLIC_CONTACT,
        removal_request_url=REMOVAL_REQUEST_URL,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["permission_note"]["provided"] is True
    assert manifest["permission_note"]["ok"] is True
    assert manifest["permission_note"]["path"] == str(note)
    assert manifest["permission_note"]["sha256"]


def test_export_public_artifact_refuses_incomplete_permission_note(tmp_path: Path) -> None:
    db = build_fixture_db(tmp_path, [record(1), record(2)])
    note = tmp_path / "permission.md"
    note.write_text("## Author Permission\nTODO\n", encoding="utf-8")
    out_dir = tmp_path / "artifact"

    with pytest.raises(ArtifactError) as error:
        export_public_artifact(
            db_path=db,
            out_dir=out_dir,
            expected_threadmarks=2,
            probes=("Cuba",),
            permission_note=note,
        )

    assert "permission note is incomplete" in str(error.value)
    assert not out_dir.exists()


def test_export_public_artifact_requires_public_contact_metadata(tmp_path: Path) -> None:
    db = build_fixture_db(tmp_path, [record(1), record(2)])
    note = write_valid_permission_note(tmp_path)
    out_dir = tmp_path / "artifact"

    with pytest.raises(ArtifactContactError) as error:
        export_public_artifact(
            db_path=db,
            out_dir=out_dir,
            expected_threadmarks=2,
            probes=("Cuba",),
            permission_note=note,
        )

    assert error.value.errors == [
        "public-contact is required for public deployment",
        "removal-request-url is required for public deployment",
    ]
    assert not out_dir.exists()


def test_export_public_artifact_rejects_placeholder_contact_metadata(tmp_path: Path) -> None:
    db = build_fixture_db(tmp_path, [record(1), record(2)])
    note = write_valid_permission_note(tmp_path)
    out_dir = tmp_path / "artifact"

    with pytest.raises(ArtifactContactError) as error:
        export_public_artifact(
            db_path=db,
            out_dir=out_dir,
            expected_threadmarks=2,
            probes=("Cuba",),
            permission_note=note,
            public_contact="mailto:operator@example.invalid",
            removal_request_url="https://search.example.invalid/removal",
        )

    assert error.value.errors == [
        "public-contact must not be a placeholder for public deployment; got 'mailto:operator@example.invalid'",
        "removal-request-url must not be a placeholder for public deployment; got 'https://search.example.invalid/removal'",
    ]
    assert not out_dir.exists()


def test_export_public_artifact_refuses_unready_database(tmp_path: Path) -> None:
    db = build_fixture_db(tmp_path, [record(1)])
    out_dir = tmp_path / "artifact"

    with pytest.raises(ArtifactValidationError) as error:
        export_public_artifact(db_path=db, out_dir=out_dir, expected_threadmarks=2, probes=("Cuba",))

    assert "expected 2" in "\n".join(error.value.result.errors)
    assert not out_dir.exists()


def test_export_public_artifact_refuses_unexpected_output_files(tmp_path: Path) -> None:
    db = build_fixture_db(tmp_path, [record(1)])
    out_dir = tmp_path / "artifact"
    out_dir.mkdir()
    (out_dir / "records.jsonl").write_text("do not include raw extracted text", encoding="utf-8")

    with pytest.raises(ArtifactError) as error:
        export_public_artifact(
            db_path=db,
            out_dir=out_dir,
            expected_threadmarks=1,
            probes=("Cuba",),
            public_contact=PUBLIC_CONTACT,
            removal_request_url=REMOVAL_REQUEST_URL,
        )

    assert "non-artifact files" in str(error.value)


def test_export_public_artifact_refuses_unsafe_public_caps(tmp_path: Path) -> None:
    db = build_fixture_db(tmp_path, [record(1)])
    out_dir = tmp_path / "artifact"

    with pytest.raises(ArtifactCapError) as error:
        export_public_artifact(
            db_path=db,
            out_dir=out_dir,
            expected_threadmarks=1,
            probes=("Cuba",),
            public_rate_limit_per_minute=0,
        )

    assert error.value.errors == [
        "public-rate-limit-per-minute must be at least 1 for public deployment; got 0"
    ]
    assert not out_dir.exists()


def test_export_public_artifact_allows_explicit_unsafe_cap_override(tmp_path: Path) -> None:
    db = build_fixture_db(tmp_path, [record(1)])
    out_dir = tmp_path / "artifact"

    result = export_public_artifact(
        db_path=db,
        out_dir=out_dir,
        expected_threadmarks=1,
        probes=("Cuba",),
        public_rate_limit_per_minute=0,
        allow_unsafe_public_caps=True,
        public_contact=PUBLIC_CONTACT,
        removal_request_url=REMOVAL_REQUEST_URL,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["public_server_defaults"]["public_rate_limit_per_minute"] == 0
