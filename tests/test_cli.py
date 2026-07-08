import json
from pathlib import Path

from planquest import cli
from planquest.artifact import export_public_artifact, sha256_file
from planquest.audit import AuditReport
from planquest.indexer import build_index
from planquest.models import Threadmark
from planquest.permission import REQUIRED_CHECKLIST_ITEMS, REQUIRED_SECTIONS
from planquest.preview import PreviewState, write_preview_state
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


def test_suggest_cli_marks_near_matches(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1, "Cuba Cuba is discussed directly.")])

    result = cli.main(["suggest", "Cubaa", "--db", str(db)])
    captured = capsys.readouterr()

    assert result == 0
    assert "cuba" in captured.out
    assert "near match: 1 edit(s)" in captured.out


def test_terms_cli_emits_metadata_only_json(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1, "Cuba and Cuban trade are discussed directly.")])

    result = cli.main(["terms", "--prefix", "Cub", "--db", str(db), "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["kind"] == "thread-search-term-index"
    assert payload["metadata_only"] is True
    assert payload["prefix"] == "cub"
    assert [item["term"] for item in payload["terms"]] == ["cuba", "cuban"]
    assert "body" not in captured.out
    assert "snippet" not in captured.out


def test_explain_cli_emits_metadata_only_json(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1, "Cuban exchange programs are discussed directly.")])

    result = cli.main(["explain", "Cuba", "--db", str(db), "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["kind"] == "thread-search-query-explain"
    assert payload["metadata_only"] is True
    assert payload["exact"]["total_threadmarks"] == 0
    assert payload["prefix"]["total_threadmarks"] == 1
    assert payload["resolved"]["match_kind"] == "prefix"
    assert [item["code"] for item in payload["cautions"]] == ["exact-missing-prefix-available"]
    assert "body" not in captured.out
    assert "snippet" not in captured.out


def test_explain_cli_reports_multi_term_breakdown(tmp_path: Path, capsys) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Communist parties are discussed separately."),
        ],
    )

    result = cli.main(["explain", "Cuba communist", "--db", str(db)])
    captured = capsys.readouterr()

    assert result == 0
    assert "term breakdown:" in captured.out
    assert "Cuba: exact 0 threadmarks/0 chunks, prefix 1 threadmarks/1 chunks" in captured.out
    assert "communist: exact 1 threadmarks/1 chunks, prefix 1 threadmarks/1 chunks" in captured.out
    assert "individual-terms-only" in captured.out
    assert "body" not in captured.out
    assert "snippet" not in captured.out


