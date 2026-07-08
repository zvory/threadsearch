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
    claim_check_report,
    concordance_db,
    list_threadmarks_db,
    query_explain_db,
    search_db,
    search_terms_db,
    search_totals_db,
    suggest_terms_db,
    term_index_db,
    threadmark_detail,
    topic_coverage,
    topic_comparison,
    topic_dossier,
    topic_recap,
    topic_report,
)


NON_HTML_CSP = "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
PUBLIC_SNIPPET_KEYS = {"snippet", "best_snippet", "topic_snippet", "claim_snippet"}
DEFAULT_PUBLIC_SNIPPET_BUDGET_CHARS = 6000
QUESTION_LEAD_WORDS = frozenset(
    {"is", "are", "was", "were", "do", "does", "did", "has", "have", "had", "can", "could", "will", "would", "should"}
)
CLAIM_FILLER_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "do",
        "does",
        "did",
        "has",
        "have",
        "had",
        "can",
        "could",
        "will",
        "would",
        "should",
        "not",
        "no",
        "never",
        "be",
        "been",
        "being",
        "turn",
        "turns",
        "turned",
        "become",
        "becomes",
        "became",
        "go",
        "goes",
        "went",
        "get",
        "gets",
        "got",
        "actually",
        "really",
        "ever",
    }
)
CLAIM_STRIP_CHARS = "\"'“”‘’()[]{}.,?!:;"


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
        if parsed.path == "/api/suggest":
            params = parse_qs(parsed.query)
            query = bounded_query(params.get("q", [""])[0], self.query_char_cap)
            limit = clamp(parse_int(params.get("limit", ["8"])[0], 8), 1, 12)
            suggestions = suggest_terms_db(self.database_path, query, limit=limit)
            self.respond_json(
                {"query": query, "suggestions": [asdict(item) for item in suggestions]},
                head_only=head_only,
            )
            return
        if parsed.path == "/api/terms":
            params = parse_qs(parsed.query)
            raw_prefix = params.get("prefix", params.get("q", [""]))[0]
            prefix = bounded_query(raw_prefix, min(self.query_char_cap, 60))
            limit = clamp(parse_int(params.get("limit", ["50"])[0], 50), 1, 100)
            min_chunk_count = clamp(parse_int(params.get("min_chunks", ["1"])[0], 1), 1, 100000)
            include_stopwords = query_flag(params, "include_stopwords")
            report = term_index_db(
                self.database_path,
                prefix=prefix,
                limit=limit,
                min_chunk_count=min_chunk_count,
                include_stopwords=include_stopwords,
            )
            self.respond_json(asdict(report), head_only=head_only)
            return
        if parsed.path == "/api/explain":
            params = parse_qs(parsed.query)
            query = bounded_query(params.get("q", [""])[0], self.query_char_cap)
            prefix_variants = query_flag(params, "prefix_variants")
            mode = params.get("mode", ["all"])[0]
            if mode not in {"all", "any"}:
                mode = "all"
            order_min = parse_optional_int(params.get("from", [""])[0])
            order_max = parse_optional_int(params.get("to", [""])[0])
            term_limit = clamp(parse_int(params.get("term_limit", ["12"])[0], 12), 0, 25)
            report = query_explain_db(
                self.database_path,
                query,
                mode=mode,
                order_min=order_min,
                order_max=order_max,
                prefix_variants=prefix_variants,
                term_limit=term_limit,
            )
            self.respond_json(asdict(report), head_only=head_only)
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
            prefix_variants = query_flag(params, "prefix_variants")
            aliases = tuple(
                bounded_query(item, self.query_char_cap)
                for item in params.get("alias", [])[:8]
                if bounded_query(item, self.query_char_cap).strip()
            )
            limit = clamp(parse_int(params.get("limit", ["20"])[0], 20), 1, self.search_limit_cap)
            mode = params.get("mode", ["all"])[0]
            if mode not in {"all", "any"}:
                mode = "all"
            sort = params.get("sort", ["relevance"])[0]
            if sort not in {"relevance", "timeline"}:
                sort = "relevance"
            order_min = parse_optional_int(params.get("from", [""])[0])
            order_max = parse_optional_int(params.get("to", [""])[0])
            grouped = True
            if self.allow_chunk_results:
                grouped = params.get("grouped", ["1"])[0] != "0"
            results = []
            totals = None
            terms = []
            if query.strip():
                totals = search_totals_db(
                    self.database_path,
                    query,
                    aliases=aliases,
                    mode=mode,
                    order_min=order_min,
                    order_max=order_max,
                    prefix_variants=prefix_variants,
                )
                terms = search_terms_db(
                    self.database_path,
                    query,
                    aliases=aliases,
                    mode=mode,
                    order_min=order_min,
                    order_max=order_max,
                    prefix_variants=prefix_variants,
                )
                for result in search_db(
                    self.database_path,
                    query,
                    aliases=aliases,
                    limit=limit,
                    mode=mode,
                    order_min=order_min,
                    order_max=order_max,
                    grouped=grouped,
                    sort=sort,
                    prefix_variants=prefix_variants,
                ):
                    item = asdict(result)
                    item["local_url"] = f"/threadmark/{result.post_id}" if self.allow_private_fulltext else None
                    results.append(item)
            payload = apply_public_snippet_budget(
                {
                    "query": query,
                    "aliases": [term.query for term in terms[1:]],
                    "terms": [asdict(term) for term in terms],
                    "sort": sort,
                    "prefix_variants": prefix_variants,
                    "match_kind": totals.match_kind if totals else "none",
                    "match_query": totals.match_query if totals else "",
                    "result_count": len(results),
                    "total_threadmarks": totals.total_threadmarks if totals else 0,
                    "total_chunks": totals.total_chunks if totals else 0,
                    "results": results,
                },
                self.snippet_budget_char_cap,
            )
            for item in payload["results"]:
                item["snippet_html"] = snippet_html(item["snippet"])
            self.respond_json(payload, head_only=head_only)
            return
        if parsed.path == "/api/mentions":
            params = parse_qs(parsed.query)
            query = bounded_query(params.get("q", [""])[0], self.query_char_cap)
            prefix_variants = query_flag(params, "prefix_variants")
            aliases = tuple(
                bounded_query(item, self.query_char_cap)
                for item in params.get("alias", [])[:8]
                if bounded_query(item, self.query_char_cap).strip()
            )
            limit = clamp(parse_int(params.get("limit", ["25"])[0], 25), 1, self.mention_limit_cap)
            mode = params.get("mode", ["all"])[0]
            if mode not in {"all", "any"}:
                mode = "all"
            sort = params.get("sort", ["relevance"])[0]
            if sort not in {"relevance", "timeline"}:
                sort = "relevance"
            order_min = parse_optional_int(params.get("from", [""])[0])
            order_max = parse_optional_int(params.get("to", [""])[0])
            report = concordance_db(
                self.database_path,
                query,
                aliases=aliases,
                limit=limit,
                mode=mode,
                order_min=order_min,
                order_max=order_max,
                window_chars=self.mention_window_char_cap,
                sort=sort,
                prefix_variants=prefix_variants,
            )
            payload = asdict(report)
            payload["prefix_variants"] = prefix_variants
            apply_public_snippet_budget(payload, self.snippet_budget_char_cap)
            for mention in payload["mentions"]:
                mention["snippet_html"] = snippet_html(mention["snippet"])
            self.respond_json(payload, head_only=head_only)
            return
        if parsed.path == "/api/dossier":
            params = parse_qs(parsed.query)
            query = bounded_query(params.get("q", [""])[0], self.query_char_cap)
            prefix_variants = query_flag(params, "prefix_variants")
            aliases = tuple(
                bounded_query(item, self.query_char_cap)
                for item in params.get("alias", [])[:8]
                if bounded_query(item, self.query_char_cap).strip()
            )
            threadmark_limit = clamp(
                parse_int(params.get("threadmark_limit", params.get("limit", ["50"]))[0], 50),
                1,
                self.report_limit_cap,
            )
            mention_limit = clamp(
                parse_int(params.get("mention_limit", ["50"])[0], 50),
                1,
                self.mention_limit_cap,
            )
            mode = params.get("mode", ["all"])[0]
            if mode not in {"all", "any"}:
                mode = "all"
            sort = params.get("sort", ["timeline"])[0]
            if sort not in {"coverage", "timeline"}:
                sort = "timeline"
            order_min = parse_optional_int(params.get("from", [""])[0])
            order_max = parse_optional_int(params.get("to", [""])[0])
            dossier = topic_dossier(
                self.database_path,
                query,
                aliases=aliases,
                threadmark_limit=threadmark_limit,
                mention_limit=mention_limit,
                mode=mode,
                order_min=order_min,
                order_max=order_max,
                window_chars=self.mention_window_char_cap,
                sort=sort,
                prefix_variants=prefix_variants,
            )
            payload = asdict(dossier)
            payload["prefix_variants"] = prefix_variants
            apply_public_snippet_budget(payload, self.snippet_budget_char_cap)
            for item in payload["timeline"]:
                item["snippet_html"] = snippet_html(item["snippet"])
            for item in payload["threadmarks"]:
                item["snippet_html"] = snippet_html(item["best_snippet"])
            for item in payload["mention_windows"]:
                item["snippet_html"] = snippet_html(item["snippet"])
            self.respond_json(payload, head_only=head_only)
            return
        if parsed.path == "/api/evidence-pack":
            params = parse_qs(parsed.query)
            query = bounded_query(params.get("q", [""])[0], self.query_char_cap)
            prefix_variants = query_flag(params, "prefix_variants")
            aliases = tuple(
                bounded_query(item, self.query_char_cap)
                for item in params.get("alias", [])[:8]
                if bounded_query(item, self.query_char_cap).strip()
            )
            claim_queries = [
                bounded_query(item, self.query_char_cap)
                for item in params.get("claim", [])
                if bounded_query(item, self.query_char_cap).strip()
            ][:8]
            inferred_from_query = False
            original_query = query
            if not claim_queries:
                candidate = question_claim_query_candidate(query)
                if candidate is not None:
                    query, claim_query = candidate
                    claim_queries = [claim_query]
                    inferred_from_query = True
            threadmark_limit = clamp(
                parse_int(params.get("threadmark_limit", params.get("limit", ["50"]))[0], 50),
                1,
                self.report_limit_cap,
            )
            mention_limit = clamp(
                parse_int(params.get("mention_limit", ["50"])[0], 50),
                1,
                self.mention_limit_cap,
            )
            claim_limit = clamp(parse_int(params.get("claim_limit", ["10"])[0], 10), 1, self.mention_limit_cap)
            mode = params.get("mode", ["all"])[0]
            if mode not in {"all", "any"}:
                mode = "all"
            sort = params.get("sort", ["timeline"])[0]
            if sort not in {"coverage", "timeline"}:
                sort = "timeline"
            order_min = parse_optional_int(params.get("from", [""])[0])
            order_max = parse_optional_int(params.get("to", [""])[0])
            dossier = topic_dossier(
                self.database_path,
                query,
                aliases=aliases,
                threadmark_limit=threadmark_limit,
                mention_limit=mention_limit,
                mode=mode,
                order_min=order_min,
                order_max=order_max,
                window_chars=self.mention_window_char_cap,
                sort=sort,
                prefix_variants=prefix_variants,
            )
            claims = [
                claim_check_report(
                    self.database_path,
                    query,
                    claim_query,
                    topic_aliases=aliases,
                    limit=claim_limit,
                    mode=mode,
                    order_min=order_min,
                    order_max=order_max,
                    prefix_variants=prefix_variants,
                )
                for claim_query in claim_queries
            ]
            payload = {
                "kind": "thread-search-evidence-pack",
                "query": query,
                "bounded_retrieval_only": True,
                "prefix_variants": prefix_variants,
                "dossier": asdict(dossier),
                "claims": [asdict(claim) for claim in claims],
            }
            if inferred_from_query:
                payload["claim_inferred_from_query"] = True
                payload["original_query"] = original_query
            apply_public_snippet_budget(payload, self.snippet_budget_char_cap)
            for item in payload["dossier"]["timeline"]:
                item["snippet_html"] = snippet_html(item["snippet"])
            for item in payload["dossier"]["threadmarks"]:
                item["snippet_html"] = snippet_html(item["best_snippet"])
            for item in payload["dossier"]["mention_windows"]:
                item["snippet_html"] = snippet_html(item["snippet"])
            for claim in payload["claims"]:
                for item in claim["evidence"]:
                    item["topic_snippet_html"] = snippet_html(item["topic_snippet"])
                    item["claim_snippet_html"] = snippet_html(item["claim_snippet"])
            self.respond_json(payload, head_only=head_only)
            return
        if parsed.path == "/api/recap":
            params = parse_qs(parsed.query)
            query = bounded_query(params.get("q", [""])[0], self.query_char_cap)
            prefix_variants = query_flag(params, "prefix_variants")
            aliases = tuple(
                bounded_query(item, self.query_char_cap)
                for item in params.get("alias", [])[:8]
                if bounded_query(item, self.query_char_cap).strip()
            )
            claim_queries = [
                bounded_query(item, self.query_char_cap)
                for item in params.get("claim", [])
                if bounded_query(item, self.query_char_cap).strip()
            ][:8]
            inferred_from_query = False
            original_query = query
            if not claim_queries:
                candidate = question_claim_query_candidate(query)
                if candidate is not None:
                    query, claim_query = candidate
                    claim_queries = [claim_query]
                    inferred_from_query = True
            timeline_limit = clamp(
                parse_int(params.get("timeline_limit", params.get("limit", ["25"]))[0], 25),
                1,
                self.report_limit_cap,
            )
            mention_limit = clamp(
                parse_int(params.get("mention_limit", ["25"])[0], 25),
                1,
                self.mention_limit_cap,
            )
            claim_limit = clamp(parse_int(params.get("claim_limit", ["10"])[0], 10), 1, self.mention_limit_cap)
            mode = params.get("mode", ["all"])[0]
            if mode not in {"all", "any"}:
                mode = "all"
            order_min = parse_optional_int(params.get("from", [""])[0])
            order_max = parse_optional_int(params.get("to", [""])[0])
            recap = topic_recap(
                self.database_path,
                query,
                aliases=aliases,
                claim_queries=tuple(claim_queries),
                timeline_limit=timeline_limit,
                mention_limit=mention_limit,
                claim_limit=claim_limit,
                mode=mode,
                order_min=order_min,
                order_max=order_max,
                window_chars=self.mention_window_char_cap,
                prefix_variants=prefix_variants,
            )
            payload = asdict(recap)
            payload["prefix_variants"] = prefix_variants
            if inferred_from_query:
                payload["claim_inferred_from_query"] = True
                payload["original_query"] = original_query
            apply_public_snippet_budget(payload, self.snippet_budget_char_cap)
            for item in payload["timeline"]:
                item["snippet_html"] = snippet_html(item["snippet"])
            for item in payload["mention_windows"]:
                item["snippet_html"] = snippet_html(item["snippet"])
            for claim in payload["claims"]:
                for item in claim["evidence"]:
                    item["topic_snippet_html"] = snippet_html(item["topic_snippet"])
                    item["claim_snippet_html"] = snippet_html(item["claim_snippet"])
            self.respond_json(payload, head_only=head_only)
            return
        if parsed.path == "/api/coverage":
            params = parse_qs(parsed.query)
            query = bounded_query(params.get("q", [""])[0], self.query_char_cap)
            prefix_variants = query_flag(params, "prefix_variants")
            aliases = tuple(
                bounded_query(item, self.query_char_cap)
                for item in params.get("alias", [])[:8]
                if bounded_query(item, self.query_char_cap).strip()
            )
            limit = clamp(parse_int(params.get("limit", ["300"])[0], 300), 1, self.threadmark_limit_cap)
            bucket_size = clamp(parse_int(params.get("bucket_size", ["25"])[0], 25), 1, 100)
            mode = params.get("mode", ["all"])[0]
            if mode not in {"all", "any"}:
                mode = "all"
            sort = params.get("sort", ["timeline"])[0]
            if sort not in {"coverage", "timeline"}:
                sort = "timeline"
            order_min = parse_optional_int(params.get("from", [""])[0])
            order_max = parse_optional_int(params.get("to", [""])[0])
            coverage = topic_coverage(
                self.database_path,
                query,
                aliases=aliases,
                limit=limit,
                mode=mode,
                order_min=order_min,
                order_max=order_max,
                sort=sort,
                bucket_size=bucket_size,
                prefix_variants=prefix_variants,
            )
            items = [public_coverage_item(item) for item in coverage.items]
            self.respond_json(
                {
                    "query": coverage.query,
                    "aliases": coverage.aliases,
                    "terms": [asdict(term) for term in coverage.terms],
                    "total_threadmarks": coverage.total_threadmarks,
                    "total_chunks": coverage.total_chunks,
                    "match_kind": coverage.match_kind,
                    "match_query": coverage.match_query,
                    "prefix_variants": prefix_variants,
                    "bucket_size": bucket_size,
                    "buckets": [asdict(bucket) for bucket in coverage.buckets],
                    "items": items,
                },
                head_only=head_only,
            )
            return
        if parsed.path == "/api/compare":
            params = parse_qs(parsed.query)
            raw_queries = [*params.get("q", []), *params.get("topic", [])]
            queries = tuple(
                bounded_query(item, self.query_char_cap)
                for item in raw_queries[:8]
                if bounded_query(item, self.query_char_cap).strip()
            )
            if len(queries) < 2:
                self.respond_json(
                    {
                        "ok": False,
                        "error": "compare requires at least two q= or topic= parameters",
                        "queries": list(queries),
                    },
                    status=400,
                    head_only=head_only,
                )
                return
            prefix_variants = query_flag(params, "prefix_variants")
            overlap_limit = clamp(parse_int(params.get("overlap_limit", ["100"])[0], 100), 1, self.threadmark_limit_cap)
            bucket_size = clamp(parse_int(params.get("bucket_size", ["25"])[0], 25), 1, 100)
            mode = params.get("mode", ["all"])[0]
            if mode not in {"all", "any"}:
                mode = "all"
            order_min = parse_optional_int(params.get("from", [""])[0])
            order_max = parse_optional_int(params.get("to", [""])[0])
            comparison = topic_comparison(
                self.database_path,
                queries,
                mode=mode,
                order_min=order_min,
                order_max=order_max,
                overlap_limit=overlap_limit,
                bucket_size=bucket_size,
                prefix_variants=prefix_variants,
            )
            payload = {
                "ok": True,
                "kind": comparison.kind,
                "metadata_only": comparison.metadata_only,
                "queries": comparison.queries,
                "mode": comparison.mode,
                "prefix_variants": comparison.prefix_variants,
                "bucket_size": comparison.bucket_size,
                "topics": [
                    {
                        "query": topic.query,
                        "total_threadmarks": topic.total_threadmarks,
                        "total_chunks": topic.total_chunks,
                        "match_kind": topic.match_kind,
                        "match_query": topic.match_query,
                        "first_threadmark": public_coverage_item(topic.first_threadmark),
                        "last_threadmark": public_coverage_item(topic.last_threadmark),
                        "buckets": [asdict(bucket) for bucket in topic.buckets],
                    }
                    for topic in comparison.topics
                ],
                "all_overlap": public_compare_overlap(comparison.all_overlap),
                "pairwise_overlaps": [
                    public_compare_overlap(overlap)
                    for overlap in comparison.pairwise_overlaps
                ],
            }
            self.respond_json(payload, head_only=head_only)
            return
        if parsed.path == "/api/claim":
            params = parse_qs(parsed.query)
            query = bounded_query(params.get("q", [""])[0], self.query_char_cap)
            prefix_variants = query_flag(params, "prefix_variants")
            aliases = tuple(
                bounded_query(item, self.query_char_cap)
                for item in params.get("alias", [])[:8]
                if bounded_query(item, self.query_char_cap)
            )
            claim = bounded_query(params.get("claim", [""])[0], self.query_char_cap)
            inferred_from_query = False
            original_query = query
            if not claim.strip():
                candidate = claim_query_candidate(query)
                if candidate is not None:
                    query, claim = candidate
                    inferred_from_query = True
            limit = clamp(parse_int(params.get("limit", ["25"])[0], 25), 1, self.mention_limit_cap)
            mode = params.get("mode", ["all"])[0]
            if mode not in {"all", "any"}:
                mode = "all"
            order_min = parse_optional_int(params.get("from", [""])[0])
            order_max = parse_optional_int(params.get("to", [""])[0])
            report = claim_check_report(
                self.database_path,
                query,
                claim,
                topic_aliases=aliases,
                limit=limit,
                mode=mode,
                order_min=order_min,
                order_max=order_max,
                prefix_variants=prefix_variants,
            )
            payload = asdict(report)
            payload["prefix_variants"] = prefix_variants
            if inferred_from_query:
                payload["claim_inferred_from_query"] = True
                payload["original_query"] = original_query
            apply_public_snippet_budget(payload, self.snippet_budget_char_cap)
            for item in payload["evidence"]:
                item["topic_snippet_html"] = snippet_html(item["topic_snippet"])
                item["claim_snippet_html"] = snippet_html(item["claim_snippet"])
            self.respond_json(payload, head_only=head_only)
            return
        if parsed.path == "/api/report":
            params = parse_qs(parsed.query)
            query = bounded_query(params.get("q", [""])[0], self.query_char_cap)
            prefix_variants = query_flag(params, "prefix_variants")
            aliases = tuple(
                bounded_query(item, self.query_char_cap)
                for item in params.get("alias", [])[:8]
                if bounded_query(item, self.query_char_cap).strip()
            )
            limit = clamp(parse_int(params.get("limit", ["50"])[0], 50), 1, self.report_limit_cap)
            mode = params.get("mode", ["all"])[0]
            if mode not in {"all", "any"}:
                mode = "all"
            sort = params.get("sort", ["coverage"])[0]
            if sort not in {"coverage", "timeline"}:
                sort = "coverage"
            order_min = parse_optional_int(params.get("from", [""])[0])
            order_max = parse_optional_int(params.get("to", [""])[0])
            report = topic_report(
                self.database_path,
                query,
                aliases=aliases,
                limit=limit,
                mode=mode,
                order_min=order_min,
                order_max=order_max,
                sort=sort,
                prefix_variants=prefix_variants,
            )
            payload = asdict(report)
            payload["prefix_variants"] = prefix_variants
            apply_public_snippet_budget(payload, self.snippet_budget_char_cap)
            for mention in payload["mentions"]:
                mention["snippet_html"] = snippet_html(mention["best_snippet"])
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
            "/api/report",
            "/api/mentions",
            "/api/dossier",
            "/api/evidence-pack",
            "/api/recap",
            "/api/coverage",
            "/api/compare",
            "/api/threadmarks",
            "/api/terms",
            "/api/explain",
            "/api/suggest",
            "/api/claim",
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


