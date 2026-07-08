import json
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread

from planquest.web import (
    APP_HTML,
    ROBOTS_TXT,
    SearchHandler,
    SlidingWindowRateLimiter,
    add_csp_nonce,
    apply_public_snippet_budget,
    bounded_query,
    claim_query_candidate,
    clamp,
    health_payload,
    html_csp,
    index_stats,
    question_claim_query_candidate,
    trim_marked_snippet,
)
from planquest.indexer import build_index
from planquest.models import Threadmark
from planquest.scrape import write_jsonl


def test_bounded_query_collapses_whitespace_and_caps_length() -> None:
    assert bounded_query("  Cuba\n  communism  ", 9) == "Cuba comm"


def test_claim_query_candidate_parses_question_style_claims() -> None:
    assert claim_query_candidate("did Cuba turn communist?") == ("Cuba", "communist")
    assert claim_query_candidate("Cuba's communist") == ("Cuba", "communist")
    assert claim_query_candidate("was Cuba actually communist") == ("Cuba", "communist")
    assert claim_query_candidate("Cuba communist") == ("Cuba", "communist")
    assert claim_query_candidate("Cuba") is None


def test_question_claim_query_candidate_requires_question_or_possessive() -> None:
    assert question_claim_query_candidate("did Cuba turn communist?") == ("Cuba", "communist")
    assert question_claim_query_candidate("Cuba's communist") == ("Cuba", "communist")
    assert question_claim_query_candidate("was Cuba actually communist") == ("Cuba", "communist")
    assert question_claim_query_candidate("Cuba communist") is None
    assert question_claim_query_candidate("Soviet Union") is None


def test_clamp_bounds_values() -> None:
    assert clamp(0, 1, 5) == 1
    assert clamp(8, 1, 5) == 5
    assert clamp(3, 1, 5) == 3


def test_trim_marked_snippet_closes_highlight_within_budget() -> None:
    trimmed = trim_marked_snippet("\x01Cuba appears in a long highlighted phrase\x02", 18)

    assert len(trimmed) <= 18
    assert trimmed.count("\x01") == trimmed.count("\x02")
    assert trimmed.endswith(" [...]")


def test_apply_public_snippet_budget_caps_nested_snippets() -> None:
    payload: dict[str, object] = {
        "results": [
            {"snippet": "Cuba appears here."},
            {"snippet": "This second snippet should be trimmed."},
        ]
    }

    apply_public_snippet_budget(payload, 28)
    snippets = [item["snippet"] for item in payload["results"]]  # type: ignore[index]

    assert payload["snippet_budget_chars"] == 28
    assert payload["snippets_truncated"] is True
    assert sum(len(snippet) for snippet in snippets) <= 28