def test_search_cli_can_emit_json_with_match_diagnostics(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1, "Cuban exchange programs are discussed.")])

    result = cli.main(["search", "Cuba", "--db", str(db), "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["query"] == "Cuba"
    assert payload["grouped"] is True
    assert payload["match_kind"] == "prefix"
    assert payload["match_query"] == '"Cuba"*'
    assert payload["result_count"] == 1
    assert payload["total_threadmarks"] == 1
    assert payload["total_chunks"] == 1
    assert payload["results"][0]["match_kind"] == "prefix"
    assert payload["results"][0]["threadmark_order"] == 1
    assert "snippet" in payload["results"][0]
    assert "body" not in payload["results"][0]


def test_search_cli_prefix_variants_include_exact_and_prefix_hits(tmp_path: Path, capsys) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Cuba appears directly."),
        ],
    )

    result = cli.main(
        [
            "search",
            "Cuba",
            "--db",
            str(db),
            "--sort",
            "timeline",
            "--prefix-variants",
            "--format",
            "json",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["prefix_variants"] is True
    assert payload["match_kind"] == "prefix-variants"
    assert payload["match_query"] == '"Cuba"*'
    assert [item["threadmark_order"] for item in payload["results"]] == [1, 2]


def test_search_cli_can_sort_json_by_timeline(tmp_path: Path, capsys) -> None:
    db = build_db(
        tmp_path,
        [
            record(2, "Cuba appears in a later turn. Cuba appears again."),
            record(1, "Cuba appears in an early turn."),
        ],
    )

    result = cli.main(["search", "Cuba", "--db", str(db), "--sort", "timeline", "--limit", "2", "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["sort"] == "timeline"
    assert [item["threadmark_order"] for item in payload["results"]] == [1, 2]


def test_search_cli_accepts_alias_terms(tmp_path: Path, capsys) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuba appears in an early turn."),
            record(2, "Castro appears in a later turn."),
        ],
    )

    result = cli.main(
        ["search", "Cuba", "--alias", "Castro", "--db", str(db), "--sort", "timeline", "--format", "json"]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["aliases"] == ["Castro"]
    assert [term["query"] for term in payload["terms"]] == ["Cuba", "Castro"]
    assert payload["total_threadmarks"] == 2
    assert payload["total_chunks"] == 2
    assert [item["threadmark_order"] for item in payload["results"]] == [1, 2]


def test_search_cli_json_reports_total_matches_beyond_limit(tmp_path: Path, capsys) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuba appears in an early turn."),
            record(2, "Cuba appears in a later turn."),
        ],
    )

    result = cli.main(["search", "Cuba", "--db", str(db), "--limit", "1", "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["result_count"] == 1
    assert payload["total_threadmarks"] == 2
    assert payload["total_chunks"] == 2


def test_public_smoke_cli_formats_report(monkeypatch, capsys) -> None:
    def fake_smoke(
        base_url: str,
        probes: tuple[str, ...],
        timeout: float,
        claim_pairs: tuple[tuple[str, str], ...] = (),
        require_artifact_manifest: bool = False,
    ) -> SmokeReport:
        assert base_url == "http://127.0.0.1:8765"
        assert probes == ("Cuba",)
        assert timeout == 3.0
        assert claim_pairs == (("Cuba", "communist"),)
        assert require_artifact_manifest is True
        return SmokeReport(
            ok=True,
            base_url=base_url,
            items=[
                SmokeItem(key="healthz", status="pass", summary="ready", evidence={"status": 200}),
                SmokeItem(
                    key="stats_public_contract",
                    status="pass",
                    summary="public stats",
                    evidence={
                        "status": 200,
                        "public_contact": PUBLIC_CONTACT,
                        "removal_request_url": REMOVAL_REQUEST_URL,
                        "artifact_manifest_validated": True,
                        "artifact_manifest_sha256": "m" * 64,
                        "artifact_database_sha256": "d" * 64,
                        "artifact_created_at_utc": "2026-07-08T00:00:00Z",
                        "require_artifact_manifest": True,
                    },
                ),
                SmokeItem(
                    key="claim_pair:Cuba:communist",
                    status="pass",
                    summary="bounded claim",
                    evidence={
                        "status": 200,
                        "evidence_level": "strong-chunk-overlap",
                        "topic_query_exact_threadmarks": 1,
                        "topic_query_exact_chunks": 1,
                        "claim_query_exact_threadmarks": 1,
                        "claim_query_exact_chunks": 1,
                        "negation_cue_evidence": 1,
                    },
                ),
                SmokeItem(
                    key="explain_pair:Cuba:communist",
                    status="pass",
                    summary="metadata pair explain",
                    evidence={
                        "status": 200,
                        "query": "Cuba communist",
                        "term_breakdown": [
                            {
                                "query": "Cuba",
                                "exact_threadmarks": 0,
                                "exact_chunks": 0,
                                "prefix_threadmarks": 1,
                                "prefix_chunks": 1,
                                "resolved_threadmarks": 1,
                                "resolved_chunks": 1,
                                "resolved_match_kind": "prefix",
                            },
                            {
                                "query": "communist",
                                "exact_threadmarks": 55,
                                "exact_chunks": 168,
                                "prefix_threadmarks": 55,
                                "prefix_chunks": 168,
                                "resolved_threadmarks": 55,
                                "resolved_chunks": 168,
                                "resolved_match_kind": "exact",
                            },
                        ],
                    },
                ),
                SmokeItem(
                    key="explain_probe:Cuba",
                    status="pass",
                    summary="metadata explain",
                    evidence={
                        "status": 200,
                        "exact_threadmarks": 0,
                        "exact_chunks": 0,
                        "prefix_threadmarks": 1,
                        "prefix_chunks": 1,
                        "resolved_threadmarks": 1,
                        "resolved_chunks": 1,
                        "resolved_match_kind": "prefix",
                    },
                ),
            ],
        )

    monkeypatch.setattr(cli, "run_public_smoke", fake_smoke)

    result = cli.main(
        [
            "public-smoke",
            "--probe",
            "Cuba",
            "--require-artifact-manifest",
            "--claim-pair",
            "Cuba",
            "communist",
            "--timeout",
            "3",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "public-smoke: passed" in captured.out
    assert "pass: healthz - ready" in captured.out
    assert f"public_contact: {PUBLIC_CONTACT}" in captured.out
    assert f"removal_request_url: {REMOVAL_REQUEST_URL}" in captured.out
    assert "artifact_manifest_validated: True" in captured.out
    assert f"artifact_manifest_sha256: {'m' * 64}" in captured.out
    assert f"artifact_database_sha256: {'d' * 64}" in captured.out
    assert "artifact_created_at_utc: 2026-07-08T00:00:00Z" in captured.out
    assert "require_artifact_manifest: True" in captured.out
    assert "pass: claim_pair:Cuba:communist - bounded claim" in captured.out
    assert "evidence_level: strong-chunk-overlap" in captured.out
    assert "topic_query_exact_threadmarks: 1" in captured.out
    assert "topic_query_exact_chunks: 1" in captured.out
    assert "claim_query_exact_threadmarks: 1" in captured.out
    assert "claim_query_exact_chunks: 1" in captured.out
    assert "negation_cue_evidence: 1" in captured.out
    assert "pass: explain_pair:Cuba:communist - metadata pair explain" in captured.out
    assert "query: Cuba communist" in captured.out
    assert "term_breakdown: [{'query': 'Cuba'" in captured.out
    assert "pass: explain_probe:Cuba - metadata explain" in captured.out
    assert "exact_threadmarks: 0" in captured.out
    assert "prefix_threadmarks: 1" in captured.out
    assert "resolved_match_kind: prefix" in captured.out


def test_preview_start_cli_rejects_missing_contact_metadata(capsys) -> None:
    result = cli.main(["preview-start", "--no-tunnel", "--public-contact", "", "--removal-request-url", ""])
    captured = capsys.readouterr()

    assert result == 1
    assert "public-contact is required for public preview" in captured.err
    assert "removal-request-url is required for public preview" in captured.err


def test_preview_status_cli_can_smoke_recorded_url(tmp_path: Path, monkeypatch, capsys) -> None:
    state = tmp_path / "state.json"
    write_preview_state(
        state,
        PreviewState(
            started_at_utc="2026-07-08T00:00:00Z",
            local_base_url="http://127.0.0.1:8765",
            public_base_url="https://preview.example.invalid",
            server_pid=None,
            tunnel_pid=None,
            server_log=str(tmp_path / "server.log"),
            tunnel_log=str(tmp_path / "tunnel.log"),
            server_command=[],
            tunnel_command=[],
        ),
    )

    def fake_smoke(
        base_url: str,
        probes: tuple[str, ...],
        timeout: float,
        claim_pairs: tuple[tuple[str, str], ...] = (),
        require_artifact_manifest: bool = False,
    ) -> SmokeReport:
        assert base_url == "https://preview.example.invalid"
        assert probes == ("Soviet", "Cuba")
        assert timeout == 2.0
        assert claim_pairs == (("Cuba", "communist"),)
        assert require_artifact_manifest is True
        return SmokeReport(
            ok=True,
            base_url=base_url,
            items=[SmokeItem(key="healthz", status="pass", summary="ready", evidence={"status": 200})],
        )

    monkeypatch.setattr(cli, "run_public_smoke", fake_smoke)

    result = cli.main(
        [
            "preview-status",
            "--state",
            str(state),
            "--smoke",
            "--probe",
            "Soviet",
            "--probe",
            "Cuba",
            "--claim-pair",
            "Cuba",
            "communist",
            "--timeout",
            "2",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "public_base_url: https://preview.example.invalid" in captured.out
    assert "public-smoke: passed" in captured.out


def test_audit_cli_can_include_public_smoke(monkeypatch, tmp_path: Path, capsys) -> None:
    def fake_status(args, probes: tuple[str, ...]) -> dict[str, object]:
        assert probes == ("Cuba",)
        return {
            "crawl": {
                "reader_root": "https://example.invalid/reader/",
                "robots_allowed": True,
                "user_agent": "thread-search-test",
                "page_count": 1,
                "cached_pages": 1,
                "network_pages_if_run_now": 0,
            },
            "corpus": {"exists": True, "ok": True, "path": "records.jsonl", "threadmarks": 1, "words": 10, "categories": [1]},
            "index": {
                "exists": True,
                "ok": True,
                "path": "records.sqlite",
                "threadmarks": 1,
                "chunks": 1,
                "stored_chunks": 1,
                "words": 10,
                "categories": [1],
            },
            "fetch_log": {"exists": True, "ok": True, "path": "fetch-log.jsonl", "entries": 1, "page_fetches": 1},
            "validation": {"ok": True, "checks": ["probe 'Cuba': 1 result(s)"], "errors": []},
            "launch_check": {"ok": True, "checks": ["public full-text routes: disabled"], "errors": []},
        }

    def fake_smoke(
        base_url: str,
        probes: tuple[str, ...],
        timeout: float,
        claim_pairs: tuple[tuple[str, str], ...] = (),
        require_artifact_manifest: bool = False,
    ) -> SmokeReport:
        assert base_url == "http://127.0.0.1:8765"
        assert probes == ("Cuba",)
        assert timeout == 2.0
        assert claim_pairs == (("Cuba", "communist"),)
        assert require_artifact_manifest is False
        return SmokeReport(
            ok=True,
            base_url=base_url,
            items=[SmokeItem(key="healthz", status="pass", summary="ready", evidence={"status": 200})],
        )

    monkeypatch.setattr(cli, "make_status_payload", fake_status)
    monkeypatch.setattr(cli, "run_public_smoke", fake_smoke)

    result = cli.main(
        [
            "audit",
            "--probe",
            "Cuba",
            "--expected-threadmarks",
            "1",
            "--permission-note",
            str(tmp_path / "missing.md"),
            "--public-base-url",
            "http://127.0.0.1:8765",
            "--claim-pair",
            "Cuba",
            "communist",
            "--smoke-timeout",
            "2",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "pass: public_smoke - Live public HTTP smoke check passes." in captured.out
    assert "base_url: http://127.0.0.1:8765" in captured.out


def test_audit_cli_requires_manifest_signal_when_artifact_manifest_is_provided(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    manifest = tmp_path / "manifest.json"

    def fake_status(args, probes: tuple[str, ...]) -> dict[str, object]:
        assert probes == ("Cuba",)
        return {
            "crawl": {"robots_allowed": True},
            "corpus": {},
            "index": {},
            "fetch_log": {},
            "validation": {},
            "launch_check": {},
        }

    def fake_smoke(
        base_url: str,
        probes: tuple[str, ...],
        timeout: float,
        claim_pairs: tuple[tuple[str, str], ...] = (),
        require_artifact_manifest: bool = False,
    ) -> SmokeReport:
        assert base_url == "http://127.0.0.1:8765"
        assert probes == ("Cuba",)
        assert timeout == 5.0
        assert claim_pairs == ()
        assert require_artifact_manifest is True
        return SmokeReport(
            ok=True,
            base_url=base_url,
            items=[SmokeItem(key="stats_public_contract", status="pass", summary="public stats")],
        )

    def fake_evaluate(payload: dict[str, object], **kwargs) -> AuditReport:
        assert kwargs["artifact_manifest"] == manifest
        assert kwargs["public_smoke_report"]["ok"] is True
        return AuditReport(ok=True, generated_at_utc="2026-07-08T00:00:00Z", items=[])

    monkeypatch.setattr(cli, "make_status_payload", fake_status)
    monkeypatch.setattr(cli, "run_public_smoke", fake_smoke)
    monkeypatch.setattr(cli, "evaluate_audit", fake_evaluate)

    result = cli.main(
        [
            "audit",
            "--probe",
            "Cuba",
            "--artifact-manifest",
            str(manifest),
            "--public-base-url",
            "http://127.0.0.1:8765",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "audit: passed" in captured.out


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


def test_serve_require_launch_ready_allows_ready_db(tmp_path: Path, monkeypatch) -> None:
    db = build_db(tmp_path, [record(1), record(2)])
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

    assert result == 0
    assert called is True


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
    called = False
    captured_kwargs: dict[str, object] = {}

    def fake_serve(*args, **kwargs) -> None:
        nonlocal called
        called = True
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
    assert called is True
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


def test_serve_require_artifact_manifest_rejects_public_chunk_override(
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
            "--allow-public-chunk-results",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert called is False
    assert "allow-public-chunk-results to remain disabled" in captured.err


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


def test_serve_require_artifact_manifest_rejects_missing_manifest(tmp_path: Path, monkeypatch, capsys) -> None:
    db = build_db(tmp_path, [record(1), record(2)])
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    artifact_db = artifact_dir / "thread-search.sqlite"
    artifact_db.write_bytes(db.read_bytes())
    called = False

    def fake_serve(*args, **kwargs) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "serve", fake_serve)

    result = cli.main(
        [
            "serve",
            "--db",
            str(artifact_db),
            "--require-artifact-manifest",
            "--expected-threadmarks",
            "2",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert called is False
    assert "Artifact manifest is missing" in captured.err


def test_serve_require_artifact_manifest_rejects_mismatched_db(tmp_path: Path, monkeypatch, capsys) -> None:
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
            str(db),
            "--require-artifact-manifest",
            "--artifact-manifest",
            str(artifact.manifest_path),
            "--expected-threadmarks",
            "2",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert called is False
    assert "artifact manifest must be adjacent" in captured.err


def test_serve_non_loopback_requires_launch_gate(tmp_path: Path, monkeypatch, capsys) -> None:
    db = build_db(tmp_path, [record(1), record(2)])
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
            "--host",
            "0.0.0.0",
            "--expected-threadmarks",
            "2",
            "--probe",
            "Cuba",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert called is False
    assert "without --require-launch-ready" in captured.err


def test_serve_non_loopback_requires_artifact_manifest_gate(tmp_path: Path, monkeypatch, capsys) -> None:
    db = build_db(tmp_path, [record(1), record(2)])
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
            "--host",
            "0.0.0.0",
            "--require-launch-ready",
            "--expected-threadmarks",
            "2",
            "--probe",
            "Cuba",
            "--public-contact",
            PUBLIC_CONTACT,
            "--removal-request-url",
            REMOVAL_REQUEST_URL,
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert called is False
    assert "without --require-artifact-manifest" in captured.err


def test_serve_non_loopback_allows_explicit_unmanifested_snippet_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = build_db(tmp_path, [record(1), record(2)])
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
            "--host",
            "0.0.0.0",
            "--require-launch-ready",
            "--allow-unmanifested-public-bind",
            "--expected-threadmarks",
            "2",
            "--probe",
            "Cuba",
            "--public-contact",
            PUBLIC_CONTACT,
            "--removal-request-url",
            REMOVAL_REQUEST_URL,
        ]
    )

    assert result == 0
    assert called is True


def test_serve_non_loopback_requires_public_contact_metadata(tmp_path: Path, monkeypatch, capsys) -> None:
    db = build_db(tmp_path, [record(1), record(2)])
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
            "--host",
            "0.0.0.0",
            "--require-launch-ready",
            "--expected-threadmarks",
            "2",
            "--probe",
            "Cuba",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert called is False
    assert "public-contact is required for non-loopback serving" in captured.err
    assert "removal-request-url is required for non-loopback serving" in captured.err


def test_serve_non_loopback_rejects_placeholder_public_contact_metadata(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    db = build_db(tmp_path, [record(1), record(2)])
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
            "--host",
            "0.0.0.0",
            "--require-launch-ready",
            "--expected-threadmarks",
            "2",
            "--probe",
            "Cuba",
            "--public-contact",
            "mailto:operator@example.invalid",
            "--removal-request-url",
            "https://search.example.invalid/removal",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert called is False
    assert "public-contact must not be a placeholder for non-loopback serving" in captured.err
    assert "removal-request-url must not be a placeholder for non-loopback serving" in captured.err


def test_serve_non_loopback_rejects_private_fulltext(tmp_path: Path, monkeypatch, capsys) -> None:
    db = build_db(tmp_path, [record(1), record(2)])
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
            "--host",
            "0.0.0.0",
            "--private-fulltext",
            "--allow-unguarded-public-bind",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert called is False
    assert "refusing to expose --private-fulltext" in captured.err


def test_serve_non_loopback_rejects_disabled_public_rate_limit(tmp_path: Path, monkeypatch, capsys) -> None:
    db = build_db(tmp_path, [record(1), record(2)])
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
            "--host",
            "0.0.0.0",
            "--require-launch-ready",
            "--expected-threadmarks",
            "2",
            "--probe",
            "Cuba",
            "--public-rate-limit-per-minute",
            "0",
            "--public-contact",
            PUBLIC_CONTACT,
            "--removal-request-url",
            REMOVAL_REQUEST_URL,
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert called is False
    assert "public-rate-limit-per-minute must be at least 1" in captured.err


def test_serve_non_loopback_can_explicitly_override_public_caps(tmp_path: Path, monkeypatch) -> None:
    db = build_db(tmp_path, [record(1), record(2)])
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
            "--host",
            "0.0.0.0",
            "--require-launch-ready",
            "--expected-threadmarks",
            "2",
            "--probe",
            "Cuba",
            "--public-rate-limit-per-minute",
            "0",
            "--allow-unsafe-public-caps",
            "--allow-unmanifested-public-bind",
            "--public-contact",
            PUBLIC_CONTACT,
            "--removal-request-url",
            REMOVAL_REQUEST_URL,
        ]
    )

    assert result == 0
    assert called is True


def test_serve_loopback_allows_private_fulltext(tmp_path: Path, monkeypatch) -> None:
    db = build_db(tmp_path, [record(1)])
    called = False

    def fake_serve(*args, **kwargs) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "serve", fake_serve)

    result = cli.main(["serve", "--db", str(db), "--host", "127.0.0.1", "--private-fulltext"])

    assert result == 0
    assert called is True


def test_claim_cli_reports_evidence_level(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1, "Cuba and communist theory appear together.")])

    result = cli.main(["claim", "Cuba", "communist", "--db", str(db), "--format", "json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 0
    assert '"evidence_level": "strong-chunk-overlap"' in captured.out
    assert '"claim_query": "communist"' in captured.out
    assert payload["topic_query_exact_threadmarks"] == 1
    assert payload["claim_query_exact_threadmarks"] == 1
    assert payload["cautions"] == []


def test_claim_cli_reports_negation_cues(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1, "Cuba did not turn communist in this timeline.")])

    result = cli.main(["claim", "Cuba", "communist", "--db", str(db), "--format", "json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 0
    assert payload["negation_cue_evidence"] == 1
    assert [item["code"] for item in payload["cautions"]] == ["negation-cues"]
    assert "did not" in payload["evidence"][0]["claim_negation_cues"]
    assert payload["evidence"][0]["proximity"] == "same-chunk"
    assert payload["evidence"][0]["chunk_distance"] == 0


def test_claim_cli_infers_question_style_query(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1, "Cuba did not turn communist in this timeline.")])

    result = cli.main(["claim", "did Cuba turn communist", "--db", str(db), "--format", "json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 0
    assert payload["claim_inferred_from_query"] is True
    assert payload["original_query"] == "did Cuba turn communist"
    assert payload["topic_query"] == "Cuba"
    assert payload["claim_query"] == "communist"
    assert payload["evidence_level"] == "strong-chunk-overlap"
    assert payload["negation_cue_evidence"] == 1
    assert [item["code"] for item in payload["cautions"]] == ["negation-cues"]


def test_claim_cli_rejects_unsplit_single_topic(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1, "Cuba appears here.")])

    result = cli.main(["claim", "Cuba", "--db", str(db), "--format", "json"])

    captured = capsys.readouterr()
    assert result == 2
    assert "claim query required" in captured.err
    assert captured.out == ""


def test_claim_cli_accepts_topic_alias_terms(tmp_path: Path, capsys) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Castro and communist theory appear together."),
            record(2, "Cuban exchange programs are discussed separately."),
        ],
    )

    result = cli.main(["claim", "Cuba", "communist", "--alias", "Castro", "--db", str(db), "--format", "json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 0
    assert payload["topic_aliases"] == ["Castro"]
    assert [term["query"] for term in payload["topic_terms"]] == ["Cuba", "Castro"]
    assert payload["evidence_level"] == "strong-chunk-overlap"
    assert payload["overlapping_chunks"] == 1


def test_evidence_pack_cli_bundles_dossier_and_claim_json(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1, "Cuba did not turn communist in this timeline.")])

    result = cli.main(
        [
            "evidence-pack",
            "Cuba",
            "--claim",
            "communist",
            "--db",
            str(db),
            "--format",
            "json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 0
    assert payload["kind"] == "thread-search-evidence-pack"
    assert payload["bounded_retrieval_only"] is True
    assert payload["dossier"]["query"] == "Cuba"
    assert payload["dossier"]["total_threadmarks"] == 1
    assert payload["claims"][0]["claim_query"] == "communist"
    assert payload["claims"][0]["evidence_level"] == "strong-chunk-overlap"
    assert payload["claims"][0]["negation_cue_evidence"] == 1


def test_evidence_pack_cli_infers_question_style_claim(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1, "Cuba did not turn communist in this timeline.")])

    result = cli.main(["evidence-pack", "did Cuba turn communist", "--db", str(db), "--format", "json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 0
    assert payload["kind"] == "thread-search-evidence-pack"
    assert payload["query"] == "Cuba"
    assert payload["claim_inferred_from_query"] is True
    assert payload["original_query"] == "did Cuba turn communist"
    assert payload["dossier"]["query"] == "Cuba"
    assert payload["claims"][0]["claim_query"] == "communist"
    assert payload["claims"][0]["evidence_level"] == "strong-chunk-overlap"


def test_evidence_pack_cli_writes_markdown_file(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1, "Cuba did not turn communist in this timeline.")])
    out = tmp_path / "pack.md"

    result = cli.main(
        [
            "evidence-pack",
            "Cuba",
            "--claim",
            "communist",
            "--db",
            str(db),
            "--out",
            str(out),
        ]
    )

    captured = capsys.readouterr()
    rendered = out.read_text(encoding="utf-8")
    assert result == 0
    assert f"wrote: {out}" in captured.out
    assert "# Evidence pack: Cuba" in rendered
    assert "Bounded retrieval evidence only" in rendered
    assert "Evidence level: `strong-chunk-overlap`" in rendered
    assert "Exact topic query: 1 threadmarks, 1 chunks. Exact claim query: 1 threadmarks, 1 chunks." in rendered
    assert "Proximity: `same-chunk`" in rendered
    assert "Negation cues: `did not`" in rendered


def test_recap_cli_outputs_extractively_bounded_json(tmp_path: Path, capsys) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuba did not turn communist in this timeline."),
            record(2, "Cuba trade policy continues later."),
        ],
    )

    result = cli.main(
        [
            "recap",
            "Cuba",
            "--claim",
            "communist",
            "--db",
            str(db),
            "--format",
            "json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 0
    assert payload["kind"] == "thread-search-topic-recap"
    assert payload["bounded_retrieval_only"] is True
    assert payload["total_threadmarks"] == 2
    assert [item["threadmark_order"] for item in payload["timeline"]] == [1, 2]
    assert payload["claims"][0]["claim_query"] == "communist"
    assert payload["claims"][0]["evidence_level"] == "strong-chunk-overlap"
    assert "body" not in payload["timeline"][0]


def test_recap_cli_infers_question_style_claim(tmp_path: Path, capsys) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuba did not turn communist in this timeline."),
            record(2, "Cuba trade policy continues later."),
        ],
    )

    result = cli.main(["recap", "did Cuba turn communist", "--db", str(db), "--format", "json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 0
    assert payload["kind"] == "thread-search-topic-recap"
    assert payload["query"] == "Cuba"
    assert payload["claim_inferred_from_query"] is True
    assert payload["original_query"] == "did Cuba turn communist"
    assert [item["threadmark_order"] for item in payload["timeline"]] == [1, 2]
    assert payload["claims"][0]["claim_query"] == "communist"
    assert payload["claims"][0]["evidence_level"] == "strong-chunk-overlap"


def test_recap_cli_keeps_plain_multiword_topic(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1, "Soviet Union policy appears in this update.")])

    result = cli.main(["recap", "Soviet Union", "--db", str(db), "--format", "json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 0
    assert payload["query"] == "Soviet Union"
    assert payload["claims"] == []
    assert "claim_inferred_from_query" not in payload
    assert payload["total_threadmarks"] == 1


def test_recap_cli_writes_markdown_file(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1, "Cuba did not turn communist in this timeline.")])
    out = tmp_path / "recap.md"

    result = cli.main(["recap", "Cuba", "--claim", "communist", "--db", str(db), "--out", str(out)])

    captured = capsys.readouterr()
    rendered = out.read_text(encoding="utf-8")
    assert result == 0
    assert f"wrote: {out}" in captured.out
    assert "# Topic recap: Cuba" in rendered
    assert "Bounded extractive recap only" in rendered
    assert "## Timeline" in rendered
    assert "Evidence level: `strong-chunk-overlap`" in rendered
    assert "Exact topic query: 1 threadmarks, 1 chunks. Exact claim query: 1 threadmarks, 1 chunks." in rendered


def test_dossier_cli_reports_bounded_retrieval_bundle(tmp_path: Path, capsys) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuba sugar policy."),
            record(2, "Cuba trade policy. Cuba policy again."),
        ],
    )

    result = cli.main(
        [
            "dossier",
            "Cuba",
            "--db",
            str(db),
            "--threadmark-limit",
            "10",
            "--mention-limit",
            "10",
            "--format",
            "json",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert '"total_threadmarks": 2' in captured.out
    assert '"total_mentions": 2' in captured.out
    assert '"timeline"' in captured.out
    assert '"mention_windows"' in captured.out
    assert '"body"' not in captured.out


def test_dossier_cli_accepts_alias_terms(tmp_path: Path, capsys) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Castro appears in a separate update."),
        ],
    )

    result = cli.main(["dossier", "Cuba", "--alias", "Castro", "--db", str(db), "--format", "json"])

    captured = capsys.readouterr()
    assert result == 0
    assert '"aliases": [' in captured.out
    assert '"Castro"' in captured.out
    assert '"total_threadmarks": 2' in captured.out


def test_mentions_cli_accepts_alias_terms(tmp_path: Path, capsys) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuba appears in one update."),
            record(2, "Castro appears in another update."),
        ],
    )

    result = cli.main(["mentions", "Cuba", "--alias", "Castro", "--db", str(db), "--format", "json"])

    captured = capsys.readouterr()
    assert result == 0
    assert '"aliases": [' in captured.out
    assert '"Castro"' in captured.out
    assert '"total_threadmarks": 2' in captured.out
    assert '"total_mentions": 2' in captured.out