def public_coverage_item(item):
    if item is None:
        return None
    return {
        "title": item.title,
        "post_id": item.post_id,
        "threadmark_order": item.threadmark_order,
        "author": item.author,
        "published_at": item.published_at,
        "source_url": item.source_url,
        "hit_count": item.hit_count,
    }


def public_compare_overlap(overlap):
    return {
        "queries": overlap.queries,
        "total_threadmarks": overlap.total_threadmarks,
        "items": [public_coverage_item(item) for item in overlap.items],
    }


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
    return {
        "ok": True,
        "source_reader_url": source_reader_url,
        "source_host": urlparse(source_reader_url).netloc,
        "public_access_mode": "snippets_and_source_links",
        "public_notice": "Public results are bounded snippets with source links; full text remains on Sufficient Velocity.",
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


def query_flag(params: dict[str, list[str]], name: str) -> bool:
    values = params.get(name, [])
    if not values:
        return False
    return values[-1].strip().casefold() in {"1", "true", "yes", "on"}


def claim_query_candidate(value: str) -> tuple[str, str] | None:
    parts = [clean_claim_token(part) for part in value.split()]
    parts = [part for part in parts if part]
    if len(parts) < 2:
        return None

    topic = parts[0]
    claim_parts = parts[1:]
    if len(parts) >= 3 and parts[0].casefold() in QUESTION_LEAD_WORDS:
        topic = parts[1]
        claim_parts = parts[2:]

    topic = clean_topic_token(topic)
    claim = clean_claim_phrase(claim_parts)
    if not topic or not claim:
        return None
    return topic, claim


def question_claim_query_candidate(value: str) -> tuple[str, str] | None:
    parts = [clean_claim_token(part) for part in value.split()]
    parts = [part for part in parts if part]
    if len(parts) < 2:
        return None

    first = parts[0]
    first_lower = first.casefold()
    first_is_possessive = first_lower.endswith("'s") or first_lower.endswith("’s")
    if first_lower not in QUESTION_LEAD_WORDS and not first_is_possessive:
        return None
    return claim_query_candidate(value)


def clean_claim_token(token: str) -> str:
    return token.strip().strip(CLAIM_STRIP_CHARS)


def clean_topic_token(token: str) -> str:
    cleaned = clean_claim_token(token)
    lowered = cleaned.casefold()
    if lowered.endswith("'s") or lowered.endswith("’s"):
        return cleaned[:-2]
    return cleaned


def clean_claim_phrase(parts: list[str]) -> str:
    filtered = []
    for part in parts:
        cleaned = clean_claim_token(part)
        if cleaned and cleaned.casefold() not in CLAIM_FILLER_WORDS:
            filtered.append(cleaned)
    return " ".join(filtered).strip()


def apply_public_snippet_budget(payload: dict[str, object], budget_chars: int) -> dict[str, object]:
    remaining = max(0, budget_chars)
    truncated = False

    def walk(value: object) -> None:
        nonlocal remaining, truncated
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        for key, item in list(value.items()):
            if key in PUBLIC_SNIPPET_KEYS and isinstance(item, str):
                if len(item) <= remaining:
                    remaining -= len(item)
                    continue
                value[key] = trim_marked_snippet(item, remaining)
                remaining = 0
                truncated = True
            else:
                walk(item)

    walk(payload)
    payload["snippet_budget_chars"] = budget_chars
    payload["snippet_chars_used"] = max(0, budget_chars) - remaining
    payload["snippets_truncated"] = truncated
    return payload


def trim_marked_snippet(snippet: str, max_chars: int) -> str:
    suffix = " [...]"
    if max_chars <= len(suffix):
        return ""
    if len(snippet) <= max_chars:
        return snippet
    available = max_chars - len(suffix)
    trimmed = snippet[:available].rstrip()
    if trimmed.count("\x01") > trimmed.count("\x02"):
        if len(trimmed) + len(suffix) + 1 > max_chars:
            trimmed = trimmed[: max(0, available - 1)].rstrip()
        trimmed += "\x02"
    return f"{trimmed}{suffix}"


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
      width: min(1120px, calc(100vw - 32px));
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
      gap: 3px;
    }
    .source-link {
      color: var(--accent-strong);
      font-size: 13px;
      font-weight: 650;
      text-decoration: none;
      width: fit-content;
    }
    .source-link:hover { text-decoration: underline; }
    .count {
      color: var(--muted);
      font-size: 14px;
      min-height: 20px;
      text-align: right;
    }
    form {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) minmax(160px, 0.65fr) 108px 108px auto auto;
      gap: 10px;
      margin-bottom: 18px;
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
    .tab-panel[hidden] {
      display: none;
    }
    .options {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 14px;
      margin: -4px 0 16px;
      color: var(--muted);
      font-size: 13px;
    }
    .suggestions,
    .query-tools {
      display: none;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      margin: -6px 0 14px;
      color: var(--muted);
      font-size: 13px;
    }
    .query-explain {
      flex: 1 1 100%;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--text);
      line-height: 1.45;
    }
    .query-explain span {
      color: var(--muted);
    }
    .query-explain .query-caution {
      display: block;
      margin-top: 3px;
      color: #7a4b09;
    }
    .suggestions button,
    .query-tools button {
      height: 28px;
      border-radius: 999px;
      padding: 0 10px;
      background: var(--panel);
      color: var(--accent-strong);
      border-color: var(--line);
      font-size: 13px;
      font-weight: 650;
    }
    .suggestions button:hover,
    .query-tools button:hover {
      background: #edf7f4;
      border-color: var(--accent);
    }
    .coverage button {
      height: 28px;
      border-radius: 999px;
      padding: 0 10px;
      background: var(--panel);
      color: var(--accent-strong);
      border: 1px solid var(--line);
      font-size: 13px;
      font-weight: 650;
    }
    .coverage button:hover {
      background: #edf7f4;
      border-color: var(--accent);
    }
    .dossier,
    .recap {
      display: none;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,0.72);
      padding: 12px 14px;
      margin-bottom: 14px;
    }
    .dossier-header,
    .recap-header {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px 14px;
      margin-bottom: 10px;
    }
    .dossier h2,
    .recap h2 {
      margin: 0;
      font-size: 16px;
      line-height: 1.3;
      letter-spacing: 0;
    }
    .dossier-summary,
    .recap-summary {
      color: var(--muted);
      font-size: 13px;
    }
    .dossier-grid,
    .recap-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .dossier-section,
    .recap-section {
      min-width: 0;
    }
    .dossier-section h3,
    .recap-section h3 {
      margin: 0 0 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
      letter-spacing: 0;
      text-transform: uppercase;
    }
    .term-list {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .term-list button {
      min-height: 28px;
      height: auto;
      border-radius: 999px;
      padding: 4px 10px;
      background: var(--panel);
      color: var(--accent-strong);
      border: 1px solid var(--line);
      font-size: 13px;
      font-weight: 650;
    }
    .term-list button:hover {
      background: #edf7f4;
      border-color: var(--accent);
    }
    .dossier-list,
    .recap-list {
      display: grid;
      gap: 8px;
    }
    .dossier-item,
    .recap-item {
      border-top: 1px solid var(--line);
      padding-top: 8px;
    }
    .dossier-item:first-child,
    .recap-item:first-child {
      border-top: 0;
      padding-top: 0;
    }
    .dossier-item p,
    .recap-item p {
      margin: 4px 0 0;
      overflow-wrap: anywhere;
    }
    .coverage-list {
      display: none;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,0.62);
      padding: 12px 14px;
      margin-bottom: 14px;
    }
    .coverage-list h2 {
      margin: 0 0 8px;
      font-size: 16px;
      line-height: 1.3;
      letter-spacing: 0;
    }
    .coverage-buckets {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin: 8px 0 12px;
    }
    .coverage-bucket {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      padding: 4px 8px;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
      width: auto;
      height: auto;
      cursor: pointer;
    }
    .coverage-bucket:hover {
      border-color: rgba(60, 87, 163, 0.55);
      color: var(--accent-strong);
    }
    .coverage-bucket strong {
      color: var(--ink);
      font-weight: 700;
    }
    .coverage-items {
      display: grid;
      gap: 6px;
    }
    .coverage-row {
      display: grid;
      grid-template-columns: 64px minmax(0, 1fr) auto;
      align-items: start;
      gap: 10px;
      border-top: 1px solid var(--line);
      padding-top: 7px;
    }
    .coverage-row:first-child {
      border-top: 0;
      padding-top: 0;
    }
    .coverage-row a {
      color: var(--accent-strong);
      font-weight: 650;
      text-decoration: none;
      overflow-wrap: anywhere;
    }
    .coverage-row a:hover { text-decoration: underline; }
    .coverage-hit-count {
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }
    .claim-evidence-item {
      border-top: 1px solid var(--line);
      padding-top: 10px;
      margin-top: 10px;
    }
    .claim-evidence-label {
      color: var(--muted);
      font-weight: 650;
      margin: 8px 0 4px;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    .options label {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .options input {
      width: 16px;
      height: 16px;
      margin: 0;
    }
    .alias-control {
      flex-wrap: wrap;
    }
    .options .alias-input {
      width: min(280px, 70vw);
      height: 32px;
      padding: 0 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--ink);
      font-size: 13px;
    }
    .json-link {
      display: none;
      color: var(--accent-strong);
      font-weight: 650;
      text-decoration: none;
    }
    .json-link:hover { text-decoration: underline; }
    .share-link {
      display: none;
      width: auto;
      height: auto;
      padding: 0;
      border: 0;
      background: transparent;
      color: var(--accent-strong);
      font-size: 13px;
      font-weight: 650;
    }
    .share-link:hover {
      background: transparent;
      text-decoration: underline;
    }
    .share-status {
      min-width: 44px;
      color: var(--muted);
      font-size: 13px;
    }
    input, select, button {
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
    select {
      padding: 0 10px;
      background: var(--panel);
      color: var(--ink);
    }
    input[type="number"] {
      appearance: textfield;
    }
    button {
      padding: 0 16px;
      background: var(--accent);
      color: white;
      border-color: var(--accent);
      font-weight: 650;
      cursor: pointer;
    }
    button:hover { background: var(--accent-strong); }
    .results {
      display: grid;
      gap: 10px;
    }
    .contents {
      display: grid;
      gap: 8px;
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
    .toc-title {
      min-width: 0;
    }
    .toc-title a {
      color: var(--accent-strong);
      text-decoration: none;
      font-weight: 650;
      overflow-wrap: anywhere;
    }
    .toc-title a:hover { text-decoration: underline; }
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
    .status {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      color: var(--muted);
      font-size: 13px;
      margin: -6px 0 14px;
      min-height: 20px;
    }
    .range-state {
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }
    .clear-range {
      display: none;
      width: auto;
      height: 28px;
      padding: 0 8px;
      border-radius: 6px;
      font-size: 12px;
      color: var(--accent-strong);
      background: var(--panel);
      cursor: pointer;
    }
    .clear-range:hover {
      border-color: rgba(60, 87, 163, 0.55);
    }
    .notice {
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      padding: 9px 0;
      margin: -4px 0 14px;
      color: var(--muted);
      font-size: 13px;
    }
    .notice a {
      color: var(--accent-strong);
      font-weight: 650;
      text-decoration: none;
    }
    .notice a:hover { text-decoration: underline; }
    .coverage {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,0.65);
      padding: 12px 14px;
      margin-bottom: 14px;
      color: var(--muted);
      font-size: 14px;
      display: none;
    }
    .coverage strong {
      color: var(--ink);
    }
    .match-note {
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
    }
    .mentions {
      display: none;
      margin: 0 0 16px;
    }
    .mentions h2 {
      margin: 0 0 8px;
      font-size: 15px;
      letter-spacing: 0;
    }
    .mention-list {
      display: grid;
      gap: 8px;
    }
    .mention {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,0.72);
      padding: 10px 12px;
    }
    .mention p {
      margin: 4px 0 0;
      overflow-wrap: anywhere;
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
    article h2 a {
      color: var(--accent-strong);
      text-decoration: none;
    }
    article h2 a:hover { text-decoration: underline; }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 12px;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
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
    .actions a {
      color: var(--accent-strong);
      text-decoration: none;
      font-weight: 650;
    }
    .actions a:hover { text-decoration: underline; }
    mark {
      background: var(--mark);
      color: inherit;
      padding: 0 2px;
      border-radius: 3px;
    }
    .empty {
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 18px;
      color: var(--muted);
      background: rgba(255,255,255,0.55);
    }
    @media (max-width: 700px) {
      main { width: min(100vw - 20px, 1120px); padding-top: 16px; }
      header { align-items: start; flex-direction: column; }
      .count { text-align: left; }
      form { grid-template-columns: 1fr; }
      input, select, button { width: 100%; }
      .share-link { width: auto; }
      .tabs { overflow-x: auto; }
      .toc-row { grid-template-columns: 52px minmax(0, 1fr); }
      .toc-words { grid-column: 2; text-align: left; }
      .dossier-grid, .recap-grid { grid-template-columns: 1fr; }
      .coverage-row { grid-template-columns: 52px minmax(0, 1fr); }
      .coverage-hit-count { grid-column: 2; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div class="title-block">
        <h1>Thread Search</h1>
        <a id="source-link" class="source-link" href="#" target="_blank" rel="noopener noreferrer">Source reader</a>
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
        <select id="mode" name="mode" aria-label="Match mode">
          <option value="all">All words</option>
          <option value="any">Any word</option>
        </select>
        <button type="submit">Search</button>
      </form>
      <div class="options">
        <label><input id="grouped" type="checkbox" checked> One hit per threadmark</label>
        <label><input id="prefix-variants" type="checkbox"> Word variants</label>
        <label>Topic order
          <select id="topic-sort" name="sort" aria-label="Topic order">
            <option value="relevance">Relevance</option>
            <option value="timeline">Timeline</option>
          </select>
        </label>
        <label class="alias-control">Topic aliases
        <input id="dossier-aliases" class="alias-input" name="alias" type="search" autocomplete="off" placeholder="Castro, Batista">
        </label>
        <a id="terms-link" class="json-link" href="/api/terms" target="_blank" rel="noopener noreferrer">Terms JSON</a>
        <a id="explain-link" class="json-link" href="/api/explain" target="_blank" rel="noopener noreferrer">Explain JSON</a>
        <a id="recap-link" class="json-link" href="/api/recap" target="_blank" rel="noopener noreferrer">Recap JSON</a>
        <a id="report-link" class="json-link" href="/api/report" target="_blank" rel="noopener noreferrer">Report JSON</a>
        <a id="dossier-link" class="json-link" href="/api/dossier" target="_blank" rel="noopener noreferrer">Dossier JSON</a>
        <a id="evidence-pack-link" class="json-link" href="/api/evidence-pack" target="_blank" rel="noopener noreferrer">Evidence Pack JSON</a>
        <a id="mentions-link" class="json-link" href="/api/mentions" target="_blank" rel="noopener noreferrer">Mentions JSON</a>
        <a id="coverage-link" class="json-link" href="/api/coverage" target="_blank" rel="noopener noreferrer">Coverage JSON</a>
        <button id="share-link" class="share-link" type="button">Copy link</button>
        <span id="share-status" class="share-status" aria-live="polite"></span>
      </div>
      <div id="suggestions" class="suggestions"></div>
      <div id="query-tools" class="query-tools"></div>
      <div class="status">
        <span id="stats"></span>
        <span class="range-state"><span id="range"></span><button id="clear-range" class="clear-range" type="button">Clear range</button></span>
      </div>
      <div id="public-notice" class="notice"></div>
      <div id="coverage" class="coverage"></div>
      <section id="recap" class="recap"></section>
      <section id="dossier" class="dossier"></section>
      <section id="coverage-list" class="coverage-list"></section>
      <section id="mentions" class="mentions"></section>
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
    const form = document.querySelector("#search-form");
    const query = document.querySelector("#query");
    const mode = document.querySelector("#mode");
    const fromOrder = document.querySelector("#from-order");
    const toOrder = document.querySelector("#to-order");
    const grouped = document.querySelector("#grouped");
    const prefixVariants = document.querySelector("#prefix-variants");
    const topicSort = document.querySelector("#topic-sort");
    const aliasInput = document.querySelector("#dossier-aliases");
    const termsLink = document.querySelector("#terms-link");
    const explainLink = document.querySelector("#explain-link");
    const recapLink = document.querySelector("#recap-link");
    const reportLink = document.querySelector("#report-link");
    const dossierLink = document.querySelector("#dossier-link");
    const evidencePackLink = document.querySelector("#evidence-pack-link");
    const mentionsLink = document.querySelector("#mentions-link");
    const coverageLink = document.querySelector("#coverage-link");
    const shareLink = document.querySelector("#share-link");
    const shareStatus = document.querySelector("#share-status");
    const results = document.querySelector("#results");
    const suggestions = document.querySelector("#suggestions");
    const queryTools = document.querySelector("#query-tools");
    const count = document.querySelector("#count");
    const stats = document.querySelector("#stats");
    const range = document.querySelector("#range");
    const clearRange = document.querySelector("#clear-range");
    const publicNotice = document.querySelector("#public-notice");
    const coverage = document.querySelector("#coverage");
    const recapPanel = document.querySelector("#recap");
    const dossier = document.querySelector("#dossier");
    const coverageList = document.querySelector("#coverage-list");
    const mentions = document.querySelector("#mentions");
    const contents = document.querySelector("#contents");
    const tocCount = document.querySelector("#toc-count");
    const sourceLink = document.querySelector("#source-link");
    let timer = null;
    let privateFulltext = false;
    let chunkResultsEnabled = false;
    let contentsLoaded = false;

    const initial = new URLSearchParams(window.location.search);
    query.value = initial.get("q") || "";
    mode.value = initial.get("mode") || "all";
    fromOrder.value = initial.get("from") || "";
    toOrder.value = initial.get("to") || "";
    grouped.checked = initial.get("grouped") !== "0";
    prefixVariants.checked = initial.get("prefix_variants") === "1";
    topicSort.value = initial.get("sort") || "relevance";
    aliasInput.value = initial.getAll("alias").join(", ");
    updateRangeState();

    form.addEventListener("submit", event => {
      event.preventDefault();
      runSearch();
    });
    [query, fromOrder, toOrder].forEach(input => input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(runSearch, 220);
    }));
    mode.addEventListener("change", runSearch);
    grouped.addEventListener("change", runSearch);
    prefixVariants.addEventListener("change", runSearch);
    topicSort.addEventListener("change", runSearch);
    aliasInput.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(runSearch, 220);
    });
    clearRange.addEventListener("click", () => clearRangeFilter());
    shareLink.addEventListener("click", () => copyShareLink());
    searchTab.addEventListener("click", () => setActiveTab("search"));
    contentsTab.addEventListener("click", () => setActiveTab("contents"));
    suggestions.addEventListener("click", event => {
      const button = event.target.closest("button[data-term]");
      if (!button) return;
      query.value = button.dataset.term;
      runSearch();
    });
    queryTools.addEventListener("click", event => {
      const exactButton = event.target.closest("button[data-exact-query]");
      if (exactButton) {
        query.value = exactButton.dataset.exactQuery;
        runSearch();
      }
    });
    coverage.addEventListener("click", event => {
      const button = event.target.closest("button[data-term]");
      if (!button) return;
      query.value = button.dataset.term;
      runSearch();
    });
    coverageList.addEventListener("click", event => {
      const button = event.target.closest("button[data-bucket-from]");
      if (!button) return;
      applyBucketRange(button.dataset.bucketFrom, button.dataset.bucketTo);
    });
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
      if (!q) {
        count.textContent = "";
        range.textContent = "";
        updateRangeState();
        updateJsonLinks(false);
        coverage.style.display = "none";
        recapPanel.style.display = "none";
        recapPanel.innerHTML = "";
        dossier.style.display = "none";
        dossier.innerHTML = "";
        coverageList.style.display = "none";
        coverageList.innerHTML = "";
        mentions.style.display = "none";
        mentions.innerHTML = "";
        suggestions.style.display = "none";
        suggestions.innerHTML = "";
        queryTools.style.display = "none";
        queryTools.innerHTML = "";
        results.innerHTML = '<div class="empty"></div>';
        updateUrl();
        return;
      }
      const params = searchParams();
      const response = await fetch(`/api/search?${params.toString()}`);
      const payload = await response.json();
      count.textContent = resultCountText(payload);
      range.textContent = rangeText();
      updateRangeState();
      results.innerHTML = payload.results.map(renderResult).join("") || '<div class="empty">No results</div>';
      renderQueryTools(q, payload);
      loadQueryExplain(q, payload);
      updateJsonLinks(true);
      loadSuggestions(q);
      loadRecap();
      loadDossier(params);
      loadCoverage();
      updateUrl();
    }

    async function loadSuggestions(q) {
      const response = await fetch(`/api/suggest?q=${encodeURIComponent(q)}&limit=8`);
      const payload = await response.json();
      const items = payload.suggestions || [];
      if (!items.length) {
        suggestions.style.display = "none";
        suggestions.innerHTML = "";
        return;
      }
      suggestions.style.display = "flex";
      const label = items.some(item => item.match_kind === "near") ? "Near indexed terms:" : "Indexed terms:";
      suggestions.innerHTML = `<span>${label}</span>${items.map(renderSuggestion).join("")}`;
    }

    async function loadQueryExplain(value, searchPayload) {
      const requested = String(value || "").trim();
      if (!requested) return;
      try {
        const response = await fetch(`/api/explain?${explainParams().toString()}`);
        const explain = await response.json();
        if (requested !== query.value.trim()) return;
        renderQueryTools(requested, searchPayload, explain);
      } catch (_error) {
        if (requested === query.value.trim()) renderQueryTools(requested, searchPayload);
      }
    }

    function renderQueryTools(value, payload, explain = null) {
      const exactQuery = exactFallbackQuery(value, payload);
      const tools = [];
      if (explain && explain.metadata_only) {
        tools.push(renderQueryExplain(explain));
      }
      if (exactQuery) {
        tools.push(`<button type="button" data-exact-query="${escapeAttribute(exactQuery)}">Exact only: ${escapeHtml(exactQuery)}</button>`);
      }
      if (!tools.length) {
        queryTools.style.display = "none";
        queryTools.innerHTML = "";
        return;
      }
      queryTools.style.display = "flex";
      queryTools.innerHTML = tools.join("");
    }

    function renderQueryExplain(explain) {
      const exact = explain.exact || {};
      const prefix = explain.prefix || {};
      const resolved = explain.resolved || {};
      const terms = (explain.indexed_terms || []).slice(0, 4);
      const breakdown = (explain.term_breakdown || []).slice(0, 6);
      const cautions = (explain.cautions || []).slice(0, 2);
      const termText = terms.length
        ? ` · Indexed: ${terms.map(term => `${escapeHtml(term.term)} <span>(${Number(term.chunk_count || 0).toLocaleString()} chunk${Number(term.chunk_count || 0) === 1 ? "" : "s"})</span>`).join(" · ")}`
        : "";
      const breakdownText = breakdown.length
        ? ` · Terms: ${breakdown.map(renderExplainTermBreakdown).join(" · ")}`
        : "";
      const cautionText = cautions.length
        ? cautions.map(caution => `<span class="query-caution"><strong>${escapeHtml(caution.code || "caution")}:</strong> ${escapeHtml(caution.message || "")}</span>`).join("")
        : "";
      return `<div class="query-explain"><strong>Query explain:</strong> Exact ${explainCountText(exact)} · Prefix ${explainCountText(prefix)} · Resolved ${escapeHtml(resolved.match_kind || "none")} ${explainCountText(resolved)}${breakdownText}${termText}${cautionText}</div>`;
    }

    function renderExplainTermBreakdown(item) {
      const exact = item.exact || {};
      const prefix = item.prefix || {};
      const resolved = item.resolved || {};
      return `${escapeHtml(item.query || "")} <span>(exact ${explainCountText(exact)}; prefix ${explainCountText(prefix)}; resolved ${escapeHtml(resolved.match_kind || "none")} ${explainCountText(resolved)})</span>`;
    }

    function explainCountText(item) {
      const threadmarks = Number(item.total_threadmarks || 0);
      const chunks = Number(item.total_chunks || 0);
      return `${threadmarks.toLocaleString()} tm, ${chunks.toLocaleString()} chunk${chunks === 1 ? "" : "s"}`;
    }

    function exactFallbackQuery(value, payload) {
      const raw = String(value || "").trim();
      if (!raw || raw.includes('"')) return "";
      if (payload.match_kind !== "prefix") return "";
      if (raw.split(/\\s+/).length !== 1) return "";
      return `"${raw.replace(/"/g, '""')}"`;
    }

    function resultCountText(payload) {
      const shown = Number((payload.results || []).length);
      const totalThreadmarks = Number(payload.total_threadmarks || 0);
      if (totalThreadmarks && shown < totalThreadmarks) {
        return `${shown.toLocaleString()} shown of ${totalThreadmarks.toLocaleString()} threadmarks`;
      }
      return `${shown.toLocaleString()} result${shown === 1 ? "" : "s"}`;
    }

    async function loadDossier(params) {
      const requested = query.value.trim();
      const dossierRequest = dossierParams({ threadmarkLimit: "10", mentionLimit: "10", namedLimit: "16", relatedLimit: "16" });
      const response = await fetch(`/api/dossier?${dossierRequest.toString()}`);
      const report = await response.json();
      if (requested !== query.value.trim()) return;
      const matchHtml = renderMatchNote(report.match_kind, report.query);
      coverage.style.display = "block";
      coverage.innerHTML = `<div><strong>${Number(report.total_threadmarks).toLocaleString()}</strong> threadmark${report.total_threadmarks === 1 ? "" : "s"} · <strong>${Number(report.total_mentions).toLocaleString()}</strong> mention${report.total_mentions === 1 ? "" : "s"}</div>${matchHtml}`;
      renderDossier(report);
      mentions.style.display = "none";
      mentions.innerHTML = "";
    }

    async function loadRecap() {
      const requested = query.value.trim();
      const response = await fetch(`/api/recap?${recapParams().toString()}`);
      const report = await response.json();
      if (requested !== query.value.trim()) return;
      renderRecap(report);
    }

    async function loadCoverage() {
      const requested = query.value.trim();
      const response = await fetch(`/api/coverage?${coverageParams().toString()}`);
      const payload = await response.json();
      if (requested !== query.value.trim()) return;
      const items = payload.items || [];
      if (!items.length) {
        coverageList.style.display = "none";
        coverageList.innerHTML = "";
        return;
      }
      coverageList.style.display = "block";
      coverageList.innerHTML = `<h2>All matching threadmarks${shownCount(items.length, payload.total_threadmarks)}</h2>
        ${renderMatchNote(payload.match_kind, payload.query)}
        ${renderCoverageTermDiagnostics(payload.terms || [])}
        ${renderCoverageBuckets(payload.buckets || [])}
        <div class="coverage-items">${items.map(renderCoverageRow).join("")}</div>`;
    }

    async function loadStats() {
      const response = await fetch("/api/stats");
      const payload = await response.json();
      if (!payload.ok) return;
      if (payload.source_reader_url) {
        sourceLink.href = payload.source_reader_url;
        sourceLink.textContent = payload.source_host ? `${payload.source_host} reader` : "Source reader";
      }
      publicNotice.innerHTML = renderPublicNotice(payload);
      privateFulltext = Boolean(payload.private_fulltext);
      chunkResultsEnabled = Boolean(payload.chunk_results_enabled);
      grouped.disabled = !chunkResultsEnabled;
      if (!chunkResultsEnabled) grouped.checked = true;
      stats.textContent = `${Number(payload.threadmarks).toLocaleString()} threadmarks · ${Number(payload.words).toLocaleString()} words`;
    }

    async function loadContents() {
      contents.innerHTML = '<div class="empty"></div>';
      const response = await fetch("/api/threadmarks?limit=300");
      const payload = await response.json();
      const items = payload.items || [];
      contentsLoaded = true;
      tocCount.textContent = `${items.length} threadmark${items.length === 1 ? "" : "s"}`;
      contents.innerHTML = items.map(renderTocRow).join("") || '<div class="empty"></div>';
    }

    function searchParams() {
      const params = new URLSearchParams({ q: query.value.trim(), mode: mode.value, limit: "30" });
      if (fromOrder.value) params.set("from", fromOrder.value);
      if (toOrder.value) params.set("to", toOrder.value);
      if (!grouped.checked) params.set("grouped", "0");
      addPrefixVariantsParam(params);
      if (topicSort.value !== "relevance") params.set("sort", topicSort.value);
      aliasTerms().forEach(term => params.append("alias", term));
      return params;
    }

    function dossierParams(options = {}) {
      const params = new URLSearchParams({
        q: query.value.trim(),
        mode: mode.value,
        threadmark_limit: options.threadmarkLimit || "100",
        mention_limit: options.mentionLimit || "50"
      });
      if (fromOrder.value) params.set("from", fromOrder.value);
      if (toOrder.value) params.set("to", toOrder.value);
      addPrefixVariantsParam(params);
      params.set("sort", topicSort.value === "timeline" ? "timeline" : "coverage");
      aliasTerms().forEach(term => params.append("alias", term));
      return params;
    }

    function coverageParams() {
      const params = new URLSearchParams({ q: query.value.trim(), mode: mode.value, limit: "300" });
      if (fromOrder.value) params.set("from", fromOrder.value);
      if (toOrder.value) params.set("to", toOrder.value);
      addPrefixVariantsParam(params);
      params.set("sort", topicSort.value === "timeline" ? "timeline" : "coverage");
      aliasTerms().forEach(term => params.append("alias", term));
      return params;
    }

    function reportParams() {
      const params = new URLSearchParams({
        q: query.value.trim(),
        mode: mode.value,
        limit: "100"
      });
      if (fromOrder.value) params.set("from", fromOrder.value);
      if (toOrder.value) params.set("to", toOrder.value);
      addPrefixVariantsParam(params);
      params.set("sort", topicSort.value === "timeline" ? "timeline" : "coverage");
      aliasTerms().forEach(term => params.append("alias", term));
      return params;
    }

    function mentionsParams() {
      const params = new URLSearchParams({ q: query.value.trim(), mode: mode.value, limit: "50" });
      if (fromOrder.value) params.set("from", fromOrder.value);
      if (toOrder.value) params.set("to", toOrder.value);
      addPrefixVariantsParam(params);
      params.set("sort", topicSort.value === "timeline" ? "timeline" : "relevance");
      aliasTerms().forEach(term => params.append("alias", term));
      return params;
    }

    function evidencePackParams() {
      const params = dossierParams({ threadmarkLimit: "25", mentionLimit: "25" });
      params.set("claim_limit", "10");
      return params;
    }

    function termsParams() {
      return new URLSearchParams({
        prefix: query.value.trim(),
        limit: "100"
      });
    }

    function explainParams() {
      const params = new URLSearchParams({
        q: query.value.trim(),
        mode: mode.value,
        term_limit: "12"
      });
      if (fromOrder.value) params.set("from", fromOrder.value);
      if (toOrder.value) params.set("to", toOrder.value);
      addPrefixVariantsParam(params);
      return params;
    }

    function recapParams() {
      const params = dossierParams({ threadmarkLimit: "25", mentionLimit: "25" });
      params.set("timeline_limit", params.get("threadmark_limit") || "25");
      params.delete("threadmark_limit");
      params.set("claim_limit", "10");
      return params;
    }

    function addPrefixVariantsParam(params) {
      if (prefixVariants.checked) params.set("prefix_variants", "1");
    }

    function aliasTerms() {
      const seen = new Set();
      const terms = [];
      for (const item of aliasInput.value.split(/[,\\n;]/)) {
        const term = item.trim();
        const key = term.toLocaleLowerCase();
        if (!term || seen.has(key)) continue;
        seen.add(key);
        terms.push(term);
        if (terms.length >= 8) break;
      }
      return terms;
    }

    function applyBucketRange(start, end) {
      fromOrder.value = String(start || "");
      toOrder.value = String(end || "");
      runSearch();
    }

    function clearRangeFilter() {
      fromOrder.value = "";
      toOrder.value = "";
      runSearch();
    }

    function updateRangeState() {
      clearRange.style.display = fromOrder.value || toOrder.value ? "inline-flex" : "none";
    }

    function updateJsonLinks(show) {
      updateTermsLink(show);
      updateExplainLink(show);
      updateRecapLink(show);
      updateReportLink(show);
      updateDossierLink(show);
      updateEvidencePackLink(show);
      updateCoverageLink(show);
      updateShareLink(show);
    }

    function updateTermsLink(show) {
      if (!show || !query.value.trim()) {
        termsLink.style.display = "none";
        termsLink.href = "/api/terms";
        return;
      }
      termsLink.href = `/api/terms?${termsParams().toString()}`;
      termsLink.style.display = "inline-flex";
    }

    function updateExplainLink(show) {
      if (!show || !query.value.trim()) {
        explainLink.style.display = "none";
        explainLink.href = "/api/explain";
        return;
      }
      explainLink.href = `/api/explain?${explainParams().toString()}`;
      explainLink.style.display = "inline-flex";
    }

    function updateRecapLink(show) {
      if (!show || !query.value.trim()) {
        recapLink.style.display = "none";
        recapLink.href = "/api/recap";
        return;
      }
      recapLink.href = `/api/recap?${recapParams().toString()}`;
      recapLink.style.display = "inline-flex";
    }

    function updateReportLink(show) {
      if (!show || !query.value.trim()) {
        reportLink.style.display = "none";
        reportLink.href = "/api/report";
        return;
      }
      reportLink.href = `/api/report?${reportParams().toString()}`;
      reportLink.style.display = "inline-flex";
    }

    function updateDossierLink(show) {
      if (!show || !query.value.trim()) {
        dossierLink.style.display = "none";
        dossierLink.href = "/api/dossier";
        return;
      }
      dossierLink.href = `/api/dossier?${dossierParams().toString()}`;
      dossierLink.style.display = "inline-flex";
    }

    function updateEvidencePackLink(show) {
      if (!show || !query.value.trim()) {
        evidencePackLink.style.display = "none";
        evidencePackLink.href = "/api/evidence-pack";
        return;
      }
      evidencePackLink.href = `/api/evidence-pack?${evidencePackParams().toString()}`;
      evidencePackLink.style.display = "inline-flex";
    }

    function updateMentionsLink(show) {
      if (!show || !query.value.trim()) {
        mentionsLink.style.display = "none";
        mentionsLink.href = "/api/mentions";
        return;
      }
      mentionsLink.href = `/api/mentions?${mentionsParams().toString()}`;
      mentionsLink.style.display = "inline-flex";
    }

    function updateCoverageLink(show) {
      if (!show || !query.value.trim()) {
        coverageLink.style.display = "none";
        coverageLink.href = "/api/coverage";
        return;
      }
      coverageLink.href = `/api/coverage?${coverageParams().toString()}`;
      coverageLink.style.display = "inline-flex";
    }

    function updateShareLink(show) {
      shareStatus.textContent = "";
      if (!show || !query.value.trim()) {
        shareLink.style.display = "none";
        return;
      }
      shareLink.style.display = "inline-flex";
    }

    function updateUrl() {
      const next = currentStatePath();
      window.history.replaceState(null, "", next);
    }

    function currentStatePath() {
      const params = uiStateParams();
      const serialized = params.toString();
      return serialized ? `${window.location.pathname}?${serialized}` : window.location.pathname;
    }

    function uiStateParams() {
      const params = new URLSearchParams();
      if (query.value.trim()) {
        params.set("q", query.value.trim());
        if (mode.value !== "all") params.set("mode", mode.value);
        if (fromOrder.value) params.set("from", fromOrder.value);
        if (toOrder.value) params.set("to", toOrder.value);
        if (!grouped.checked) params.set("grouped", "0");
        if (prefixVariants.checked) params.set("prefix_variants", "1");
        if (topicSort.value !== "relevance") params.set("sort", topicSort.value);
        aliasTerms().forEach(term => params.append("alias", term));
      }
      if (!contentsPanel.hidden) params.set("view", "contents");
      return params;
    }

    async function copyShareLink() {
      const url = `${window.location.origin}${currentStatePath()}`;
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(url);
        } else {
          fallbackCopy(url);
        }
        shareStatus.textContent = "Copied";
      } catch (_error) {
        shareStatus.textContent = "Copy failed";
      }
      window.setTimeout(() => {
        shareStatus.textContent = "";
      }, 1800);
    }

    function fallbackCopy(value) {
      const area = document.createElement("textarea");
      area.value = value;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.left = "-9999px";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }

    function rangeText() {
      if (fromOrder.value && toOrder.value) return `#${fromOrder.value}-${toOrder.value}`;
      if (fromOrder.value) return `from #${fromOrder.value}`;
      if (toOrder.value) return `through #${toOrder.value}`;
      return "";
    }

    function renderResult(result) {
      const date = result.published_at ? new Date(result.published_at).toLocaleDateString() : "";
      const title = escapeHtml(result.title);
      const author = escapeHtml(result.author || "");
      const order = Number(result.threadmark_order).toLocaleString();
      return `<article>
        <h2><a href="${escapeAttribute(result.source_url)}" target="_blank" rel="noopener noreferrer">${title}</a></h2>
        <div class="meta">
          <span>#${order}</span>
          ${date ? `<span>${date}</span>` : ""}
          ${author ? `<span>${author}</span>` : ""}
        </div>
        ${renderMatchNote(result.match_kind, "Result")}
        <p class="snippet">${result.snippet_html}</p>
        <div class="actions">
          ${privateFulltext && result.local_url ? `<a href="${escapeAttribute(result.local_url)}">Open local text</a>` : ""}
          <a href="${escapeAttribute(result.source_url)}" target="_blank" rel="noopener noreferrer">Open on SV</a>
        </div>
      </article>`;
    }

    function renderSuggestion(item) {
      const term = escapeHtml(item.term);
      const count = Number(item.occurrence_count).toLocaleString();
      const near = item.match_kind === "near" ? ` title="Near match: ${Number(item.edit_distance)} edit${Number(item.edit_distance) === 1 ? "" : "s"}"` : "";
      return `<button type="button" data-term="${escapeAttribute(item.term)}"${near}>${term} <span aria-label="${count} occurrences">(${count})</span></button>`;
    }

    function renderPublicNotice(payload) {
      const notice = escapeHtml(payload.public_notice || "Public results are bounded snippets with source links.");
      const parts = [notice];
      if (payload.source_reader_url) {
        const host = escapeHtml(payload.source_host || "source reader");
        parts.push(`<a href="${escapeAttribute(payload.source_reader_url)}" target="_blank" rel="noopener noreferrer">${host}</a>`);
      }
      if (payload.public_contact) {
        parts.push(`Contact: ${renderNoticeValue(payload.public_contact)}`);
      }
      if (payload.removal_request_url) {
        parts.push(`Removal requests: ${renderNoticeValue(payload.removal_request_url)}`);
      }
      return parts.join(" ");
    }

    function renderNoticeValue(value) {
      const text = String(value || "").trim();
      if (/^(https?:|mailto:)/i.test(text)) {
        return `<a href="${escapeAttribute(text)}" target="_blank" rel="noopener noreferrer">${escapeHtml(text)}</a>`;
      }
      return escapeHtml(text);
    }

    function renderMatchNote(kind, label) {
      if (kind === "prefix") {
        return `<div class="match-note">${escapeHtml(label)} matched by prefix fallback; exact match had no results.</div>`;
      }
      if (kind === "prefix-variants") {
        return `<div class="match-note">${escapeHtml(label)} matched with word variants.</div>`;
      }
      return "";
    }

    function renderMention(mention) {
      const title = escapeHtml(mention.title);
      const order = Number(mention.threadmark_order).toLocaleString();
      return `<div class="mention">
        <div class="meta">
          <span>#${order}</span>
          <a href="${escapeAttribute(mention.source_url)}" target="_blank" rel="noopener noreferrer">${title}</a>
        </div>
        <p>${mention.snippet_html}</p>
      </div>`;
    }

    function renderDossier(report) {
      const timeline = report.timeline || report.mention_windows || [];
      if (!timeline.length) {
        dossier.style.display = "none";
        dossier.innerHTML = "";
        return;
      }
      const timelineHeading = `Timeline${shownCount(timeline.length, report.total_mentions)}`;
      const timelineHtml = `<div class="dossier-section"><h3>${timelineHeading}</h3><div class="dossier-list">${timeline.map(renderDossierMention).join("")}</div></div>`;
      dossier.style.display = "block";
      dossier.innerHTML = `<div class="dossier-header">
        <h2>Topic dossier: ${escapeHtml(report.query)}</h2>
        <div class="dossier-summary">${Number(report.total_threadmarks).toLocaleString()} threadmark${report.total_threadmarks === 1 ? "" : "s"} · ${Number(report.total_mentions).toLocaleString()} mention${report.total_mentions === 1 ? "" : "s"}</div>
      </div>
      ${renderDossierTerms(report.terms || [])}
      <div class="dossier-grid">${timelineHtml}</div>`;
    }

    function renderRecap(report) {
      const timeline = report.timeline || [];
      const claims = report.claims || [];
      if (!timeline.length && !claims.length) {
        recapPanel.style.display = "none";
        recapPanel.innerHTML = "";
        return;
      }
      const timelineHtml = timeline.length
        ? `<div class="recap-section"><h3>Timeline${shownCount(timeline.length, report.total_mentions)}</h3><div class="recap-list">${timeline.map(renderRecapThreadmark).join("")}</div></div>`
        : "";
      const claimHtml = claims.length
        ? `<div class="recap-section"><h3>Claim checks</h3><div class="recap-list">${claims.map(renderRecapClaim).join("")}</div></div>`
        : "";
      recapPanel.style.display = "block";
      recapPanel.innerHTML = `<div class="recap-header">
        <h2>Timeline recap: ${escapeHtml(report.query)}</h2>
        <div class="recap-summary">${Number(report.total_threadmarks).toLocaleString()} threadmark${report.total_threadmarks === 1 ? "" : "s"} · ${Number(report.total_mentions).toLocaleString()} mention${report.total_mentions === 1 ? "" : "s"}</div>
      </div>
      ${renderDossierTerms(report.terms || [])}
      <div class="recap-grid">${timelineHtml}${claimHtml}</div>`;
    }

    function renderRecapThreadmark(item) {
      const date = item.published_at ? new Date(item.published_at).toLocaleDateString() : "";
      const title = escapeHtml(item.title);
      const order = Number(item.threadmark_order).toLocaleString();
      return `<div class="recap-item">
        <div class="meta">
          <span>#${order}</span>
          ${date ? `<span>${date}</span>` : ""}
          <a href="${escapeAttribute(item.source_url)}" target="_blank" rel="noopener noreferrer">${title}</a>
        </div>
        <p>${item.snippet_html}</p>
      </div>`;
    }

    function renderRecapMention(item) {
      const title = escapeHtml(item.title);
      const order = Number(item.threadmark_order).toLocaleString();
      return `<div class="recap-item">
        <div class="meta">
          <span>#${order}</span>
          <a href="${escapeAttribute(item.source_url)}" target="_blank" rel="noopener noreferrer">${title}</a>
        </div>
        <p>${item.snippet_html}</p>
      </div>`;
    }

    function renderRecapClaim(claim) {
      const evidence = claim.evidence || [];
      return `<div class="recap-item">
        <div class="meta">
          <span>${escapeHtml(claim.claim_query || "")}</span>
          <span>${escapeHtml(claim.evidence_level || "")}</span>
          <span>${Number(claim.overlapping_threadmarks || 0).toLocaleString()} overlap${Number(claim.overlapping_threadmarks || 0) === 1 ? "" : "s"}</span>
        </div>
        <p><strong>${escapeHtml(claim.assessment || "")}</strong> ${escapeHtml(claim.guidance || "")}</p>
        ${renderClaimExactDiagnostics(claim)}
        ${renderClaimCautions(claim.cautions || [])}
        ${evidence.length ? `<div class="claim-evidence-label">Evidence snippets${shownCount(claim.evidence_returned || evidence.length, claim.overlapping_threadmarks)}</div>${evidence.slice(0, 3).map(renderClaimEvidence).join("")}` : ""}
      </div>`;
    }

    function shownCount(shown, total) {
      const totalNumber = Number(total || 0);
      const shownNumber = Number(shown || 0);
      if (!totalNumber) return "";
      if (shownNumber < totalNumber) return ` (showing ${shownNumber.toLocaleString()} of ${totalNumber.toLocaleString()})`;
      return ` (${totalNumber.toLocaleString()})`;
    }

    function renderDossierTerms(terms) {
      if (!terms.length) return "";
      const items = terms.map(term => {
        const note = term.match_kind === "prefix" ? "prefix" : "exact";
        return `${escapeHtml(term.query)} <span>(${note})</span>`;
      }).join(" · ");
      return `<div class="match-note">${items}</div>`;
    }

    function renderClaimTermDiagnostics(label, terms) {
      if (!terms.length) return "";
      const items = terms.map(term => {
        const kind = term.match_kind === "prefix" ? "prefix" : term.match_kind === "none" ? "none" : "exact";
        const threadmarks = Number(term.total_threadmarks || 0).toLocaleString();
        const chunks = Number(term.total_chunks || 0).toLocaleString();
        return `${escapeHtml(term.query)} <span>(${kind}, ${threadmarks} tm, ${chunks} chunk${Number(term.total_chunks || 0) === 1 ? "" : "s"})</span>`;
      }).join(" · ");
      return `<div class="match-note"><strong>${escapeHtml(label)}:</strong> ${items}</div>`;
    }

    function renderClaimExactDiagnostics(report) {
      const topicThreadmarks = Number(report.topic_query_exact_threadmarks || 0);
      const topicChunks = Number(report.topic_query_exact_chunks || 0);
      const claimThreadmarks = Number(report.claim_query_exact_threadmarks || 0);
      const claimChunks = Number(report.claim_query_exact_chunks || 0);
      return `<div class="match-note"><strong>Exact query counts:</strong> Topic ${topicThreadmarks.toLocaleString()} tm, ${topicChunks.toLocaleString()} chunk${topicChunks === 1 ? "" : "s"} · Claim ${claimThreadmarks.toLocaleString()} tm, ${claimChunks.toLocaleString()} chunk${claimChunks === 1 ? "" : "s"}</div>`;
    }

    function renderClaimCautions(cautions) {
      if (!cautions.length) return "";
      const items = cautions.map(caution => {
        const code = caution.code ? `<strong>${escapeHtml(caution.code)}:</strong> ` : "";
        return `<li>${code}${escapeHtml(caution.message || "")}</li>`;
      }).join("");
      return `<div class="match-note"><strong>Cautions:</strong><ul>${items}</ul></div>`;
    }

    function renderCoverageTermDiagnostics(terms) {
      if (!terms.length) return "";
      const items = terms.map(term => {
        const kind = term.match_kind === "prefix" ? "prefix" : term.match_kind === "none" ? "none" : "exact";
        const threadmarks = Number(term.total_threadmarks || 0).toLocaleString();
        const chunks = Number(term.total_chunks || 0).toLocaleString();
        return `${escapeHtml(term.query)} <span>(${kind}, ${threadmarks} tm, ${chunks} hit${Number(term.total_chunks || 0) === 1 ? "" : "s"})</span>`;
      }).join(" · ");
      return `<div class="match-note"><strong>Coverage terms:</strong> ${items}</div>`;
    }

    function renderDossierThreadmark(item) {
      const date = item.published_at ? new Date(item.published_at).toLocaleDateString() : "";
      const title = escapeHtml(item.title);
      const order = Number(item.threadmark_order).toLocaleString();
      const hits = Number(item.chunk_hits).toLocaleString();
      return `<div class="dossier-item">
        <div class="meta">
          <span>#${order}</span>
          ${date ? `<span>${date}</span>` : ""}
          <span>${hits} hit${item.chunk_hits === 1 ? "" : "s"}</span>
          <a href="${escapeAttribute(item.source_url)}" target="_blank" rel="noopener noreferrer">${title}</a>
        </div>
        <p>${item.snippet_html}</p>
      </div>`;
    }

    function renderDossierMention(item) {
      const title = escapeHtml(item.title);
      const order = Number(item.threadmark_order).toLocaleString();
      return `<div class="dossier-item">
        <div class="meta">
          <span>#${order}</span>
          <a href="${escapeAttribute(item.source_url)}" target="_blank" rel="noopener noreferrer">${title}</a>
        </div>
        <p>${item.snippet_html}</p>
      </div>`;
    }

    function renderCoverageRow(item) {
      const title = escapeHtml(item.title);
      const order = Number(item.threadmark_order).toLocaleString();
      const date = item.published_at ? new Date(item.published_at).toLocaleDateString() : "";
      const author = escapeHtml(item.author || "");
      const hits = Number(item.hit_count).toLocaleString();
      return `<div class="coverage-row">
        <div class="toc-order">#${order}</div>
        <div class="toc-title">
          <a href="${escapeAttribute(item.source_url)}" target="_blank" rel="noopener noreferrer">${title}</a>
          <div class="toc-meta">${date ? `${date}` : ""}${date && author ? " · " : ""}${author}</div>
        </div>
        <div class="coverage-hit-count">${hits} hit${item.hit_count === 1 ? "" : "s"}</div>
      </div>`;
    }

    function renderCoverageBuckets(buckets) {
      if (!buckets.length) return "";
      return `<div class="coverage-buckets">${buckets.map(bucket => {
        const start = Number(bucket.start_order).toLocaleString();
        const end = Number(bucket.end_order).toLocaleString();
        const threadmarks = Number(bucket.threadmark_count).toLocaleString();
        const chunks = Number(bucket.chunk_count).toLocaleString();
        const rawStart = Number(bucket.start_order);
        const rawEnd = Number(bucket.end_order);
        return `<button class="coverage-bucket" type="button" data-bucket-from="${rawStart}" data-bucket-to="${rawEnd}" aria-label="Filter to threadmarks ${start} through ${end}">#${start}-${end}: <strong>${threadmarks}</strong> tm · ${chunks} hit${bucket.chunk_count === 1 ? "" : "s"}</button>`;
      }).join("")}</div>`;
    }

    function renderClaimEvidence(item) {
      const title = escapeHtml(item.title);
      const order = Number(item.threadmark_order).toLocaleString();
      const cues = item.claim_negation_cues || [];
      const cueHtml = cues.length ? `<div class="match-note"><strong>Negation cues:</strong> ${cues.map(escapeHtml).join(" · ")}</div>` : "";
      const proximity = item.proximity_note ? `<div class="match-note"><strong>Proximity:</strong> ${escapeHtml(item.proximity || item.scope)} · ${escapeHtml(item.proximity_note)}</div>` : "";
      return `<div class="claim-evidence-item">
        <div class="meta">
          <span>${escapeHtml(item.scope)}</span>
          <span>#${order}</span>
          <a href="${escapeAttribute(item.source_url)}" target="_blank" rel="noopener noreferrer">${title}</a>
        </div>
        ${proximity}
        ${cueHtml}
        <div class="claim-evidence-label">Topic</div>
        <p>${item.topic_snippet_html}</p>
        <div class="claim-evidence-label">Claim</div>
        <p>${item.claim_snippet_html}</p>
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
