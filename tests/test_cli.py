import json
from pathlib import Path

from planquest import cli
from planquest.artifact import export_public_artifact, sha256_file
from planquest.indexer import build_index
from planquest.models import Threadmark
from planquest.permission import REQUIRED_CHECKLIST_ITEMS, REQUIRED_SECTIONS
from planquest.scrape import write_jsonl
from planquest.smoke import SmokeItem, SmokeReport


PUBLIC_CONTACT = "mailto:operator@thread-search.example"
REMOVAL_REQUEST_URL = "https://thread-search.example/removal"


def record(order: int, text: str = "Cuba appears here.") -> Threadmark:
    return Threadmark(
        order=order,
        category_id=1,
        category_name="Threadmarks",
        threadmark_id=str(order),
        post_id=str(4000 + order),
        title=f"Turn {order}",
        author="Blackstar",
        published_at="2020-01-01T00:00:00-0500",
        source_url=f"https://forums.sufficientvelocity.com/threads/example.1/#post-{4000 + order}",
        reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
        text=text,
        word_count=len(text.split()),
    )


def build_db(tmp_path: Path, records: list[Threadmark]) -> Path:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(records, jsonl)
    build_index(jsonl, db)
    return db


def test_search_cli_emits_full_text_search_json(tmp_path: Path, capsys) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuba appears in the first turn."),
            record(2, "Cuba appears in the second turn."),
        ],
    )

    result = cli.main(["search", "Cuba", "--db", str(db), "--format", "json", "--limit", "1"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["query"] == "Cuba"
    assert payload["result_count"] == 1
    assert payload["total_threadmarks"] == 2
    assert payload["total_chunks"] == 2


def test_toc_cli_emits_threadmark_metadata_json(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1), record(2)])

    result = cli.main(["toc", "--db", str(db), "--format", "json", "--from-order", "2"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert len(payload) == 1
    assert payload[0]["threadmark_order"] == 2
    assert "body" not in payload[0]


def test_public_smoke_cli_formats_search_report(monkeypatch, capsys) -> None:
    def fake_smoke(
        base_url: str,
        probes: tuple[str, ...],
        timeout: float = 5.0,
        require_artifact_manifest: bool = False,
    ) -> SmokeReport:
        assert base_url == "https://search.example.invalid"
        assert probes == ("Cuba",)
        assert timeout == 7
        assert require_artifact_manifest is True
        return SmokeReport(
            ok=True,
            base_url=base_url,
            items=[
                SmokeItem(
                    key="search_probe:Cuba",
                    status="pass",
                    summary="search ok",
                    evidence={"status": 200, "result_count": 1, "total_threadmarks": 1},
                )
            ],
        )

    monkeypatch.setattr(cli, "run_public_smoke", fake_smoke)

    result = cli.main(
        [
            "public-smoke",
            "--base-url",
            "https://search.example.invalid",
            "--probe",
            "Cuba",
            "--timeout",
            "7",
            "--require-artifact-manifest",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "public-smoke: passed" in captured.out
    assert "pass: search_probe:Cuba - search ok" in captured.out
    assert "result_count: 1" in captured.out


def test_serve_require_launch_ready_rejects_partial_db(tmp_path: Path, monkeypatch) -> None:
    db = build_db(tmp_path, [record(1)])
    called = False

    def fake_serve(*args, **kwargs) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "serve", fake_serve)

    result = cli.main(
        [
            "serve",
            "--db",
            str(db),
            "--require-launch-ready",
            "--expected-threadmarks",
            "2",
            "--probe",
            "Cuba",
        ]
    )

    assert result == 1
    assert called is False


def test_serve_passes_public_contact_notice_options(tmp_path: Path, monkeypatch) -> None:
    db = build_db(tmp_path, [record(1), record(2)])
    captured_kwargs: dict[str, object] = {}

    def fake_serve(*args, **kwargs) -> None:
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(cli, "serve", fake_serve)

    result = cli.main(
        [
            "serve",
            "--db",
            str(db),
            "--public-contact",
            PUBLIC_CONTACT,
            "--removal-request-url",
            REMOVAL_REQUEST_URL,
        ]
    )

    assert result == 0
    assert captured_kwargs["public_contact"] == PUBLIC_CONTACT
    assert captured_kwargs["removal_request_url"] == REMOVAL_REQUEST_URL
    assert captured_kwargs["artifact_manifest_validated"] is False


def test_serve_require_artifact_manifest_allows_valid_artifact(tmp_path: Path, monkeypatch, capsys) -> None:
    db = build_db(tmp_path, [record(1), record(2)])
    note = write_valid_permission_note(tmp_path)
    artifact = export_public_artifact(
        db_path=db,
        out_dir=tmp_path / "artifact",
        expected_threadmarks=2,
        probes=("Cuba",),
        permission_note=note,
        public_contact=PUBLIC_CONTACT,
        removal_request_url=REMOVAL_REQUEST_URL,
    )
    captured_kwargs: dict[str, object] = {}

    def fake_serve(*args, **kwargs) -> None:
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(cli, "serve", fake_serve)

    result = cli.main(
        [
            "serve",
            "--db",
            str(artifact.database_path),
            "--require-artifact-manifest",
            "--expected-threadmarks",
            "2",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured_kwargs["artifact_manifest_validated"] is True
    assert captured_kwargs["artifact_manifest_sha256"] == sha256_file(artifact.manifest_path)
    assert captured_kwargs["artifact_database_sha256"] == artifact.sha256
    assert "artifact manifest validated" in captured.out


def test_serve_require_artifact_manifest_rejects_runtime_cap_above_manifest(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    db = build_db(tmp_path, [record(1), record(2)])
    note = write_valid_permission_note(tmp_path)
    artifact = export_public_artifact(
        db_path=db,
        out_dir=tmp_path / "artifact",
        expected_threadmarks=2,
        probes=("Cuba",),
        public_search_limit=10,
        permission_note=note,
        public_contact=PUBLIC_CONTACT,
        removal_request_url=REMOVAL_REQUEST_URL,
    )
    called = False

    def fake_serve(*args, **kwargs) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "serve", fake_serve)

    result = cli.main(
        [
            "serve",
            "--db",
            str(artifact.database_path),
            "--require-artifact-manifest",
            "--expected-threadmarks",
            "2",
            "--public-search-limit",
            "11",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert called is False
    assert "public-search-limit exceeds artifact manifest default 10" in captured.err


def test_serve_require_artifact_manifest_rejects_private_fulltext(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    db = build_db(tmp_path, [record(1), record(2)])
    note = write_valid_permission_note(tmp_path)
    artifact = export_public_artifact(
        db_path=db,
        out_dir=tmp_path / "artifact",
        expected_threadmarks=2,
        probes=("Cuba",),
        permission_note=note,
        public_contact=PUBLIC_CONTACT,
        removal_request_url=REMOVAL_REQUEST_URL,
    )
    called = False

    def fake_serve(*args, **kwargs) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "serve", fake_serve)

    result = cli.main(
        [
            "serve",
            "--db",
            str(artifact.database_path),
            "--require-artifact-manifest",
            "--expected-threadmarks",
            "2",
            "--private-fulltext",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert called is False
    assert "private-fulltext to remain disabled" in captured.err


def test_artifact_cli_reports_incomplete_permission_items(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1)])
    note = tmp_path / "permission.md"
    note.write_text(
        "# Thread Search Permission Note\n\n"
        "## Author Permission\n- [ ] Permission source: TODO\n\n"
        "## Site Rules Review\n- [x] Reviewed.\n\n"
        "## Public Deployment Scope\n- [x] Snippet-only.\n\n"
        "## Operator Decision\n- [ ] Decision date: TODO\n",
        encoding="utf-8",
    )

    result = cli.main(
        [
            "artifact",
            "--db",
            str(db),
            "--out-dir",
            str(tmp_path / "artifact"),
            "--expected-threadmarks",
            "1",
            "--probe",
            "Cuba",
            "--permission-note",
            str(note),
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "permission note is incomplete or invalid" in captured.err
    assert "missing_required_items:" in captured.err
    assert "unchecked_items:" in captured.err
    assert "Permission source: TODO" in captured.err
    assert "permission-note --check" in captured.err
    assert not (tmp_path / "artifact").exists()


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