def test_coverage_cli_reports_metadata_without_snippets(tmp_path: Path, capsys) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Castro appears in a separate update."),
        ],
    )

    result = cli.main(
        ["coverage", "Cuba", "--alias", "Castro", "--bucket-size", "1", "--db", str(db), "--format", "json"]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 0
    assert payload["aliases"] == ["Castro"]
    assert payload["total_threadmarks"] == 2
    assert [(bucket["start_order"], bucket["end_order"]) for bucket in payload["buckets"]] == [(1, 1), (2, 2)]
    assert "items" in payload
    assert '"snippet"' not in captured.out
    assert '"best_snippet"' not in captured.out
    assert '"body"' not in captured.out


def test_compare_cli_reports_metadata_without_snippets(tmp_path: Path, capsys) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuba appears in an early update."),
            record(2, "Cuba and communist parties appear together."),
            record(3, "Soviet planning appears elsewhere."),
        ],
    )

    result = cli.main(["compare", "Cuba", "communist", "--bucket-size", "1", "--db", str(db), "--format", "json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 0
    assert payload["kind"] == "thread-search-topic-comparison"
    assert payload["metadata_only"] is True
    assert payload["queries"] == ["Cuba", "communist"]
    assert [topic["total_threadmarks"] for topic in payload["topics"]] == [2, 1]
    assert payload["all_overlap"]["total_threadmarks"] == 1
    assert payload["all_overlap"]["items"][0]["threadmark_order"] == 2
    assert payload["pairwise_overlaps"][0]["total_threadmarks"] == 1
    assert '"snippet"' not in captured.out
    assert '"best_snippet"' not in captured.out
    assert '"body"' not in captured.out


def test_report_cli_runs_without_bucket_size_argument(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1, "Cuba appears here.")])

    result = cli.main(["report", "Cuba", "--db", str(db), "--format", "json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 0
    assert payload["query"] == "Cuba"
    assert payload["total_threadmarks"] == 1


def test_report_cli_accepts_alias_terms(tmp_path: Path, capsys) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuba appears in one update."),
            record(2, "Castro appears in another update."),
        ],
    )

    result = cli.main(["report", "Cuba", "--alias", "Castro", "--db", str(db), "--format", "json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 0
    assert payload["aliases"] == ["Castro"]
    assert [term["query"] for term in payload["terms"]] == ["Cuba", "Castro"]
    assert payload["total_threadmarks"] == 2
    assert payload["total_chunks"] == 2


def test_artifact_cli_rejects_unsafe_public_caps_before_export(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1)])
    note = tmp_path / "permission.md"
    note.write_text("## Author Permission\nReviewed.\n", encoding="utf-8")

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
            "--public-search-limit",
            "1000",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "public-search-limit must be at most 100" in captured.err
    assert not (tmp_path / "artifact").exists()


