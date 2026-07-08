from __future__ import annotations

from dataclasses import asdict
import html
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sqlite3
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Callable, Deque
from urllib.parse import parse_qs, urlparse

from .config import TARGET_READER_URL
from .db import connect_readonly
from .search import (
    list_threadmarks_db,
    search_db,
    search_totals_db,
    threadmark_detail,
)


NON_HTML_CSP = "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
DEFAULT_PUBLIC_SNIPPET_BUDGET_CHARS = 6000
THREAD_TITLE_OVERRIDES = {
    "attempting-to-fulfill-the-plan-mnkh-edition.73217": "Attempting to Fulfill the Plan MNKh Edition",
}
REMOVED_PUBLIC_API_PATHS = {
    "/api/suggest",
    "/api/terms",
    "/api/explain",
    "/api/report",
    "/api/mentions",
    "/api/dossier",
    "/api/evidence-pack",
    "/api/recap",
    "/api/coverage",
    "/api/compare",
    "/api/claim",
}


def html_csp(nonce: str) -> str:
    return (
        "default-src 'self'; "
        "base-uri 'none'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "img-src 'none'; "
        "font-src 'none'; "
        "connect-src 'self'; "
        f"script-src 'nonce-{nonce}'; "
        f"style-src 'nonce-{nonce}'"
    )


def add_csp_nonce(body: str, nonce: str) -> str:
    return body.replace("<style>", f'<style nonce="{nonce}">').replace("<script>", f'<script nonce="{nonce}">')


def serve(
    db_path: Path,
    host: str,
    port: int,
    private_fulltext: bool = False,
    public_search_limit: int = 30,
    public_report_limit: int = 100,
    public_mention_limit: int = 50,
    public_threadmark_limit: int = 300,
    max_query_chars: int = 120,
    mention_window_chars: int = 320,
    public_snippet_budget_chars: int = DEFAULT_PUBLIC_SNIPPET_BUDGET_CHARS,
    public_rate_limit_per_minute: int = 60,
    allow_public_chunk_results: bool = False,
    public_contact: str = "",
    removal_request_url: str = "",
    artifact_manifest_validated: bool = False,
    artifact_manifest_sha256: str = "",
    artifact_database_sha256: str = "",
    artifact_created_at_utc: str = "",
) -> None:
    class Handler(SearchHandler):
        database_path = db_path
        allow_private_fulltext = private_fulltext
        search_limit_cap = public_search_limit
        report_limit_cap = public_report_limit
        mention_limit_cap = public_mention_limit
        threadmark_limit_cap = public_threadmark_limit
        query_char_cap = max_query_chars
        mention_window_char_cap = mention_window_chars
        snippet_budget_char_cap = public_snippet_budget_chars
        rate_limiter = SlidingWindowRateLimiter(public_rate_limit_per_minute, 60.0)
        allow_chunk_results = private_fulltext or allow_public_chunk_results
        public_contact_value = public_contact
        removal_request_url_value = removal_request_url
        artifact_manifest_validated_value = artifact_manifest_validated
        artifact_manifest_sha256_value = artifact_manifest_sha256
        artifact_database_sha256_value = artifact_database_sha256
        artifact_created_at_utc_value = artifact_created_at_utc

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving thread search at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


