import json
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread

from planquest.web import (
    APP_HTML,
    DETAIL_HTML,
    ROBOTS_TXT,
    SearchHandler,
    SlidingWindowRateLimiter,
    add_csp_nonce,
    bounded_query,
    clamp,
    health_payload,
    highlighted_source_url,
    html_csp,
    index_stats,
    text_fragment_directive,
    thread_id_from_reader_url,
    thread_title_from_reader_url,
    thread_url_key_from_reader_url,
)
from planquest.indexer import build_index
from planquest.models import Threadmark
from planquest.scrape import write_jsonl


def test_bounded_query_collapses_whitespace_and_caps_length() -> None:
    assert bounded_query("  Cuba\n  communism  ", 9) == "Cuba comm"


def test_clamp_bounds_values() -> None:
    assert clamp(0, 1, 5) == 1
    assert clamp(8, 1, 5) == 5
    assert clamp(3, 1, 5) == 3


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
        threadmark_limit_cap=46,
        query_char_cap=56,
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
    assert stats["source_thread_url_key"] == thread_url_key_from_reader_url(stats["source_reader_url"])
    assert stats["threads"] == [
        {
            "id": "attempting-to-fulfill-the-plan-mnkh-edition.73217",
            "url_key": stats["source_thread_url_key"],
            "title": "Attempting to Fulfill the Plan MNKh Edition",
            "reader_url": stats["source_reader_url"],
            "source_host": "forums.sufficientvelocity.com",
            "threadmarks": 1,
            "words": 3,
        }
    ]
    assert stats["public_access_mode"] == "source_linked_search"
    assert "source threadmarks" in stats["public_notice"]
    assert stats["public_contact"] == "mailto:operator@example.invalid"
    assert stats["removal_request_url"] == "https://search.example.invalid/removal"
    assert stats["search_limit_cap"] == 12
    assert stats["threadmark_limit_cap"] == 46
    assert stats["query_char_cap"] == 56
    assert stats["rate_limit_per_minute"] == 78
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


def test_thread_metadata_is_derived_from_reader_url() -> None:
    reader_url = "https://forums.sufficientvelocity.com/threads/a-young-womans-political-record.118774/reader/"
    mnkh_reader_url = (
        "https://forums.sufficientvelocity.com/threads/attempting-to-fulfill-the-plan-mnkh-edition.73217/reader/"
    )

    assert thread_id_from_reader_url(reader_url) == "a-young-womans-political-record.118774"
    assert thread_title_from_reader_url(reader_url) == "A Young Womans Political Record"
    assert thread_id_from_reader_url(mnkh_reader_url) == "attempting-to-fulfill-the-plan-mnkh-edition.73217"
    assert thread_title_from_reader_url(mnkh_reader_url) == "Attempting to Fulfill the Plan MNKh Edition"


def test_thread_url_key_is_compact_and_opaque() -> None:
    reader_url = "https://forums.sufficientvelocity.com/threads/a-young-womans-political-record.118774/reader/"

    key = thread_url_key_from_reader_url(reader_url)

    assert key == thread_url_key_from_reader_url(f" {reader_url} ")
    assert key == thread_url_key_from_reader_url(reader_url.rstrip("/"))
    assert len(key) == 12
    assert "young" not in key.lower()
    assert "118774" not in key
    assert key.replace("-", "").replace("_", "").isalnum()


def test_text_fragment_directive_targets_first_marked_span_with_context() -> None:
    snippet = "earlier omitted ... before the \x01Cuba\x02 mention after it ... later omitted"

    assert text_fragment_directive(snippet) == "text=before%20the-,Cuba"


def test_text_fragment_directive_escapes_ascii_dashes() -> None:
    snippet = "pre-war Nikolai \x01Voznesensky\x02 supported the plan"

    assert text_fragment_directive(snippet) == "text=pre%2Dwar%20Nikolai-,Voznesensky"


def test_text_fragment_directive_expands_possessive_target_to_word_boundary() -> None:
    snippet = "Industry 3 Dice (-2 Dice while no Minister, \x01Voz\x02's experience prevents -20 dice malus) ["

    assert (
        text_fragment_directive(snippet)
        == "text=Industry%203%20Dice%20%28%2D2%20Dice%20while%20no%20Minister%2C-,Voz%27s"
    )


def test_text_fragment_directive_expands_typographic_possessive_target() -> None:
    snippet = "before Nikolai \x01Voz\x02\u2019s plan after"

    assert text_fragment_directive(snippet) == "text=before%20Nikolai-,Voz%E2%80%99s"