def test_artifact_cli_requires_public_contact_metadata(tmp_path: Path, capsys) -> None:
    db = build_db(tmp_path, [record(1)])
    note = write_valid_permission_note(tmp_path)

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
    assert "public-contact is required for public deployment" in captured.err
    assert "removal-request-url is required for public deployment" in captured.err
    assert "set --public-contact and --removal-request-url" in captured.err
    assert not (tmp_path / "artifact").exists()


def test_permission_note_cli_writes_and_checks_template(tmp_path: Path) -> None:
    note = tmp_path / "permission.md"

    write_result = cli.main(["permission-note", "--out", str(note)])
    check_result = cli.main(["permission-note", "--check", "--out", str(note)])

    assert write_result == 0
    assert check_result == 1
    assert "TODO" in note.read_text(encoding="utf-8")


def test_permission_request_cli_prints_and_writes_draft(tmp_path: Path, capsys) -> None:
    draft = tmp_path / "permission-request.md"

    print_result = cli.main(
        [
            "permission-request",
            "--public-base-url",
            "https://search.example.invalid",
            "--operator",
            "Test Operator",
        ]
    )
    captured = capsys.readouterr()
    write_result = cli.main(["permission-request", "--out", str(draft), "--contact", "operator@example.invalid"])

    assert print_result == 0
    assert "Thread Search Public Search Permission Request" in captured.out
    assert "https://search.example.invalid" in captured.out
    assert "Test Operator" in captured.out
    assert write_result == 0
    assert "operator@example.invalid" in draft.read_text(encoding="utf-8")


def test_permission_request_cli_refuses_existing_file(tmp_path: Path, capsys) -> None:
    draft = tmp_path / "permission-request.md"
    draft.write_text("existing", encoding="utf-8")

    result = cli.main(["permission-request", "--out", str(draft)])

    captured = capsys.readouterr()
    assert result == 1
    assert "already exists" in captured.err
    assert draft.read_text(encoding="utf-8") == "existing"


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