class SearchHandler(BaseHTTPRequestHandler):
    database_path: Path
    allow_private_fulltext = False
    search_limit_cap = 30
    report_limit_cap = 100
    mention_limit_cap = 50
    threadmark_limit_cap = 300
    query_char_cap = 120
    mention_window_char_cap = 320
    snippet_budget_char_cap = DEFAULT_PUBLIC_SNIPPET_BUDGET_CHARS
    rate_limiter: SlidingWindowRateLimiter | None = None
    allow_chunk_results = False
    public_contact_value = ""
    removal_request_url_value = ""
    artifact_manifest_validated_value = False
    artifact_manifest_sha256_value = ""
    artifact_database_sha256_value = ""
    artifact_created_at_utc_value = ""

    def do_HEAD(self) -> None:
        self.handle_request(head_only=True)

    def do_GET(self) -> None:
        self.handle_request(head_only=False)

    def handle_request(self, head_only: bool = False) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.respond_html(APP_HTML, head_only=head_only)
            return
        if parsed.path == "/robots.txt":
            self.respond_text(ROBOTS_TXT, content_type="text/plain; charset=utf-8", head_only=head_only)
            return
        if parsed.path == "/healthz":
            payload = health_payload(self.database_path)
            self.respond_json(payload, head_only=head_only, status=200 if payload["ok"] else 503)
            return
        if parsed.path == "/api/stats":
            self.respond_json(
                index_stats(
                    self.database_path,
                    self.allow_private_fulltext,
                    self.search_limit_cap,
                    self.report_limit_cap,
                    self.mention_limit_cap,
                    self.threadmark_limit_cap,
                    self.query_char_cap,
                    self.mention_window_char_cap,
                    self.snippet_budget_char_cap,
                    self.rate_limiter.limit if self.rate_limiter else 0,
                    self.allow_chunk_results,
                    public_contact=self.public_contact_value,
                    removal_request_url=self.removal_request_url_value,
                    artifact_manifest_validated=self.artifact_manifest_validated_value,
                    artifact_manifest_sha256=self.artifact_manifest_sha256_value,
                    artifact_database_sha256=self.artifact_database_sha256_value,
                    artifact_created_at_utc=self.artifact_created_at_utc_value,
                ),
                head_only=head_only,
            )
            return
        if self.is_public_api_path(parsed.path) and not self.check_rate_limit(head_only=head_only):
            return
        if parsed.path in REMOVED_PUBLIC_API_PATHS:
            self.send_error(404)
            return
        if parsed.path == "/api/threadmarks":
            params = parse_qs(parsed.query)
            limit = clamp(parse_int(params.get("limit", ["100"])[0], 100), 1, self.threadmark_limit_cap)
            order_min = parse_optional_int(params.get("from", [""])[0])
            order_max = parse_optional_int(params.get("to", [""])[0])
            items = list_threadmarks_db(
                self.database_path,
                limit=limit,
                order_min=order_min,
                order_max=order_max,
            )
            self.respond_json({"items": [asdict(item) for item in items]}, head_only=head_only)
            return
        if parsed.path.startswith("/api/threadmark/"):
            if not self.allow_private_fulltext:
                self.send_error(404)
                return
            post_id = parsed.path.removeprefix("/api/threadmark/").strip("/")
            detail = threadmark_detail(self.database_path, post_id)
            if detail is None:
                self.send_error(404)
                return
            self.respond_json(asdict(detail), head_only=head_only)
            return
        if parsed.path == "/api/search":
            params = parse_qs(parsed.query)
            query = bounded_query(params.get("q", [""])[0], self.query_char_cap)
            mode = params.get("mode", ["all"])[0]
            if mode not in {"all", "any"}:
                mode = "all"
            order_min = parse_optional_int(params.get("from", [""])[0])
            order_max = parse_optional_int(params.get("to", [""])[0])
            results = []
            totals = None
            if query.strip():
                totals = search_totals_db(
                    self.database_path,
                    query,
                    mode=mode,
                    order_min=order_min,
                    order_max=order_max,
                    prefix_variants=True,
                )
                for result in search_db(
                    self.database_path,
                    query,
                    limit=max(1, totals.total_chunks),
                    mode=mode,
                    order_min=order_min,
                    order_max=order_max,
                    grouped=False,
                    sort="timeline",
                    prefix_variants=True,
                ):
                    item = asdict(result)
                    item["local_url"] = f"/threadmark/{result.post_id}" if self.allow_private_fulltext else None
                    item["snippet_html"] = snippet_html(item["snippet"])
                    results.append(item)
            payload = {
                "query": query,
                "mode": mode,
                "word_variants": True,
                "match_kind": totals.match_kind if totals else "none",
                "match_query": totals.match_query if totals else "",
                "result_count": len(results),
                "hit_count": len(results),
                "total_threadmarks": totals.total_threadmarks if totals else 0,
                "total_chunks": totals.total_chunks if totals else 0,
                "threadmarks": group_search_results(results),
                "results": results,
            }
            self.respond_json(payload, head_only=head_only)
            return
        if parsed.path.startswith("/threadmark/") and self.allow_private_fulltext:
            self.respond_html(DETAIL_HTML, head_only=head_only)
            return
        self.send_error(404)

    @staticmethod
    def is_public_api_path(path: str) -> bool:
        return path in {
            "/api/search",
            "/api/threadmarks",
        } or path.startswith("/api/threadmark/")

    def check_rate_limit(self, head_only: bool = False) -> bool:
        if self.rate_limiter is None:
            return True
        result = self.rate_limiter.check(self.client_address[0])
        if result.allowed:
            return True
        self.respond_json(
            {"ok": False, "error": "rate limit exceeded", "retry_after_seconds": result.retry_after_seconds},
            status=429,
            extra_headers={"Retry-After": str(result.retry_after_seconds)},
            head_only=head_only,
        )
        return False

    def log_message(self, format: str, *args: object) -> None:
        return

    def respond_html(self, body: str, head_only: bool = False) -> None:
        nonce = secrets.token_urlsafe(18)
        body = add_csp_nonce(body, nonce)
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_common_headers(cache_control="no-store", content_security_policy=html_csp(nonce))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def respond_json(
        self,
        payload: dict[str, object],
        head_only: bool = False,
        status: int = 200,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_common_headers(cache_control="no-store", content_security_policy=NON_HTML_CSP)
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def respond_text(
        self,
        body: str,
        content_type: str = "text/plain; charset=utf-8",
        head_only: bool = False,
        status: int = 200,
    ) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_common_headers(cache_control="no-store", content_security_policy=NON_HTML_CSP)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def send_common_headers(self, cache_control: str, content_security_policy: str = NON_HTML_CSP) -> None:
        self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Security-Policy", content_security_policy)
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Permissions-Policy", "interest-cohort=()")


def group_search_results(results: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    by_post: dict[str, dict[str, object]] = {}
    for item in results:
        post_id = str(item.get("post_id") or "")
        group = by_post.get(post_id)
        if group is None:
            group = {
                "title": item.get("title"),
                "post_id": post_id,
                "threadmark_order": item.get("threadmark_order"),
                "author": item.get("author"),
                "published_at": item.get("published_at"),
                "source_url": item.get("source_url"),
                "local_url": item.get("local_url"),
                "hit_count": 0,
                "hits": [],
            }
            by_post[post_id] = group
            groups.append(group)
        hits = group["hits"]
        if isinstance(hits, list):
            hits.append(
                {
                    "chunk_index": item.get("chunk_index"),
                    "snippet": item.get("snippet"),
                    "snippet_html": item.get("snippet_html"),
                    "source_url": item.get("source_url"),
                }
            )
            group["hit_count"] = len(hits)
    return groups


def index_stats(
    db_path: Path,
    private_fulltext: bool = False,
    search_limit_cap: int = 30,
    report_limit_cap: int = 100,
    mention_limit_cap: int = 50,
    threadmark_limit_cap: int = 300,
    query_char_cap: int = 120,
    mention_window_char_cap: int = 320,
    snippet_budget_char_cap: int = DEFAULT_PUBLIC_SNIPPET_BUDGET_CHARS,
    rate_limit_per_minute: int = 60,
    allow_chunk_results: bool = False,
    source_reader_url: str = TARGET_READER_URL,
    public_contact: str = "",
    removal_request_url: str = "",
    artifact_manifest_validated: bool = False,
    artifact_manifest_sha256: str = "",
    artifact_database_sha256: str = "",
    artifact_created_at_utc: str = "",
) -> dict[str, object]:
    if not db_path.exists():
        return {"ok": False, "error": f"missing database: {db_path}"}
    try:
        with connect_readonly(db_path) as conn:
            threadmarks = int(conn.execute("SELECT COUNT(*) FROM threadmarks").fetchone()[0])
            chunks = int(conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0])
            words = int(conn.execute("SELECT COALESCE(SUM(word_count), 0) FROM threadmarks").fetchone()[0])
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc)}
    source_host = urlparse(source_reader_url).netloc
    threads = [
        {
            "id": thread_id_from_reader_url(source_reader_url),
            "title": thread_title_from_reader_url(source_reader_url),
            "reader_url": source_reader_url,
            "source_host": source_host,
            "threadmarks": threadmarks,
            "words": words,
        }
    ]
    return {
        "ok": True,
        "source_reader_url": source_reader_url,
        "source_host": source_host,
        "threads": threads,
        "public_access_mode": "source_linked_search",
        "public_notice": "Search results link back to their source threadmarks.",
        "public_contact": public_contact,
        "removal_request_url": removal_request_url,
        "artifact_manifest_validated": artifact_manifest_validated,
        "artifact_manifest_sha256": artifact_manifest_sha256,
        "artifact_database_sha256": artifact_database_sha256,
        "artifact_created_at_utc": artifact_created_at_utc,
        "threadmarks": threadmarks,
        "chunks": chunks,
        "words": words,
        "private_fulltext": private_fulltext,
        "search_limit_cap": search_limit_cap,
        "report_limit_cap": report_limit_cap,
        "mention_limit_cap": mention_limit_cap,
        "threadmark_limit_cap": threadmark_limit_cap,
        "query_char_cap": query_char_cap,
        "mention_window_char_cap": mention_window_char_cap,
        "snippet_budget_char_cap": snippet_budget_char_cap,
        "rate_limit_per_minute": rate_limit_per_minute,
        "chunk_results_enabled": allow_chunk_results,
    }


