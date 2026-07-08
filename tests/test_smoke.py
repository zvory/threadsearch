from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

from planquest.indexer import build_index
from planquest.models import Threadmark
from planquest.scrape import write_jsonl
from planquest.smoke import forbidden_key_paths, run_public_smoke
from planquest.web import SearchHandler

PUBLIC_CONTACT = "mailto:operator@thread-search.example"
REMOVAL_REQUEST_URL = "https://thread-search.example/removal"


def record(order: int, text: str) -> Threadmark:
    return Threadmark(
        order=order,
        category_id=1,
        category_name="Threadmarks",
        threadmark_id=str(order),
        post_id=str(5000 + order),
        title=f"Turn {order}",
        author="Blackstar",
        published_at="2020-01-01T00:00:00-0500",
        source_url=f"https://forums.sufficientvelocity.com/threads/example.1/#post-{5000 + order}",
        reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
        text=text,
        word_count=len(text.split()),
    )


def build_db(tmp_path: Path) -> Path:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba did not turn communist in this timeline."),
            record(2, "Soviet planning appears in another update."),
        ],
        jsonl,
    )
    build_index(jsonl, db)
    return db


def test_public_smoke_passes_public_snippet_server(tmp_path: Path) -> None:
    db = build_db(tmp_path)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None
        public_contact_value = PUBLIC_CONTACT
        removal_request_url_value = REMOVAL_REQUEST_URL

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        report = run_public_smoke(
            f"http://127.0.0.1:{server.server_port}",
            probes=("Cuba", "Soviet"),
            timeout=5,
            claim_pairs=(("Cuba", "communist"),),
        )

        assert report.ok is True
        items = {item.key: item for item in report.items}
        assert set(items) >= {
            "html_shell",
            "robots_txt",
            "healthz",
            "stats_public_contract",
            "search_probe:Cuba",
            "search_prefix_variants_probe:Cuba",
            "terms_probe:Cuba",
            "explain_probe:Cuba",
            "mentions_probe:Cuba",
            "coverage_probe:Cuba",
            "compare_probe:Cuba:Soviet",
            "dossier_probe:Cuba",
            "evidence_pack_probe:Cuba",
            "recap_probe:Cuba",
            "explain_pair:Cuba:communist",
            "claim_pair:Cuba:communist",
            "claim_q_only:Cuba:communist",
            "evidence_pack_q_only:Cuba:communist",
            "recap_q_only:Cuba:communist",
            "private_threadmark_route",
            "private_download_paths",
        }
        assert items["html_shell"].evidence["meta_robots"] is True
        assert items["html_shell"].evidence["share_link"] is True
        assert items["html_shell"].evidence["share_state"] is True
        assert items["html_shell"].evidence["share_copy"] is True
        assert items["html_shell"].evidence["result_match_notes"] is True
        assert items["private_download_paths"].evidence["exposed_paths"] == []
        assert items["private_download_paths"].evidence["statuses"]["/thread-search.sqlite"] == 404
        assert items["search_probe:Cuba"].evidence["total_threadmarks"] == 1
        assert items["search_probe:Cuba"].evidence["total_chunks"] == 1
        assert items["search_prefix_variants_probe:Cuba"].evidence["prefix_variants"] is True
        assert items["search_prefix_variants_probe:Cuba"].evidence["match_kind"] == "prefix-variants"
        assert items["terms_probe:Cuba"].evidence["first_term"] == "cuba"
        assert items["explain_probe:Cuba"].evidence["exact_threadmarks"] == 1
        assert items["explain_probe:Cuba"].evidence["prefix_threadmarks"] == 1
        assert items["explain_probe:Cuba"].evidence["resolved_match_kind"] == "exact"
        explain_pair = items["explain_pair:Cuba:communist"]
        assert explain_pair.evidence["query"] == "Cuba communist"
        assert [item["query"] for item in explain_pair.evidence["term_breakdown"]] == ["Cuba", "communist"]
        assert explain_pair.evidence["term_breakdown"][0]["resolved_match_kind"] == "exact"
        assert explain_pair.evidence["term_breakdown"][1]["resolved_match_kind"] == "exact"
        assert items["mentions_probe:Cuba"].evidence["total_mentions"] == 1
        assert items["compare_probe:Cuba:Soviet"].evidence["topic_count"] == 2
        assert items["compare_probe:Cuba:Soviet"].evidence["pairwise_count"] == 1
        assert items["evidence_pack_probe:Cuba"].evidence["total_threadmarks"] == 1
        assert items["recap_probe:Cuba"].evidence["timeline_count"] == 1
        stats = items["stats_public_contract"]
        assert stats.evidence["public_contact"] == PUBLIC_CONTACT
        assert stats.evidence["removal_request_url"] == REMOVAL_REQUEST_URL
        assert stats.evidence["artifact_manifest_validated"] is False
        assert stats.evidence["require_artifact_manifest"] is False
        claim = items["claim_pair:Cuba:communist"]
        assert claim.evidence["evidence_level"] == "strong-chunk-overlap"
        assert claim.evidence["topic_query_exact_threadmarks"] == 1
        assert claim.evidence["claim_query_exact_threadmarks"] == 1
        assert claim.evidence["negation_cue_evidence"] == 1
        assert claim.evidence["caution_codes"] == ["negation-cues"]
        assert claim.evidence["evidence_proximity_ok"] is True
        q_only_claim = items["claim_q_only:Cuba:communist"]
        assert q_only_claim.evidence["claim_inferred_from_query"] is True
        assert q_only_claim.evidence["original_query"] == "Cuba's communist"
        assert q_only_claim.evidence["topic_query"] == "Cuba"
        assert q_only_claim.evidence["claim_query"] == "communist"
        assert q_only_claim.evidence["evidence_level"] == "strong-chunk-overlap"
        assert q_only_claim.evidence["topic_query_exact_threadmarks"] == 1
        assert q_only_claim.evidence["claim_query_exact_threadmarks"] == 1
        assert q_only_claim.evidence["caution_codes"] == ["negation-cues"]
        q_only_pack = items["evidence_pack_q_only:Cuba:communist"]
        assert q_only_pack.evidence["claim_inferred_from_query"] is True
        assert q_only_pack.evidence["original_query"] == "Cuba's communist"
        assert q_only_pack.evidence["resolved_query"] == "Cuba"
        assert q_only_pack.evidence["dossier_query"] == "Cuba"
        assert q_only_pack.evidence["claim_query"] == "communist"
        assert q_only_pack.evidence["topic_query_exact_threadmarks"] == 1
        assert q_only_pack.evidence["claim_query_exact_threadmarks"] == 1
        assert q_only_pack.evidence["caution_codes"] == ["negation-cues"]
        q_only_recap = items["recap_q_only:Cuba:communist"]
        assert q_only_recap.evidence["claim_inferred_from_query"] is True
        assert q_only_recap.evidence["original_query"] == "Cuba's communist"
        assert q_only_recap.evidence["resolved_query"] == "Cuba"
        assert q_only_recap.evidence["claim_query"] == "communist"
        assert q_only_recap.evidence["topic_query_exact_threadmarks"] == 1
        assert q_only_recap.evidence["claim_query_exact_threadmarks"] == 1
        assert q_only_recap.evidence["caution_codes"] == ["negation-cues"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_public_smoke_can_require_artifact_manifest_signal(tmp_path: Path) -> None:
    db = build_db(tmp_path)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None
        public_contact_value = PUBLIC_CONTACT
        removal_request_url_value = REMOVAL_REQUEST_URL
        artifact_manifest_validated_value = True
        artifact_manifest_sha256_value = "m" * 64
        artifact_database_sha256_value = "d" * 64
        artifact_created_at_utc_value = "2026-07-08T00:00:00Z"

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        report = run_public_smoke(
            f"http://127.0.0.1:{server.server_port}",
            probes=("Cuba",),
            timeout=5,
            require_artifact_manifest=True,
        )
        items = {item.key: item for item in report.items}

        assert report.ok is True
        assert items["stats_public_contract"].evidence["artifact_manifest_validated"] is True
        assert items["stats_public_contract"].evidence["artifact_manifest_sha256"] == "m" * 64
        assert items["stats_public_contract"].evidence["artifact_database_sha256"] == "d" * 64
        assert items["stats_public_contract"].evidence["artifact_created_at_utc"] == "2026-07-08T00:00:00Z"
        assert items["stats_public_contract"].evidence["require_artifact_manifest"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_public_smoke_fails_when_artifact_manifest_signal_required_but_missing(tmp_path: Path) -> None:
    db = build_db(tmp_path)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None
        public_contact_value = PUBLIC_CONTACT
        removal_request_url_value = REMOVAL_REQUEST_URL

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        report = run_public_smoke(
            f"http://127.0.0.1:{server.server_port}",
            probes=("Cuba",),
            timeout=5,
            require_artifact_manifest=True,
        )
        items = {item.key: item for item in report.items}

        assert report.ok is False
        assert items["stats_public_contract"].status == "fail"
        assert items["stats_public_contract"].evidence["artifact_manifest_validated"] is False
        assert items["stats_public_contract"].evidence["require_artifact_manifest"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_public_smoke_fails_public_database_download(tmp_path: Path) -> None:
    db = build_db(tmp_path)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None
        public_contact_value = PUBLIC_CONTACT
        removal_request_url_value = REMOVAL_REQUEST_URL

        def handle_request(self, head_only: bool = False) -> None:
            if urlparse(self.path).path == "/thread-search.sqlite":
                self.respond_text("sqlite database bytes", content_type="application/octet-stream", head_only=head_only)
                return
            super().handle_request(head_only=head_only)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        report = run_public_smoke(f"http://127.0.0.1:{server.server_port}", probes=("Cuba",), timeout=5)
        items = {item.key: item for item in report.items}

        assert report.ok is False
        assert items["private_download_paths"].status == "fail"
        assert items["private_download_paths"].evidence["exposed_paths"] == ["/thread-search.sqlite"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_public_smoke_fails_missing_public_contact_metadata(tmp_path: Path) -> None:
    db = build_db(tmp_path)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        report = run_public_smoke(f"http://127.0.0.1:{server.server_port}", probes=("Cuba",), timeout=5)
        items = {item.key: item for item in report.items}

        assert report.ok is False
        assert items["stats_public_contract"].status == "fail"
        assert items["stats_public_contract"].evidence["public_contact"] == ""
        assert items["stats_public_contract"].evidence["removal_request_url"] == ""
        assert items["stats_public_contract"].evidence["contact_errors"] == [
            "public-contact is required for public deployment",
            "removal-request-url is required for public deployment",
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_public_smoke_fails_placeholder_public_contact_metadata(tmp_path: Path) -> None:
    db = build_db(tmp_path)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None
        public_contact_value = "mailto:operator@example.invalid"
        removal_request_url_value = "https://search.example.invalid/removal"

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        report = run_public_smoke(f"http://127.0.0.1:{server.server_port}", probes=("Cuba",), timeout=5)
        items = {item.key: item for item in report.items}

        assert report.ok is False
        assert items["stats_public_contract"].status == "fail"
        assert items["stats_public_contract"].evidence["contact_errors"] == [
            "public-contact must not be a placeholder for public deployment; got 'mailto:operator@example.invalid'",
            "removal-request-url must not be a placeholder for public deployment; got 'https://search.example.invalid/removal'",
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_public_smoke_fails_private_fulltext_server(tmp_path: Path) -> None:
    db = build_db(tmp_path)

    class Handler(SearchHandler):
        database_path = db
        allow_private_fulltext = True
        allow_chunk_results = True
        rate_limiter = None
        public_contact_value = PUBLIC_CONTACT
        removal_request_url_value = REMOVAL_REQUEST_URL

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        report = run_public_smoke(f"http://127.0.0.1:{server.server_port}", probes=("Cuba",), timeout=5)
        statuses = {item.key: item.status for item in report.items}

        assert report.ok is False
        assert statuses["stats_public_contract"] == "fail"
        assert statuses["private_threadmark_route"] == "fail"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_forbidden_key_paths_reports_nested_public_text_keys() -> None:
    payload = {"items": [{"source_url": "https://example.invalid", "body": "full text"}]}

    assert forbidden_key_paths(payload, {"body"}) == ["$.items[0].body"]