def test_highlighted_source_url_uses_text_fragment_without_post_anchor() -> None:
    source_url = "https://forums.sufficientvelocity.com/threads/example.1/#post-123"
    snippet = "before the \x01Cuba\x02 mention after it"

    assert (
        highlighted_source_url(source_url, snippet)
        == "https://forums.sufficientvelocity.com/threads/example.1/#:~:text=before%20the-,Cuba"
    )


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
    assert "/api/threadmarks?" in APP_HTML
    assert 'id="count"' in APP_HTML
    assert APP_HTML.count('id="count"') == 1
    assert "|[])}." not in APP_HTML
    assert 'id="source-link"' not in APP_HTML
    assert 'id="thread-picker"' in APP_HTML
    assert 'id="thread-picker-input"' in APP_HTML
    assert 'id="thread-source-link"' in APP_HTML
    assert "Source thread" in APP_HTML
    assert "updateThreadSourceLink" in APP_HTML
    assert 'role="combobox"' in APP_HTML
    assert 'id="thread-options"' in APP_HTML
    assert 'role="listbox"' in APP_HTML
    assert "fuzzyThreadScore" in APP_HTML
    assert "filteredThreadOptions" in APP_HTML
    assert "selectThread" in APP_HTML
    assert "findThreadOption" in APP_HTML
    assert "url_key" in APP_HTML
    assert "legacy_id" in APP_HTML
    assert "payload.threads" in APP_HTML
    assert 'id="query"' in APP_HTML
    assert 'id="from-order"' in APP_HTML
    assert 'id="to-order"' in APP_HTML
    assert 'id="all-words"' in APP_HTML
    assert "All words" in APP_HTML
    assert "Search thread text" in APP_HTML
    assert "renderThreadmarkGroup" in APP_HTML
    assert "renderHit" in APP_HTML
    assert "hit.highlight_url" in APP_HTML
    assert "hit-link" in APP_HTML
    assert "payload.threadmarks" in APP_HTML
    assert "/api/search" in APP_HTML
    assert 'id="query-tools"' not in APP_HTML
    assert 'id="public-notice"' not in APP_HTML
    assert 'id="grouped"' not in APP_HTML
    assert 'id="clear-range"' not in APP_HTML
    assert 'id="share-link"' not in APP_HTML
    assert "Word variants" not in APP_HTML
    assert "One hit per threadmark" not in APP_HTML
    assert "uiStateParams" in APP_HTML
    assert 'params.set("thread", selectedThreadId)' in APP_HTML
    assert 'params.set("view", "contents")' in APP_HTML
    assert "threadOptions.length > 1" not in APP_HTML
    assert "resultCountText" in APP_HTML
    assert "All matching threadmarks" not in APP_HTML
    assert "prefix fallback" not in APP_HTML
    assert "matched with word variants" not in APP_HTML
    assert "renderMatchNote" not in APP_HTML
    assert "/api/threadmark/" not in APP_HTML


def test_web_templates_use_dark_theme() -> None:
    assert "color-scheme: dark;" in APP_HTML
    assert "color-scheme: dark;" in DETAIL_HTML
    assert 'name="theme-color" content="#101312"' in APP_HTML
    assert 'name="theme-color" content="#101312"' in DETAIL_HTML
    assert "color-scheme: light;" not in APP_HTML
    assert "color-scheme: light;" not in DETAIL_HTML
    assert "#edf7f4" not in APP_HTML
    assert "rgba(255,255,255,0.55)" not in APP_HTML
    assert "#8a1f11" not in DETAIL_HTML


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
        assert payload["word_variants"] is True
        assert payload["match_kind"] == "prefix-variants"
        assert "*" in payload["match_query"]
        assert payload["result_count"] == 1
        assert payload["total_threadmarks"] == 1
        assert payload["total_chunks"] == 1
        assert payload["results"][0]["match_kind"] == "prefix-variants"
        assert payload["threadmarks"][0]["hit_count"] == 1
        assert payload["threadmarks"][0]["hits"][0]["snippet_html"]
        assert payload["results"][0]["highlight_url"].startswith(
            "https://forums.sufficientvelocity.com/threads/example.1/#:~:text="
        )
        assert "Cuban" in payload["results"][0]["highlight_url"]
        assert payload["threadmarks"][0]["hits"][0]["highlight_url"] == payload["results"][0]["highlight_url"]
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
        assert [item["threadmark_order"] for item in payload["results"]] == [1, 2]
        assert [item["threadmark_order"] for item in payload["threadmarks"]] == [1, 2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_search_endpoint_groups_all_hits_by_threadmark(tmp_path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    long_hit = "Cuba appears here. " + ("filler " * 280)
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
                text=f"{long_hit}\n\n{long_hit}",
                word_count=len(f"{long_hit} {long_hit}".split()),
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
        conn.request("GET", "/api/search?q=Cuba")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["result_count"] == 3
        assert [item["threadmark_order"] for item in payload["threadmarks"]] == [1, 2]
        assert [item["hit_count"] for item in payload["threadmarks"]] == [2, 1]
        assert len(payload["threadmarks"][0]["hits"]) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_search_endpoint_always_includes_word_variants(tmp_path) -> None:
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
        assert default_payload["word_variants"] is True
        assert default_payload["match_kind"] == "prefix-variants"
        assert [item["threadmark_order"] for item in default_payload["results"]] == [1, 2]
        assert variant_response.status == 200
        assert variant_payload["word_variants"] is True
        assert variant_payload["match_kind"] == "prefix-variants"
        assert variant_payload["match_query"] == '"Cuba"*'
        assert [item["threadmark_order"] for item in variant_payload["results"]] == [1, 2]
        assert [item["threadmark_order"] for item in variant_payload["threadmarks"]] == [1, 2]
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
        assert payload["result_count"] == 2
        assert payload["total_threadmarks"] == 2
        assert payload["total_chunks"] == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
        conn.request("HEAD", "/api/search?q=Cuba")
        response = conn.getresponse()

        assert response.status == 200
        assert response.getheader("Content-Type") == "application/json; charset=utf-8"
        assert response.read() == b""
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