def test_index_stats_include_public_caps(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba appears here.",
                word_count=3,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    stats = index_stats(
        db,
        search_limit_cap=12,
        report_limit_cap=34,
        mention_limit_cap=45,
        threadmark_limit_cap=46,
        query_char_cap=56,
        mention_window_char_cap=67,
        snippet_budget_char_cap=89,
        rate_limit_per_minute=78,
        public_contact="mailto:operator@example.invalid",
        removal_request_url="https://search.example.invalid/removal",
        artifact_manifest_validated=True,
        artifact_manifest_sha256="m" * 64,
        artifact_database_sha256="d" * 64,
        artifact_created_at_utc="2026-07-08T00:00:00Z",
    )

    assert stats["ok"] is True
    assert stats["source_reader_url"].startswith("https://forums.sufficientvelocity.com/threads/")
    assert stats["source_host"] == "forums.sufficientvelocity.com"
    assert stats["public_access_mode"] == "snippets_and_source_links"
    assert "source links" in stats["public_notice"]
    assert stats["public_contact"] == "mailto:operator@example.invalid"
    assert stats["removal_request_url"] == "https://search.example.invalid/removal"
    assert stats["search_limit_cap"] == 12
    assert stats["report_limit_cap"] == 34
    assert stats["mention_limit_cap"] == 45
    assert stats["threadmark_limit_cap"] == 46
    assert stats["query_char_cap"] == 56
    assert stats["mention_window_char_cap"] == 67
    assert stats["snippet_budget_char_cap"] == 89
    assert stats["rate_limit_per_minute"] == 78
    assert stats["chunk_results_enabled"] is False
    assert stats["artifact_manifest_validated"] is True
    assert stats["artifact_manifest_sha256"] == "m" * 64
    assert stats["artifact_database_sha256"] == "d" * 64
    assert stats["artifact_created_at_utc"] == "2026-07-08T00:00:00Z"


def test_health_payload_checks_database_readiness(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba appears here.",
                word_count=3,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    payload = health_payload(db)

    assert payload["ok"] is True
    assert payload["threadmarks"] == 1
    assert payload["chunks"] == 1


def test_health_payload_rejects_missing_or_invalid_database(tmp_path) -> None:
    missing = health_payload(tmp_path / "missing.sqlite")
    invalid_db = tmp_path / "invalid.sqlite"
    invalid_db.write_text("not sqlite", encoding="utf-8")
    invalid = health_payload(invalid_db)

    assert missing["ok"] is False
    assert invalid["ok"] is False
    assert "error" in missing
    assert "error" in invalid


def test_app_html_exposes_contents_tab_without_fulltext_route() -> None:
    assert 'id="tab-contents"' in APP_HTML
    assert 'id="panel-contents"' in APP_HTML
    assert "/api/threadmarks?limit=300" in APP_HTML
    assert 'id="suggestions"' in APP_HTML
    assert 'id="query-tools"' in APP_HTML
    assert 'id="count"' in APP_HTML
    assert APP_HTML.count('id="count"') == 1
    assert "|[])}." not in APP_HTML
    assert 'id="source-link"' in APP_HTML
    assert 'id="public-notice"' in APP_HTML
    assert "renderNoticeValue" in APP_HTML
    assert "Removal requests:" in APP_HTML
    assert "public_contact" in APP_HTML
    assert "removal_request_url" in APP_HTML
    assert 'id="prefix-variants"' in APP_HTML
    assert 'id="terms-link"' in APP_HTML
    assert 'id="explain-link"' in APP_HTML
    assert 'id="recap-link"' in APP_HTML
    assert 'id="report-link"' in APP_HTML
    assert 'id="dossier-link"' in APP_HTML
    assert 'id="evidence-pack-link"' in APP_HTML
    assert 'id="mentions-link"' in APP_HTML
    assert 'id="coverage-link"' in APP_HTML
    assert 'id="compare-link"' not in APP_HTML
    assert 'id="claim-link"' not in APP_HTML
    assert 'id="recap"' in APP_HTML
    assert 'id="dossier"' in APP_HTML
    assert 'id="compare"' not in APP_HTML
    assert 'id="dossier-aliases"' in APP_HTML
    assert 'id="coverage-list"' in APP_HTML
    assert 'id="clear-range"' in APP_HTML
    assert 'id="topic-sort"' in APP_HTML
    assert 'value="timeline"' in APP_HTML
    assert "/api/suggest" in APP_HTML
    assert "/api/terms" in APP_HTML
    assert "/api/explain" in APP_HTML
    assert "/api/mentions" in APP_HTML
    assert "/api/dossier" in APP_HTML
    assert "/api/report" in APP_HTML
    assert "/api/evidence-pack" in APP_HTML
    assert "/api/recap" in APP_HTML
    assert "/api/coverage" in APP_HTML
    assert "/api/compare" not in APP_HTML
    assert "/api/claim" not in APP_HTML
    assert "Claim check:" not in APP_HTML
    assert "Timeline recap:" in APP_HTML
    assert "Proximity:" in APP_HTML
    assert "renderClaimTermDiagnostics" in APP_HTML
    assert "renderClaimExactDiagnostics" in APP_HTML
    assert "Exact query counts:" in APP_HTML
    assert "renderClaimCautions" in APP_HTML
    assert "Cautions:" in APP_HTML
    assert "Evidence snippets" in APP_HTML
    assert "Dossier JSON" in APP_HTML
    assert "Report JSON" in APP_HTML
    assert "Evidence Pack JSON" in APP_HTML
    assert "Recap JSON" in APP_HTML
    assert "Mentions JSON" in APP_HTML
    assert "Coverage JSON" in APP_HTML
    assert "Compare JSON" not in APP_HTML
    assert "Claim JSON" not in APP_HTML
    assert 'id="share-link"' in APP_HTML
    assert 'id="share-status"' in APP_HTML
    assert "Copy link" in APP_HTML
    assert "copyShareLink" in APP_HTML
    assert "fallbackCopy" in APP_HTML
    assert "uiStateParams" in APP_HTML
    assert "currentStatePath" in APP_HTML
    assert 'params.set("prefix_variants", "1")' in APP_HTML
    assert 'params.append("alias", term)' in APP_HTML
    assert 'params.set("view", "contents")' in APP_HTML
    assert "navigator.clipboard" in APP_HTML
    assert "document.execCommand(\"copy\")" in APP_HTML
    assert "Topic aliases" in APP_HTML
    assert "Topic dossier:" in APP_HTML
    assert "dossierParams" in APP_HTML
    assert "reportParams" in APP_HTML
    assert "termsParams" in APP_HTML
    assert "explainParams" in APP_HTML
    assert "loadQueryExplain" in APP_HTML
    assert "renderQueryExplain" in APP_HTML
    assert "Query explain:" in APP_HTML
    assert "explainCountText" in APP_HTML
    assert "renderExplainTermBreakdown" in APP_HTML
    assert "term_breakdown" in APP_HTML
    assert "evidencePackParams" in APP_HTML
    assert "recapParams" in APP_HTML
    assert "loadRecap" in APP_HTML
    assert "renderRecap" in APP_HTML
    assert "renderRecapThreadmark" in APP_HTML
    assert "renderRecapMention" in APP_HTML
    assert "renderRecapClaim" in APP_HTML
    assert "mentionsParams" in APP_HTML
    assert "coverageParams" in APP_HTML
    assert "compareParams" not in APP_HTML
    assert "claimParams" not in APP_HTML
    assert "resultCountText" in APP_HTML
    assert "shown of" in APP_HTML
    assert "renderQueryTools" in APP_HTML
    assert "claimSplitCandidate" not in APP_HTML
    assert "exactFallbackQuery" in APP_HTML
    assert "Exact only:" in APP_HTML
    assert "data-exact-query" in APP_HTML
    assert 'payload.match_kind !== "prefix"' in APP_HTML
    assert "exactButton.dataset.exactQuery" in APP_HTML
    assert "questionLeadWords" not in APP_HTML
    assert "claimFillerWords" not in APP_HTML
    assert "cleanClaimToken" not in APP_HTML
    assert "cleanTopicToken" not in APP_HTML
    assert "cleanClaimPhrase" not in APP_HTML
    assert "implicitClaimCandidate" not in APP_HTML
    assert "loadClaimCheck" not in APP_HTML
    assert "evidencePackParams()" in APP_HTML
    assert "recapParams()" in APP_HTML
    assert "updateClaimLink" not in APP_HTML
    assert "updateCompareLink" not in APP_HTML
    assert "compareParams" not in APP_HTML
    assert "data-claim-topic" not in APP_HTML
    assert "updateJsonLinks" in APP_HTML
    assert "updateRecapLink" in APP_HTML
    assert "updateReportLink" in APP_HTML
    assert "updateMentionsLink" in APP_HTML
    assert "aliasTerms" in APP_HTML
    assert "aliasInput.value.split(/[,\\n;]/)" in APP_HTML
    assert "Near indexed terms" in APP_HTML
    assert "Near match:" in APP_HTML
    assert 'params.append("alias", term)' in APP_HTML
    assert "loadDossier" in APP_HTML
    assert "renderDossierThreadmark" in APP_HTML
    assert "renderDossierMention" in APP_HTML
    assert "renderCoverageRow" in APP_HTML
    assert "renderCoverageBuckets" in APP_HTML
    assert "renderCoverageTermDiagnostics" in APP_HTML
    assert "Coverage terms:" in APP_HTML
    assert "Word variants" in APP_HTML
    assert "addPrefixVariantsParam" in APP_HTML
    assert "prefix_variants" in APP_HTML
    assert "applyBucketRange" in APP_HTML
    assert "clearRangeFilter" in APP_HTML
    assert "updateRangeState" in APP_HTML
    assert "Clear range" in APP_HTML
    assert "data-bucket-from" in APP_HTML
    assert "Filter to threadmarks" in APP_HTML
    assert "All matching threadmarks" in APP_HTML
    assert "shownCount" in APP_HTML
    assert "showing" in APP_HTML
    assert "prefix fallback" in APP_HTML
    assert "matched with word variants" in APP_HTML
    assert "renderMatchNote" in APP_HTML
    assert 'renderMatchNote(result.match_kind, "Result")' in APP_HTML
    assert "/api/threadmark/" not in APP_HTML


def test_search_endpoint_reports_prefix_fallback(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuban exchange programs are discussed.",
                word_count=5,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/search?q=Cuba")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["match_kind"] == "prefix"
        assert "*" in payload["match_query"]
        assert payload["result_count"] == 1
        assert payload["total_threadmarks"] == 1
        assert payload["total_chunks"] == 1
        assert payload["results"][0]["match_kind"] == "prefix"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_search_endpoint_supports_timeline_sort(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba appears in a later turn. Cuba appears again.",
                word_count=9,
            ),
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba appears in an early turn.",
                word_count=6,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/search?q=Cuba&sort=timeline&limit=5")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["sort"] == "timeline"
        assert [item["threadmark_order"] for item in payload["results"]] == [1, 2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_search_endpoint_merges_alias_terms(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba appears in the first turn.",
                word_count=6,
            ),
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Castro appears in the second turn.",
                word_count=6,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/search?q=Cuba&alias=Castro&sort=timeline&limit=5")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["aliases"] == ["Castro"]
        assert [term["query"] for term in payload["terms"]] == ["Cuba", "Castro"]
        assert payload["total_threadmarks"] == 2
        assert payload["total_chunks"] == 2
        assert [item["threadmark_order"] for item in payload["results"]] == [1, 2]
        assert "body" not in payload["results"][0]
        assert "snippet_html" in payload["results"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_search_endpoint_prefix_variants_include_exact_and_prefix_hits(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuban exchange programs are discussed.",
                word_count=5,
            ),
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba appears directly.",
                word_count=3,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/search?q=Cuba&sort=timeline&limit=5")
        default_response = conn.getresponse()
        default_payload = json.loads(default_response.read().decode("utf-8"))
        conn.request("GET", "/api/search?q=Cuba&sort=timeline&limit=5&prefix_variants=1")
        variant_response = conn.getresponse()
        variant_payload = json.loads(variant_response.read().decode("utf-8"))

        assert default_response.status == 200
        assert default_payload["prefix_variants"] is False
        assert default_payload["match_kind"] == "exact"
        assert [item["threadmark_order"] for item in default_payload["results"]] == [2]
        assert variant_response.status == 200
        assert variant_payload["prefix_variants"] is True
        assert variant_payload["match_kind"] == "prefix-variants"
        assert variant_payload["match_query"] == '"Cuba"*'
        assert [item["threadmark_order"] for item in variant_payload["results"]] == [1, 2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_search_endpoint_reports_totals_beyond_result_limit(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba appears in an early turn.",
                word_count=6,
            ),
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba appears in a later turn.",
                word_count=6,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/search?q=Cuba&limit=1")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["result_count"] == 1
        assert payload["total_threadmarks"] == 2
        assert payload["total_chunks"] == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_report_endpoint_merges_alias_terms(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba appears in the first turn.",
                word_count=6,
            ),
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Castro appears in the second turn.",
                word_count=6,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/report?q=Cuba&alias=Castro&sort=timeline&limit=5")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["aliases"] == ["Castro"]
        assert [term["query"] for term in payload["terms"]] == ["Cuba", "Castro"]
        assert payload["total_threadmarks"] == 2
        assert payload["total_chunks"] == 2
        assert [item["threadmark_order"] for item in payload["mentions"]] == [1, 2]
        assert "snippet_html" in payload["mentions"][0]
        assert "body" not in payload["mentions"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_suggest_endpoint_is_public_rate_limited_path() -> None:
    assert SearchHandler.is_public_api_path("/api/suggest") is True


def test_terms_endpoint_is_public_rate_limited_path() -> None:
    assert SearchHandler.is_public_api_path("/api/terms") is True


def test_explain_endpoint_is_public_rate_limited_path() -> None:
    assert SearchHandler.is_public_api_path("/api/explain") is True


def test_claim_endpoint_is_public_rate_limited_path() -> None:
    assert SearchHandler.is_public_api_path("/api/claim") is True


def test_dossier_endpoint_is_public_rate_limited_path() -> None:
    assert SearchHandler.is_public_api_path("/api/dossier") is True


def test_evidence_pack_endpoint_is_public_rate_limited_path() -> None:
    assert SearchHandler.is_public_api_path("/api/evidence-pack") is True


def test_recap_endpoint_is_public_rate_limited_path() -> None:
    assert SearchHandler.is_public_api_path("/api/recap") is True


def test_coverage_endpoint_is_public_rate_limited_path() -> None:
    assert SearchHandler.is_public_api_path("/api/coverage") is True


def test_compare_endpoint_is_public_rate_limited_path() -> None:
    assert SearchHandler.is_public_api_path("/api/compare") is True


def test_head_routes_public_api_without_body(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuban state appears here.",
                word_count=4,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("HEAD", "/api/suggest?q=Cuba")
        response = conn.getresponse()

        assert response.status == 200
        assert response.getheader("Content-Type") == "application/json; charset=utf-8"
        assert response.read() == b""
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_suggest_endpoint_returns_near_terms_when_prefix_misses(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba Cuba is discussed directly.",
                word_count=5,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/suggest?q=Cubaa")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["suggestions"][0]["term"] == "cuba"
        assert payload["suggestions"][0]["match_kind"] == "near"
        assert payload["suggestions"][0]["edit_distance"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_terms_endpoint_returns_metadata_without_snippets(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba and Cuban trade are discussed directly.",
                word_count=7,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/terms?prefix=Cub&limit=10")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["kind"] == "thread-search-term-index"
        assert payload["metadata_only"] is True
        assert payload["prefix"] == "cub"
        assert [item["term"] for item in payload["terms"]] == ["cuba", "cuban"]
        serialized = json.dumps(payload)
        assert '"snippet"' not in serialized
        assert '"best_snippet"' not in serialized
        assert '"body"' not in serialized
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_explain_endpoint_returns_metadata_without_snippets(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuban exchange programs are discussed directly.",
                word_count=6,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/explain?q=Cuba&term_limit=10")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["kind"] == "thread-search-query-explain"
        assert payload["metadata_only"] is True
        assert payload["exact"]["total_threadmarks"] == 0
        assert payload["prefix"]["total_threadmarks"] == 1
        assert payload["resolved"]["match_kind"] == "prefix"
        assert [item["code"] for item in payload["cautions"]] == ["exact-missing-prefix-available"]
        serialized = json.dumps(payload)
        assert '"snippet"' not in serialized
        assert '"best_snippet"' not in serialized
        assert '"body"' not in serialized
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_explain_endpoint_reports_multi_term_breakdown_metadata_only(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuban exchange programs are discussed directly.",
                word_count=6,
            ),
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Communist parties are discussed separately.",
                word_count=5,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/explain?q=Cuba+communist&term_limit=10")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["metadata_only"] is True
        assert [item["query"] for item in payload["term_breakdown"]] == ["Cuba", "communist"]
        assert payload["term_breakdown"][0]["exact"]["total_threadmarks"] == 0
        assert payload["term_breakdown"][0]["prefix"]["total_threadmarks"] == 1
        assert payload["term_breakdown"][1]["exact"]["total_threadmarks"] == 1
        assert [item["code"] for item in payload["cautions"]] == ["individual-terms-only"]
        serialized = json.dumps(payload)
        assert '"snippet"' not in serialized
        assert '"best_snippet"' not in serialized
        assert '"body"' not in serialized
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_healthz_returns_503_for_invalid_database(tmp_path) -> None:
    invalid_db = tmp_path / "invalid.sqlite"
    invalid_db.write_text("not sqlite", encoding="utf-8")

    class Handler(SearchHandler):
        database_path = invalid_db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/healthz")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 503
        assert payload["ok"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_html_response_uses_nonce_csp(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba appears here.",
                word_count=3,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
        csp = response.getheader("Content-Security-Policy") or ""

        assert response.status == 200
        assert "script-src 'nonce-" in csp
        assert "style-src 'nonce-" in csp
        assert "'unsafe-inline'" not in csp
        assert 'nonce="' in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_csp_helpers_apply_nonce() -> None:
    assert 'nonce="abc123"' in add_csp_nonce("<style></style><script></script>", "abc123")
    policy = html_csp("abc123")
    assert "script-src 'nonce-abc123'" in policy
    assert "style-src 'nonce-abc123'" in policy


def test_mentions_endpoint_supports_timeline_sort(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba appears first.",
                word_count=3,
            ),
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text=f"Cuba appears later. {'filler ' * 20}Cuba appears again.",
                word_count=24,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/mentions?q=Cuba&sort=timeline&limit=5")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert [item["threadmark_order"] for item in payload["mentions"]] == [1, 2, 2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_mentions_endpoint_merges_alias_terms(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba appears first.",
                word_count=3,
            ),
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Castro appears later.",
                word_count=3,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/mentions?q=Cuba&alias=Castro&sort=timeline&limit=5")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["aliases"] == ["Castro"]
        assert [term["query"] for term in payload["terms"]] == ["Cuba", "Castro"]
        assert payload["total_threadmarks"] == 2
        assert payload["total_mentions"] == 2
        assert [item["threadmark_order"] for item in payload["mentions"]] == [1, 2]
        assert "snippet_html" in payload["mentions"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_mentions_endpoint_applies_public_snippet_budget(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba appears in a long sentence with enough words to make a mention window large. Cuba appears again with more surrounding words.",
                word_count=21,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None
        snippet_budget_char_cap = 48

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/mentions?q=Cuba&limit=5")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        snippets = [item["snippet"] for item in payload["mentions"]]
        assert response.status == 200
        assert payload["snippet_budget_chars"] == 48
        assert payload["snippets_truncated"] is True
        assert sum(len(snippet) for snippet in snippets) <= 48
        assert all(snippet.count("\x01") == snippet.count("\x02") for snippet in snippets)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dossier_endpoint_returns_bounded_retrieval_bundle(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuban exchange programs and sugar policy are discussed.",
                word_count=8,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/dossier?q=Cuba&threadmark_limit=5&mention_limit=5")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["query"] == "Cuba"
        assert payload["match_kind"] == "prefix"
        assert payload["total_threadmarks"] == 1
        assert payload["total_mentions"] == 1
        assert "snippet_html" in payload["threadmarks"][0]
        assert "snippet_html" in payload["timeline"][0]
        assert payload["mention_windows"] == []
        assert "body" not in payload["threadmarks"][0]
        assert "body" not in payload["timeline"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dossier_endpoint_merges_alias_terms(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuban exchange programs are discussed.",
                word_count=5,
            ),
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Castro appears in a separate update.",
                word_count=6,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/dossier?q=Cuba&alias=Castro&threadmark_limit=5&mention_limit=5")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["aliases"] == ["Castro"]
        assert [term["query"] for term in payload["terms"]] == ["Cuba", "Castro"]
        assert payload["total_threadmarks"] == 2
        assert [item["threadmark_order"] for item in payload["threadmarks"]] == [1, 2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_evidence_pack_endpoint_bundles_dossier_and_claim(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba did not turn communist in this timeline.",
                word_count=8,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/evidence-pack?q=Cuba&claim=communist&threadmark_limit=5&mention_limit=5")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["kind"] == "thread-search-evidence-pack"
        assert payload["bounded_retrieval_only"] is True
        assert payload["dossier"]["query"] == "Cuba"
        assert payload["dossier"]["total_threadmarks"] == 1
        assert payload["claims"][0]["claim_query"] == "communist"
        assert payload["claims"][0]["evidence_level"] == "strong-chunk-overlap"
        assert payload["claims"][0]["negation_cue_evidence"] == 1
        assert "snippet_budget_chars" in payload
        assert "snippet_html" in payload["dossier"]["threadmarks"][0]
        assert "snippet_html" in payload["dossier"]["timeline"][0]
        assert payload["dossier"]["mention_windows"] == []
        assert "topic_snippet_html" in payload["claims"][0]["evidence"][0]
        assert "claim_snippet_html" in payload["claims"][0]["evidence"][0]
        assert "body" not in payload["dossier"]["threadmarks"][0]
        assert "body" not in payload["dossier"]["timeline"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_evidence_pack_endpoint_infers_question_style_claim(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba did not turn communist in this timeline.",
                word_count=8,
            ),
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba trade policy continues later.",
                word_count=5,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request(
            "GET",
            "/api/evidence-pack?q=did%20Cuba%20turn%20communist&threadmark_limit=5&mention_limit=5",
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["kind"] == "thread-search-evidence-pack"
        assert payload["query"] == "Cuba"
        assert payload["claim_inferred_from_query"] is True
        assert payload["original_query"] == "did Cuba turn communist"
        assert payload["dossier"]["query"] == "Cuba"
        assert payload["dossier"]["total_threadmarks"] == 2
        assert payload["claims"][0]["claim_query"] == "communist"
        assert payload["claims"][0]["evidence_level"] == "strong-chunk-overlap"
        assert "snippet_html" in payload["dossier"]["threadmarks"][0]
        assert "snippet_html" in payload["dossier"]["timeline"][0]
        assert payload["dossier"]["mention_windows"] == []
        assert "topic_snippet_html" in payload["claims"][0]["evidence"][0]
        assert "claim_snippet_html" in payload["claims"][0]["evidence"][0]
        assert "body" not in payload["dossier"]["threadmarks"][0]
        assert "body" not in payload["dossier"]["timeline"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_recap_endpoint_returns_timeline_and_claim(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba did not turn communist in this timeline.",
                word_count=8,
            ),
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba trade policy continues later.",
                word_count=5,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/recap?q=Cuba&claim=communist&timeline_limit=5&mention_limit=5")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["kind"] == "thread-search-topic-recap"
        assert payload["bounded_retrieval_only"] is True
        assert payload["query"] == "Cuba"
        assert payload["total_threadmarks"] == 2
        assert [item["threadmark_order"] for item in payload["timeline"]] == [1, 2]
        assert payload["claims"][0]["claim_query"] == "communist"
        assert payload["claims"][0]["evidence_level"] == "strong-chunk-overlap"
        assert payload["claims"][0]["negation_cue_evidence"] == 1
        assert "snippet_budget_chars" in payload
        assert "snippet_html" in payload["timeline"][0]
        assert payload["mention_windows"] == []
        assert "topic_snippet_html" in payload["claims"][0]["evidence"][0]
        assert "claim_snippet_html" in payload["claims"][0]["evidence"][0]
        assert "body" not in payload["timeline"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_recap_endpoint_infers_question_style_claim(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba did not turn communist in this timeline.",
                word_count=8,
            ),
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba trade policy continues later.",
                word_count=5,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/recap?q=did%20Cuba%20turn%20communist&timeline_limit=5&mention_limit=5")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["kind"] == "thread-search-topic-recap"
        assert payload["query"] == "Cuba"
        assert payload["claim_inferred_from_query"] is True
        assert payload["original_query"] == "did Cuba turn communist"
        assert [item["threadmark_order"] for item in payload["timeline"]] == [1, 2]
        assert payload["claims"][0]["claim_query"] == "communist"
        assert payload["claims"][0]["evidence_level"] == "strong-chunk-overlap"
        assert "snippet_html" in payload["timeline"][0]
        assert payload["mention_windows"] == []
        assert "topic_snippet_html" in payload["claims"][0]["evidence"][0]
        assert "claim_snippet_html" in payload["claims"][0]["evidence"][0]
        assert "body" not in payload["timeline"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_recap_endpoint_keeps_plain_multiword_topic(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Soviet Union policy appears in the update.",
                word_count=7,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/recap?q=Soviet%20Union&timeline_limit=5&mention_limit=5")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["kind"] == "thread-search-topic-recap"
        assert payload["query"] == "Soviet Union"
        assert payload["claims"] == []
        assert "claim_inferred_from_query" not in payload
        assert payload["total_threadmarks"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_coverage_endpoint_returns_metadata_without_snippets(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuban exchange programs are discussed.",
                word_count=5,
            ),
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Castro appears in a separate update.",
                word_count=6,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/coverage?q=Cuba&alias=Castro&limit=5&bucket_size=1")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["aliases"] == ["Castro"]
        assert payload["match_kind"] == "prefix"
        assert [term["query"] for term in payload["terms"]] == ["Cuba", "Castro"]
        assert payload["total_threadmarks"] == 2
        assert [bucket["start_order"] for bucket in payload["buckets"]] == [1, 2]
        assert [bucket["threadmark_count"] for bucket in payload["buckets"]] == [1, 1]
        assert "total_mentions" not in payload
        assert [item["threadmark_order"] for item in payload["items"]] == [1, 2]
        assert all("snippet" not in item for item in payload["items"])
        assert all("best_snippet" not in item for item in payload["items"])
        assert all("body" not in item for item in payload["items"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_compare_endpoint_returns_metadata_without_snippets(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba appears in an early update.",
                word_count=6,
            ),
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba and communist parties are discussed together.",
                word_count=7,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/compare?q=Cuba&topic=communist&bucket_size=1")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["kind"] == "thread-search-topic-comparison"
        assert payload["metadata_only"] is True
        assert payload["queries"] == ["Cuba", "communist"]
        assert [topic["total_threadmarks"] for topic in payload["topics"]] == [2, 1]
        assert payload["topics"][0]["first_threadmark"]["threadmark_order"] == 1
        assert payload["topics"][0]["last_threadmark"]["threadmark_order"] == 2
        assert payload["all_overlap"]["total_threadmarks"] == 1
        assert payload["all_overlap"]["items"][0]["threadmark_order"] == 2
        assert payload["pairwise_overlaps"][0]["total_threadmarks"] == 1
        serialized = json.dumps(payload)
        assert '"snippet"' not in serialized
        assert '"best_snippet"' not in serialized
        assert '"body"' not in serialized
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_claim_endpoint_reports_evidence_level(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba and communist theory appear together.",
                word_count=6,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/claim?q=Cuba&claim=communist")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["claim_query"] == "communist"
        assert payload["evidence_level"] == "strong-chunk-overlap"
        assert payload["claim_threadmarks"] == 1
        assert payload["overlapping_chunks"] == 1
        assert payload["evidence_returned"] == 1
        assert payload["evidence_limit"] == 25
        assert payload["negation_cue_evidence"] == 0
        assert payload["topic_match_kind"] == "exact"
        assert payload["claim_match_kind"] == "exact"
        assert payload["topic_query_exact_threadmarks"] == 1
        assert payload["topic_query_exact_chunks"] == 1
        assert payload["claim_query_exact_threadmarks"] == 1
        assert payload["claim_query_exact_chunks"] == 1
        assert payload["cautions"] == []
        assert "claim_snippet_html" in payload["evidence"][0]
        assert "topic_snippet_html" in payload["evidence"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_claim_endpoint_prefix_variants_can_expand_topic_side(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba appears directly in an unrelated note.",
                word_count=7,
            ),
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuban policy did not become communist in this timeline.",
                word_count=8,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/claim?q=Cuba&claim=communist")
        default_response = conn.getresponse()
        default_payload = json.loads(default_response.read().decode("utf-8"))
        conn.request("GET", "/api/claim?q=Cuba&claim=communist&prefix_variants=1")
        variant_response = conn.getresponse()
        variant_payload = json.loads(variant_response.read().decode("utf-8"))

        assert default_response.status == 200
        assert default_payload["prefix_variants"] is False
        assert default_payload["evidence_level"] == "no-overlap"
        assert default_payload["topic_match_kind"] == "exact"
        assert [item["code"] for item in default_payload["cautions"]] == ["no-overlap"]
        assert variant_response.status == 200
        assert variant_payload["prefix_variants"] is True
        assert variant_payload["topic_match_kind"] == "prefix-variants"
        assert variant_payload["topic_match_query"] == '"Cuba"*'
        assert variant_payload["evidence_level"] == "strong-chunk-overlap"
        assert variant_payload["negation_cue_evidence"] == 1
        assert [item["code"] for item in variant_payload["cautions"]] == [
            "topic-prefix-match",
            "claim-prefix-match",
            "negation-cues",
        ]
        assert "body" not in variant_payload["evidence"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_claim_endpoint_infers_question_style_query(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba did not turn communist in this timeline.",
                word_count=8,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/claim?q=did%20Cuba%20turn%20communist")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["claim_inferred_from_query"] is True
        assert payload["original_query"] == "did Cuba turn communist"
        assert payload["topic_query"] == "Cuba"
        assert payload["claim_query"] == "communist"
        assert payload["evidence_level"] == "strong-chunk-overlap"
        assert payload["negation_cue_evidence"] == 1
        assert "body" not in payload["evidence"][0]
        assert "claim_snippet_html" in payload["evidence"][0]
        assert "topic_snippet_html" in payload["evidence"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_claim_endpoint_reports_negation_cues(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuba did not turn communist in this timeline.",
                word_count=8,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/claim?q=Cuba&claim=communist")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["negation_cue_evidence"] == 1
        assert [item["code"] for item in payload["cautions"]] == ["negation-cues"]
        assert "did not" in payload["evidence"][0]["claim_negation_cues"]
        assert "claim_snippet_html" in payload["evidence"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_claim_endpoint_merges_topic_aliases(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Castro and communist theory appear together.",
                word_count=6,
            ),
            Threadmark(
                order=2,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="2",
                post_id="2",
                title="Turn 2",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-2",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuban exchange programs are discussed separately.",
                word_count=6,
            ),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/claim?q=Cuba&claim=communist&alias=Castro")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["topic_aliases"] == ["Castro"]
        assert [term["query"] for term in payload["topic_terms"]] == ["Cuba", "Castro"]
        assert payload["evidence_level"] == "strong-chunk-overlap"
        assert payload["topic_threadmarks"] == 2
        assert payload["overlapping_chunks"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_claim_endpoint_reports_prefix_fallback_per_side(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            Threadmark(
                order=1,
                category_id=1,
                category_name="Threadmarks",
                threadmark_id="1",
                post_id="1",
                title="Turn 1",
                author="Blackstar",
                published_at=None,
                source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1",
                reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
                text="Cuban exchange programs and communist theory are discussed.",
                word_count=8,
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    class Handler(SearchHandler):
        database_path = db
        rate_limiter = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/claim?q=Cuba&claim=communist")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["evidence_level"] == "strong-chunk-overlap"
        assert payload["topic_match_kind"] == "prefix"
        assert payload["claim_match_kind"] == "exact"
        assert "*" in payload["topic_match_query"]
        assert payload["topic_query_exact_threadmarks"] == 0
        assert payload["topic_query_exact_chunks"] == 0
        assert payload["claim_query_exact_threadmarks"] == 1
        assert payload["claim_query_exact_chunks"] == 1
        assert [item["code"] for item in payload["cautions"]] == ["topic-prefix-match", "topic-exact-missing"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_robots_txt_disallows_public_crawling() -> None:
    assert ROBOTS_TXT == "User-agent: *\nDisallow: /\n"


def test_common_headers_include_public_safety_headers() -> None:
    headers: list[tuple[str, str]] = []
    handler = object.__new__(SearchHandler)
    handler.send_header = lambda name, value: headers.append((name, value))  # type: ignore[method-assign]

    handler.send_common_headers(cache_control="no-store")

    assert ("Cache-Control", "no-store") in headers
    assert ("Content-Security-Policy", "default-src 'none'; base-uri 'none'; frame-ancestors 'none'") in headers
    assert ("X-Robots-Tag", "noindex, nofollow") in headers
    assert ("Referrer-Policy", "no-referrer") in headers
    assert ("X-Content-Type-Options", "nosniff") in headers


def test_sliding_window_rate_limiter_blocks_after_limit() -> None:
    now = 100.0
    limiter = SlidingWindowRateLimiter(2, 60.0, clock=lambda: now)

    assert limiter.check("client").allowed is True
    assert limiter.check("client").allowed is True
    blocked = limiter.check("client")

    assert blocked.allowed is False
    assert blocked.retry_after_seconds == 60


def test_sliding_window_rate_limiter_allows_after_window() -> None:
    current = {"now": 100.0}
    limiter = SlidingWindowRateLimiter(1, 60.0, clock=lambda: current["now"])

    assert limiter.check("client").allowed is True
    current["now"] = 161.0

    assert limiter.check("client").allowed is True


def test_sliding_window_rate_limiter_zero_disables_limit() -> None:
    limiter = SlidingWindowRateLimiter(0, 60.0, clock=lambda: 100.0)

    assert limiter.check("client").allowed is True
    assert limiter.check("client").allowed is True