def thread_id_from_reader_url(source_reader_url: str) -> str:
    slug = thread_slug_from_reader_url(source_reader_url)
    if slug:
        return slug
    parsed = urlparse(source_reader_url)
    return parsed.netloc or "default"


def thread_title_from_reader_url(source_reader_url: str) -> str:
    slug = thread_slug_from_reader_url(source_reader_url)
    parsed = urlparse(source_reader_url)
    if not slug:
        return parsed.netloc or "Thread"
    if slug in THREAD_TITLE_OVERRIDES:
        return THREAD_TITLE_OVERRIDES[slug]
    title_slug = slug
    parts = slug.rsplit(".", 1)
    if len(parts) == 2 and parts[1].isdigit():
        title_slug = parts[0]
    title = " ".join(word.capitalize() for word in title_slug.replace("-", " ").replace("_", " ").split())
    return title or parsed.netloc or "Thread"


def thread_slug_from_reader_url(source_reader_url: str) -> str:
    parts = [part for part in urlparse(source_reader_url).path.split("/") if part]
    try:
        threads_index = parts.index("threads")
    except ValueError:
        return ""
    if threads_index + 1 >= len(parts):
        return ""
    return parts[threads_index + 1]


def health_payload(db_path: Path) -> dict[str, object]:
    stats = index_stats(db_path)
    ok = (
        stats.get("ok") is True
        and int(stats.get("threadmarks") or 0) > 0
        and int(stats.get("chunks") or 0) >= int(stats.get("threadmarks") or 0)
    )
    payload: dict[str, object] = {"ok": ok}
    if ok:
        payload["threadmarks"] = stats["threadmarks"]
        payload["chunks"] = stats["chunks"]
    else:
        payload["error"] = stats.get("error", "database is not ready")
    return payload


class RateLimitResult:
    def __init__(self, allowed: bool, retry_after_seconds: int = 0) -> None:
        self.allowed = allowed
        self.retry_after_seconds = retry_after_seconds


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: float, clock: Callable[[], float] | None = None) -> None:
        self.limit = max(0, limit)
        self.window_seconds = window_seconds
        self.clock = clock or time.monotonic
        self._hits: defaultdict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> RateLimitResult:
        if self.limit <= 0:
            return RateLimitResult(True)

        now = self.clock()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = max(1, int(round(hits[0] + self.window_seconds - now)))
                return RateLimitResult(False, retry_after)
            hits.append(now)
            return RateLimitResult(True)


def parse_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_optional_int(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def bounded_query(value: str, max_chars: int) -> str:
    value = " ".join(value.split())
    if max_chars > 0:
        value = value[:max_chars]
    return value


def snippet_html(snippet: str) -> str:
    escaped = html.escape(snippet)
    return escaped.replace("\x01", "<mark>").replace("\x02", "</mark>")


ROBOTS_TXT = """User-agent: *
Disallow: /
"""


APP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>Thread Search</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8f5;
      --panel: #ffffff;
      --ink: #1d2528;
      --muted: #667074;
      --line: #cfd8d1;
      --accent: #0f766e;
      --accent-strong: #134e4a;
      --mark: #ffe08a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }
    main {
      width: min(1040px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 48px;
    }
    header {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 14px;
    }
    h1 {
      margin: 0;
      font-size: 24px;
      font-weight: 720;
      letter-spacing: 0;
    }
    .title-block {
      display: grid;
      gap: 8px;
      min-width: 0;
    }
    .thread-picker {
      position: relative;
      width: min(420px, calc(100vw - 32px));
    }
    .thread-source-link {
      color: var(--accent);
      font-size: 13px;
      font-weight: 650;
      text-decoration: none;
      width: max-content;
    }
    .thread-source-link:hover {
      text-decoration: underline;
    }
    .thread-source-link[hidden] {
      display: none;
    }
    .thread-picker-label {
      display: block;
      margin-bottom: 3px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
      letter-spacing: 0;
      text-transform: uppercase;
    }
    .thread-combobox {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 40px;
      height: 42px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
    }
    .thread-combobox:focus-within {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(15, 118, 110, 0.14);
    }
    .thread-combobox input {
      height: 40px;
      border: 0;
      border-radius: 5px 0 0 5px;
      background: transparent;
    }
    .thread-combobox input:focus {
      outline: none;
    }
    .thread-combobox button {
      display: grid;
      place-items: center;
      width: 40px;
      height: 40px;
      padding: 0;
      border: 0;
      border-left: 1px solid var(--line);
      border-radius: 0 5px 5px 0;
      background: transparent;
      color: var(--accent-strong);
    }
    .thread-combobox button:hover {
      background: #edf7f4;
    }
    .thread-picker-arrow {
      width: 9px;
      height: 9px;
      border-right: 2px solid currentColor;
      border-bottom: 2px solid currentColor;
      transform: rotate(45deg);
      margin-top: -4px;
    }
    .thread-options {
      position: absolute;
      z-index: 20;
      top: calc(100% + 4px);
      left: 0;
      right: 0;
      max-height: 260px;
      overflow-y: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: 0 12px 28px rgba(29, 37, 40, 0.16);
      padding: 4px;
    }
    .thread-option {
      display: grid;
      gap: 2px;
      width: 100%;
      height: auto;
      min-height: 46px;
      padding: 7px 9px;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: var(--ink);
      text-align: left;
    }
    .thread-option:hover,
    .thread-option.is-active,
    .thread-option[aria-selected="true"] {
      background: #edf7f4;
      color: var(--ink);
    }
    .thread-option-title {
      font-weight: 700;
      overflow-wrap: anywhere;
    }
    .thread-option-meta,
    .thread-option-empty {
      color: var(--muted);
      font-size: 12px;
    }
    .thread-option-empty {
      padding: 8px 10px;
    }
    .count {
      min-height: 20px;
      color: var(--muted);
      font-size: 14px;
      text-align: right;
    }
    .tabs {
      display: flex;
      gap: 6px;
      margin-bottom: 14px;
      border-bottom: 1px solid var(--line);
    }
    .tab {
      height: 38px;
      border: 0;
      border-radius: 6px 6px 0 0;
      padding: 0 14px;
      background: transparent;
      color: var(--muted);
      font-weight: 650;
      cursor: pointer;
    }
    .tab[aria-selected="true"] {
      background: var(--panel);
      color: var(--ink);
      box-shadow: inset 0 -2px 0 var(--accent);
    }
    .tab-panel[hidden] { display: none; }
    form {
      display: grid;
      grid-template-columns: minmax(260px, 1fr) 108px 108px auto auto;
      gap: 10px;
      margin-bottom: 14px;
    }
    input, button {
      height: 42px;
      border: 1px solid var(--line);
      border-radius: 6px;
      font: inherit;
    }
    input {
      min-width: 0;
      padding: 0 12px;
      background: var(--panel);
      color: var(--ink);
    }
    input[type="number"] { appearance: textfield; }
    button {
      padding: 0 16px;
      background: var(--accent);
      color: white;
      border-color: var(--accent);
      font-weight: 650;
      cursor: pointer;
    }
    button:hover { background: var(--accent-strong); }
    .checkbox {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      height: 42px;
      padding: 0 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--muted);
      font-size: 14px;
      white-space: nowrap;
    }
    .checkbox input {
      width: 16px;
      height: 16px;
      margin: 0;
      padding: 0;
    }
    .status {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      min-height: 20px;
      margin: -4px 0 14px;
      color: var(--muted);
      font-size: 13px;
    }
    .results,
    .contents,
    .hit-list {
      display: grid;
      gap: 10px;
    }
    article {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px 16px;
    }
    article h2 {
      margin: 0 0 6px;
      font-size: 16px;
      line-height: 1.3;
      letter-spacing: 0;
    }
    article h2 a,
    .toc-title a,
    .actions a {
      color: var(--accent-strong);
      text-decoration: none;
      font-weight: 650;
      overflow-wrap: anywhere;
    }
    article h2 a:hover,
    .toc-title a:hover,
    .actions a:hover { text-decoration: underline; }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 12px;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 10px;
    }
    .hit {
      border-top: 1px solid var(--line);
      padding-top: 10px;
    }
    .hit:first-child {
      border-top: 0;
      padding-top: 0;
    }
    .hit-label {
      margin-bottom: 3px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    .snippet {
      margin: 0;
      font-size: 15px;
      overflow-wrap: anywhere;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 10px;
      font-size: 13px;
    }
    mark {
      background: var(--mark);
      color: inherit;
      padding: 0 2px;
      border-radius: 3px;
    }
    .empty {
      min-height: 44px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 18px;
      color: var(--muted);
      background: rgba(255,255,255,0.55);
    }
    .toc-row {
      display: grid;
      grid-template-columns: 72px minmax(0, 1fr) auto;
      gap: 12px;
      align-items: start;
      border-bottom: 1px solid var(--line);
      padding: 10px 0;
    }
    .toc-order {
      color: var(--muted);
      font-variant-numeric: tabular-nums;
      font-size: 13px;
    }
    .toc-title { min-width: 0; }
    .toc-meta {
      margin-top: 4px;
      color: var(--muted);
      font-size: 13px;
    }
    .toc-words {
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
      text-align: right;
    }
    @media (max-width: 700px) {
      main { width: min(100vw - 20px, 1040px); padding-top: 16px; }
      header { align-items: start; flex-direction: column; }
      .count { text-align: left; }
      form { grid-template-columns: 1fr; }
      input, button, .checkbox { width: 100%; }
      .tabs { overflow-x: auto; }
      .toc-row { grid-template-columns: 52px minmax(0, 1fr); }
      .toc-words { grid-column: 2; text-align: left; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div class="title-block">
        <h1>Thread Search</h1>
        <div id="thread-picker" class="thread-picker">
          <label class="thread-picker-label" for="thread-picker-input">Thread</label>
          <div class="thread-combobox">
            <input id="thread-picker-input" type="search" autocomplete="off" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="thread-options" placeholder="Choose a thread">
            <button id="thread-picker-toggle" type="button" aria-label="Show thread options"><span class="thread-picker-arrow" aria-hidden="true"></span></button>
          </div>
          <div id="thread-options" class="thread-options" role="listbox" hidden></div>
        </div>
        <a id="thread-source-link" class="thread-source-link" href="#" target="_blank" rel="noopener noreferrer" hidden>Source thread</a>
      </div>
      <div id="count" class="count"></div>
    </header>
    <div class="tabs" role="tablist" aria-label="Views">
      <button id="tab-search" class="tab" type="button" role="tab" aria-selected="true" aria-controls="panel-search">Search</button>
      <button id="tab-contents" class="tab" type="button" role="tab" aria-selected="false" aria-controls="panel-contents">Contents</button>
    </div>
    <section id="panel-search" class="tab-panel" role="tabpanel" aria-labelledby="tab-search">
      <form id="search-form">
        <input id="query" name="q" type="search" autocomplete="off" autofocus placeholder="Search thread text">
        <input id="from-order" name="from" type="number" min="1" inputmode="numeric" placeholder="From #">
        <input id="to-order" name="to" type="number" min="1" inputmode="numeric" placeholder="To #">
        <label class="checkbox"><input id="all-words" type="checkbox" checked> All words</label>
        <button type="submit">Search</button>
      </form>
      <div class="status">
        <span id="stats"></span>
        <span id="range"></span>
      </div>
      <section id="results" class="results">
        <div class="empty"></div>
      </section>
    </section>
    <section id="panel-contents" class="tab-panel" role="tabpanel" aria-labelledby="tab-contents" hidden>
      <div class="status">
        <span id="toc-count"></span>
        <span></span>
      </div>
      <section id="contents" class="contents"></section>
    </section>
  </main>
  <script>
    const searchTab = document.querySelector("#tab-search");
    const contentsTab = document.querySelector("#tab-contents");
    const searchPanel = document.querySelector("#panel-search");
    const contentsPanel = document.querySelector("#panel-contents");
    const threadPicker = document.querySelector("#thread-picker");
    const threadPickerInput = document.querySelector("#thread-picker-input");
    const threadPickerToggle = document.querySelector("#thread-picker-toggle");
    const threadOptionsPanel = document.querySelector("#thread-options");
    const threadSourceLink = document.querySelector("#thread-source-link");
    const form = document.querySelector("#search-form");
    const query = document.querySelector("#query");
    const fromOrder = document.querySelector("#from-order");
    const toOrder = document.querySelector("#to-order");
    const allWords = document.querySelector("#all-words");
    const results = document.querySelector("#results");
    const count = document.querySelector("#count");
    const stats = document.querySelector("#stats");
    const range = document.querySelector("#range");
    const contents = document.querySelector("#contents");
    const tocCount = document.querySelector("#toc-count");
    let timer = null;
    let privateFulltext = false;
    let contentsLoaded = false;
    let statsPayload = null;
    let threadOptions = [];
    let selectedThreadId = "";
    let activeThreadOptionId = "";

    const initial = new URLSearchParams(window.location.search);
    selectedThreadId = initial.get("thread") || "";
    query.value = initial.get("q") || "";
    fromOrder.value = initial.get("from") || "";
    toOrder.value = initial.get("to") || "";
    allWords.checked = (initial.get("mode") || "all") !== "any";

    form.addEventListener("submit", event => {
      event.preventDefault();
      runSearch();
    });
    [query, fromOrder, toOrder].forEach(input => input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(runSearch, 220);
    }));
    allWords.addEventListener("change", runSearch);
    threadPickerInput.addEventListener("focus", () => {
      threadPickerInput.select();
      openThreadOptions();
    });
    threadPickerInput.addEventListener("input", () => openThreadOptions());
    threadPickerInput.addEventListener("keydown", event => handleThreadPickerKeydown(event));
    threadPickerInput.addEventListener("blur", () => {
      window.setTimeout(() => {
        if (!threadPicker.contains(document.activeElement)) {
          resetThreadPickerInput();
          closeThreadOptions();
        }
      }, 120);
    });
    threadPickerToggle.addEventListener("mousedown", event => event.preventDefault());
    threadPickerToggle.addEventListener("click", () => {
      if (threadOptionsPanel.hidden) {
        threadPickerInput.focus();
        openThreadOptions();
      } else {
        closeThreadOptions();
      }
    });
    threadOptionsPanel.addEventListener("click", event => {
      const button = event.target.closest("button[data-thread-id]");
      if (!button) return;
      selectThread(button.dataset.threadId, { runSearch: true });
    });
    document.addEventListener("click", event => {
      if (threadPicker.contains(event.target)) return;
      resetThreadPickerInput();
      closeThreadOptions();
    });
    searchTab.addEventListener("click", () => setActiveTab("search"));
    contentsTab.addEventListener("click", () => setActiveTab("contents"));

    loadStats();
    if (query.value.trim()) runSearch();
    if (initial.get("view") === "contents") setActiveTab("contents");

    function setActiveTab(name) {
      const showContents = name === "contents";
      searchTab.setAttribute("aria-selected", showContents ? "false" : "true");
      contentsTab.setAttribute("aria-selected", showContents ? "true" : "false");
      searchPanel.hidden = showContents;
      contentsPanel.hidden = !showContents;
      if (showContents && !contentsLoaded) loadContents();
      updateUrl();
    }

    async function runSearch() {
      const q = query.value.trim();
      range.textContent = rangeText();
      if (!q) {
        count.textContent = "";
        results.innerHTML = '<div class="empty"></div>';
        updateUrl();
        return;
      }
      const response = await fetch(`/api/search?${searchParams().toString()}`);
      const payload = await response.json();
      count.textContent = resultCountText(payload);
      const groups = payload.threadmarks || [];
      results.innerHTML = groups.map(renderThreadmarkGroup).join("") || '<div class="empty">No results</div>';
      updateUrl();
    }

    async function loadStats() {
      const response = await fetch("/api/stats");
      const payload = await response.json();
      if (!payload.ok) return;
      statsPayload = payload;
      setThreadOptions(Array.isArray(payload.threads) && payload.threads.length ? payload.threads : fallbackThreadOptions(payload));
      privateFulltext = Boolean(payload.private_fulltext);
      updateStatsText();
    }

    function setThreadOptions(items) {
      threadOptions = items.map((item, index) => ({
        id: String(item.id || item.reader_url || `thread-${index + 1}`),
        title: String(item.title || item.source_host || item.reader_url || `Thread ${index + 1}`),
        reader_url: String(item.reader_url || ""),
        source_host: String(item.source_host || ""),
        threadmarks: Number(item.threadmarks || 0),
        words: Number(item.words || 0)
      }));
      if (!threadOptions.length) {
        threadPickerInput.value = "";
        threadPickerInput.disabled = true;
        threadPickerToggle.disabled = true;
        updateThreadSourceLink(null);
        return;
      }
      threadPickerInput.disabled = false;
      threadPickerToggle.disabled = false;
      const initialThread = threadOptions.find(item => item.id === selectedThreadId) || threadOptions[0];
      selectThread(initialThread.id, { runSearch: false });
    }

    function fallbackThreadOptions(payload) {
      if (!payload.source_reader_url) return [];
      return [{
        id: payload.source_reader_url,
        title: payload.source_host ? `${payload.source_host} reader` : "Source reader",
        reader_url: payload.source_reader_url,
        source_host: payload.source_host || "",
        threadmarks: payload.threadmarks || 0,
        words: payload.words || 0
      }];
    }

    function selectedThread() {
      return threadOptions.find(item => item.id === selectedThreadId) || threadOptions[0] || null;
    }

    function selectThread(threadId, options = {}) {
      const item = threadOptions.find(candidate => candidate.id === threadId);
      if (!item) return;
      selectedThreadId = item.id;
      activeThreadOptionId = item.id;
      threadPickerInput.value = item.title;
      threadPickerInput.dataset.selectedThreadId = item.id;
      updateStatsText();
      updateThreadSourceLink(item);
      closeThreadOptions();
      updateUrl();
      if (options.runSearch && query.value.trim()) runSearch();
    }

    function updateThreadSourceLink(item) {
      if (!item || !item.reader_url) {
        threadSourceLink.hidden = true;
        threadSourceLink.removeAttribute("href");
        return;
      }
      threadSourceLink.hidden = false;
      threadSourceLink.href = item.reader_url;
      threadSourceLink.textContent = "Source thread";
    }

    function updateStatsText() {
      const item = selectedThread();
      const source = item || statsPayload || {};
      const threadmarkCount = Number(source.threadmarks || 0);
      const wordCount = Number(source.words || 0);
      stats.textContent = `${threadmarkCount.toLocaleString()} threadmarks · ${wordCount.toLocaleString()} words`;
    }

    function openThreadOptions() {
      if (!threadOptions.length) return;
      renderThreadOptions();
      threadOptionsPanel.hidden = false;
      threadPickerInput.setAttribute("aria-expanded", "true");
    }

    function closeThreadOptions() {
      threadOptionsPanel.hidden = true;
      threadPickerInput.setAttribute("aria-expanded", "false");
      threadPickerInput.removeAttribute("aria-activedescendant");
    }

    function resetThreadPickerInput() {
      const item = selectedThread();
      threadPickerInput.value = item ? item.title : "";
    }

    function renderThreadOptions() {
      const matches = filteredThreadOptions();
      if (!matches.length) {
        activeThreadOptionId = "";
        threadPickerInput.removeAttribute("aria-activedescendant");
        threadOptionsPanel.innerHTML = '<div class="thread-option-empty">No matching threads</div>';
        return;
      }
      if (!matches.some(item => item.id === activeThreadOptionId)) {
        activeThreadOptionId = matches[0].id;
      }
      const active = matches.find(item => item.id === activeThreadOptionId);
      if (active) threadPickerInput.setAttribute("aria-activedescendant", threadOptionElementId(active));
      threadOptionsPanel.innerHTML = matches.map(renderThreadOption).join("");
    }

    function filteredThreadOptions() {
      const value = threadPickerInput.value.trim();
      return threadOptions
        .map((item, index) => ({ item, index, score: fuzzyThreadScore(item, value) }))
        .filter(entry => entry.score >= 0)
        .sort((left, right) => right.score - left.score || left.index - right.index)
        .map(entry => entry.item);
    }

    function fuzzyThreadScore(item, value) {
      const needle = value.toLocaleLowerCase();
      if (!needle) return 0;
      const haystack = `${item.title} ${item.source_host} ${item.reader_url}`.toLocaleLowerCase();
      const directIndex = haystack.indexOf(needle);
      if (directIndex >= 0) return 1000 - directIndex + needle.length;
      let score = 0;
      let lastIndex = -1;
      for (const char of needle) {
        const nextIndex = haystack.indexOf(char, lastIndex + 1);
        if (nextIndex < 0) return -1;
        score += nextIndex === lastIndex + 1 ? 6 : 1;
        lastIndex = nextIndex;
      }
      return score;
    }

    function renderThreadOption(item) {
      const id = threadOptionElementId(item);
      const isSelected = item.id === selectedThreadId;
      const isActive = item.id === activeThreadOptionId;
      const meta = [item.source_host, item.threadmarks ? `${Number(item.threadmarks).toLocaleString()} threadmarks` : ""].filter(Boolean).join(" · ");
      return `<button id="${escapeAttribute(id)}" class="thread-option${isActive ? " is-active" : ""}" type="button" role="option" data-thread-id="${escapeAttribute(item.id)}" aria-selected="${isSelected ? "true" : "false"}">
        <span class="thread-option-title">${escapeHtml(item.title)}</span>
        ${meta ? `<span class="thread-option-meta">${escapeHtml(meta)}</span>` : ""}
      </button>`;
    }

    function threadOptionElementId(item) {
      return `thread-option-${String(item.id).replace(/[^a-zA-Z0-9_-]/g, "-")}`;
    }

    function handleThreadPickerKeydown(event) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        moveActiveThreadOption(1);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        moveActiveThreadOption(-1);
        return;
      }
      if (event.key === "Enter") {
        if (threadOptionsPanel.hidden) return;
        event.preventDefault();
        const active = filteredThreadOptions().find(item => item.id === activeThreadOptionId);
        if (active) {
          selectThread(active.id, { runSearch: true });
        } else {
          resetThreadPickerInput();
          closeThreadOptions();
        }
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        resetThreadPickerInput();
        closeThreadOptions();
      }
    }

    function moveActiveThreadOption(offset) {
      const matches = filteredThreadOptions();
      if (!matches.length) {
        openThreadOptions();
        return;
      }
      const current = matches.findIndex(item => item.id === activeThreadOptionId);
      const next = current < 0 ? 0 : (current + offset + matches.length) % matches.length;
      activeThreadOptionId = matches[next].id;
      renderThreadOptions();
      openThreadOptions();
    }

    async function loadContents() {
      contents.innerHTML = '<div class="empty"></div>';
      const params = new URLSearchParams({ limit: "300" });
      addThreadParam(params);
      const response = await fetch(`/api/threadmarks?${params.toString()}`);
      const payload = await response.json();
      const items = payload.items || [];
      contentsLoaded = true;
      tocCount.textContent = `${items.length} threadmark${items.length === 1 ? "" : "s"}`;
      contents.innerHTML = items.map(renderTocRow).join("") || '<div class="empty"></div>';
    }

    function searchParams() {
      const params = new URLSearchParams({
        q: query.value.trim(),
        mode: allWords.checked ? "all" : "any"
      });
      addThreadParam(params);
      if (fromOrder.value) params.set("from", fromOrder.value);
      if (toOrder.value) params.set("to", toOrder.value);
      return params;
    }

    function addThreadParam(params) {
      if (selectedThreadId) params.set("thread", selectedThreadId);
    }

    function updateUrl() {
      const params = uiStateParams();
      const serialized = params.toString();
      window.history.replaceState(null, "", serialized ? `${window.location.pathname}?${serialized}` : window.location.pathname);
    }

    function uiStateParams() {
      const params = new URLSearchParams();
      if (selectedThreadId && threadOptions.length > 1) params.set("thread", selectedThreadId);
      if (query.value.trim()) {
        params.set("q", query.value.trim());
        if (!allWords.checked) params.set("mode", "any");
        if (fromOrder.value) params.set("from", fromOrder.value);
        if (toOrder.value) params.set("to", toOrder.value);
      }
      if (!contentsPanel.hidden) params.set("view", "contents");
      return params;
    }

    function rangeText() {
      if (fromOrder.value && toOrder.value) return `#${fromOrder.value}-${toOrder.value}`;
      if (fromOrder.value) return `from #${fromOrder.value}`;
      if (toOrder.value) return `through #${toOrder.value}`;
      return "";
    }

    function resultCountText(payload) {
      const hits = Number(payload.hit_count || payload.result_count || 0);
      const threadmarks = Number(payload.total_threadmarks || 0);
      if (!hits) return "0 hits";
      return `${hits.toLocaleString()} hit${hits === 1 ? "" : "s"} in ${threadmarks.toLocaleString()} threadmark${threadmarks === 1 ? "" : "s"}`;
    }

    function renderThreadmarkGroup(group) {
      const date = group.published_at ? new Date(group.published_at).toLocaleDateString() : "";
      const title = escapeHtml(group.title || "");
      const author = escapeHtml(group.author || "");
      const order = Number(group.threadmark_order || 0).toLocaleString();
      const hits = group.hits || [];
      const hitCount = Number(group.hit_count || hits.length || 0);
      return `<article>
        <h2><a href="${escapeAttribute(group.source_url)}" target="_blank" rel="noopener noreferrer">${title}</a></h2>
        <div class="meta">
          <span>#${order}</span>
          ${date ? `<span>${date}</span>` : ""}
          ${author ? `<span>${author}</span>` : ""}
          <span>${hitCount.toLocaleString()} hit${hitCount === 1 ? "" : "s"}</span>
        </div>
        <div class="hit-list">${hits.map(renderHit).join("")}</div>
        <div class="actions">
          ${privateFulltext && group.local_url ? `<a href="${escapeAttribute(group.local_url)}">Open local text</a>` : ""}
          <a href="${escapeAttribute(group.source_url)}" target="_blank" rel="noopener noreferrer">Open source</a>
        </div>
      </article>`;
    }

    function renderHit(hit, index) {
      const label = `Hit ${Number(index + 1).toLocaleString()}`;
      return `<div class="hit">
        <div class="hit-label">${label}</div>
        <p class="snippet">${hit.snippet_html || escapeHtml(hit.snippet || "")}</p>
      </div>`;
    }

    function renderTocRow(item) {
      const date = item.published_at ? new Date(item.published_at).toLocaleDateString() : "";
      const author = escapeHtml(item.author || "");
      const title = escapeHtml(item.title);
      const words = Number(item.word_count).toLocaleString();
      return `<div class="toc-row">
        <div class="toc-order">#${Number(item.threadmark_order).toLocaleString()}</div>
        <div class="toc-title">
          <a href="${escapeAttribute(item.source_url)}" target="_blank" rel="noopener noreferrer">${title}</a>
          <div class="toc-meta">${date ? `${date}` : ""}${date && author ? " · " : ""}${author}</div>
        </div>
        <div class="toc-words">${words} words</div>
      </div>`;
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[char]));
    }
    function escapeAttribute(value) {
      return escapeHtml(value).replace(/`/g, "&#96;");
    }
  </script>
</body>
</html>
"""


DETAIL_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>Thread Search Threadmark</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8f5;
      --panel: #ffffff;
      --ink: #1d2528;
      --muted: #667074;
      --line: #cfd8d1;
      --accent: #134e4a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.55;
    }
    main {
      width: min(860px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 56px;
    }
    nav {
      margin-bottom: 18px;
      font-size: 14px;
    }
    nav a, .source a {
      color: var(--accent);
      text-decoration: none;
      font-weight: 650;
    }
    nav a:hover, .source a:hover { text-decoration: underline; }
    article {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 20px;
    }
    h1 {
      margin: 0 0 8px;
      font-size: 24px;
      line-height: 1.25;
      letter-spacing: 0;
    }
    .meta, .source {
      color: var(--muted);
      font-size: 14px;
      margin-bottom: 16px;
    }
    pre {
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font: inherit;
    }
    .error {
      color: #8a1f11;
      font-weight: 650;
    }
  </style>
</head>
<body>
  <main>
    <nav><a href="/">Search</a></nav>
    <article id="detail">Loading...</article>
  </main>
  <script>
    const detail = document.querySelector("#detail");
    const postId = window.location.pathname.split("/").filter(Boolean).pop();
    load();

    async function load() {
      const response = await fetch(`/api/threadmark/${encodeURIComponent(postId)}`);
      if (!response.ok) {
        detail.innerHTML = '<p class="error">Threadmark text is unavailable.</p>';
        return;
      }
      const item = await response.json();
      const date = item.published_at ? new Date(item.published_at).toLocaleDateString() : "";
      detail.innerHTML = `<h1>${escapeHtml(item.title)}</h1>
        <div class="meta">#${Number(item.threadmark_order).toLocaleString()} · ${date} · ${escapeHtml(item.author || "")} · ${Number(item.word_count).toLocaleString()} words</div>
        <div class="source"><a href="${escapeAttribute(item.source_url)}" target="_blank" rel="noopener noreferrer">Open original post on SV</a></div>
        <pre>${escapeHtml(item.body || "")}</pre>`;
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[char]));
    }
    function escapeAttribute(value) {
      return escapeHtml(value).replace(/`/g, "&#96;");
    }
  </script>
</body>
</html>
"""
