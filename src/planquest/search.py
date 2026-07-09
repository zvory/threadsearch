from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
from typing import Any

from .db import connect_readonly


QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "these",
    "this",
    "those",
    "to",
    "was",
    "were",
    "with",
}


@dataclass(frozen=True)
class SearchResult:
    title: str
    snippet: str
    post_id: str
    threadmark_order: int
    chunk_index: int
    author: str
    published_at: str | None
    source_url: str
    rank: float
    match_kind: str = "exact"
    match_query: str = ""


@dataclass(frozen=True)
class SearchTotals:
    total_threadmarks: int
    total_chunks: int
    match_kind: str = "none"
    match_query: str = ""


@dataclass(frozen=True)
class ThreadmarkDetail:
    title: str
    body: str
    post_id: str
    threadmark_order: int
    category_id: int
    category_name: str
    author: str
    published_at: str | None
    source_url: str
    reader_url: str
    word_count: int


@dataclass(frozen=True)
class ThreadmarkMeta:
    title: str
    post_id: str
    threadmark_order: int
    category_id: int
    category_name: str
    author: str
    published_at: str | None
    source_url: str
    reader_url: str
    word_count: int


def search_db(
    db_path: Path,
    query: str,
    limit: int = 20,
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    grouped: bool = False,
    sort: str = "relevance",
    prefix_variants: bool = False,
) -> list[SearchResult]:
    match_candidates = make_match_query_candidates(query, mode=mode, prefix_variants=prefix_variants)
    if not match_candidates:
        return []

    fetch_limit = max(1, limit) * 6 if grouped else max(1, limit)
    order_sql = chunk_order_sql(sort)
    rows: list[sqlite3.Row] = []
    used_match_kind = "none"
    used_match_query = ""
    for match_kind, match_query in match_candidates:
        where_sql, params = search_where(match_query, order_min=order_min, order_max=order_max)
        with connect_readonly(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT
                    title,
                    snippet(chunks_fts, 1, x'01', x'02', ' ... ', 42) AS snippet,
                    post_id,
                    threadmark_order,
                    chunk_index,
                    author,
                    published_at,
                    source_url,
                    bm25(chunks_fts) AS rank
                FROM chunks_fts
                WHERE {where_sql}
                ORDER BY {order_sql}
                LIMIT ?
                """,
                (*params, fetch_limit),
            ).fetchall()
        if rows:
            used_match_kind = match_kind
            used_match_query = match_query
            break

    results = [
        SearchResult(
            title=row["title"],
            snippet=row["snippet"],
            post_id=row["post_id"],
            threadmark_order=int(row["threadmark_order"]),
            chunk_index=int(row["chunk_index"]),
            author=row["author"],
            published_at=row["published_at"],
            source_url=row["source_url"],
            rank=float(row["rank"]),
            match_kind=used_match_kind,
            match_query=used_match_query,
        )
        for row in rows
    ]
    if grouped:
        results = dedupe_by_post(results)
    return results[: max(0, limit)]


def search_totals_db(
    db_path: Path,
    query: str,
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    prefix_variants: bool = False,
) -> SearchTotals:
    match_candidates = make_match_query_candidates(query, mode=mode, prefix_variants=prefix_variants)
    if not match_candidates:
        return SearchTotals(total_threadmarks=0, total_chunks=0)

    for match_kind, match_query in match_candidates:
        where_sql, params = search_where(match_query, order_min=order_min, order_max=order_max)
        with connect_readonly(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total_chunks,
                    COUNT(DISTINCT post_id) AS total_threadmarks
                FROM chunks_fts
                WHERE {where_sql}
                """,
                params,
            ).fetchone()
        total_chunks = int(row["total_chunks"] or 0)
        if total_chunks:
            return SearchTotals(
                total_threadmarks=int(row["total_threadmarks"] or 0),
                total_chunks=total_chunks,
                match_kind=match_kind,
                match_query=match_query,
            )
    return SearchTotals(total_threadmarks=0, total_chunks=0)


def threadmark_detail(db_path: Path, post_id: str) -> ThreadmarkDetail | None:
    with connect_readonly(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                title,
                body,
                post_id,
                threadmark_order,
                category_id,
                category_name,
                author,
                published_at,
                source_url,
                reader_url,
                word_count
            FROM threadmarks
            WHERE post_id = ?
            """,
            (post_id,),
        ).fetchone()

    if row is None:
        return None

    return ThreadmarkDetail(
        title=row["title"],
        body=row["body"],
        post_id=row["post_id"],
        threadmark_order=int(row["threadmark_order"]),
        category_id=int(row["category_id"]),
        category_name=row["category_name"],
        author=row["author"],
        published_at=row["published_at"],
        source_url=row["source_url"],
        reader_url=row["reader_url"],
        word_count=int(row["word_count"]),
    )


def list_threadmarks_db(
    db_path: Path,
    limit: int = 300,
    order_min: int | None = None,
    order_max: int | None = None,
) -> list[ThreadmarkMeta]:
    clauses: list[str] = []
    params: list[Any] = []
    if order_min is not None:
        clauses.append("threadmark_order >= ?")
        params.append(order_min)
    if order_max is not None:
        clauses.append("threadmark_order <= ?")
        params.append(order_max)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with connect_readonly(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT
                title,
                post_id,
                threadmark_order,
                category_id,
                category_name,
                author,
                published_at,
                source_url,
                reader_url,
                word_count
            FROM threadmarks
            {where_sql}
            ORDER BY threadmark_order
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()

    return [
        ThreadmarkMeta(
            title=row["title"],
            post_id=row["post_id"],
            threadmark_order=int(row["threadmark_order"]),
            category_id=int(row["category_id"]),
            category_name=row["category_name"],
            author=row["author"],
            published_at=row["published_at"],
            source_url=row["source_url"],
            reader_url=row["reader_url"],
            word_count=int(row["word_count"]),
        )
        for row in rows
    ]


def search_where(
    match_query: str,
    order_min: int | None = None,
    order_max: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    clauses = ["chunks_fts MATCH ?"]
    params: list[Any] = [f"body:({match_query})"]
    if order_min is not None:
        clauses.append("CAST(chunks_fts.threadmark_order AS INTEGER) >= ?")
        params.append(order_min)
    if order_max is not None:
        clauses.append("CAST(chunks_fts.threadmark_order AS INTEGER) <= ?")
        params.append(order_max)
    return " AND ".join(clauses), tuple(params)


def chunk_order_sql(sort: str) -> str:
    if sort == "timeline":
        return "CAST(chunks_fts.threadmark_order AS INTEGER), CAST(chunks_fts.chunk_index AS INTEGER), rank"
    return "rank"


def make_match_query_candidates(query: str, mode: str = "all", prefix_variants: bool = False) -> list[tuple[str, str]]:
    if prefix_variants:
        queries = [
            ("prefix-variants", make_prefix_match_query(query, mode=mode)),
            ("exact", make_match_query(query, mode=mode)),
        ]
    else:
        queries = [
            ("exact", make_match_query(query, mode=mode)),
            ("prefix", make_prefix_match_query(query, mode=mode)),
        ]

    seen: set[str] = set()
    candidates: list[tuple[str, str]] = []
    for kind, item in queries:
        if item and item not in seen:
            seen.add(item)
            candidates.append((kind, item))
    return candidates


def make_match_query(query: str, mode: str = "all") -> str:
    parts = [quote_fts_phrase(term) for term in query_terms(query)]
    parts = [part for part in parts if part]
    if not parts:
        return ""

    joiner = " OR " if mode == "any" else " AND "
    return joiner.join(parts)


def make_prefix_match_query(query: str, mode: str = "all") -> str:
    parts = [quote_fts_prefix(term) for term in prefix_query_terms(query)]
    parts = [part for part in parts if part]
    if not parts:
        return ""

    joiner = " OR " if mode == "any" else " AND "
    return joiner.join(parts)


def quote_fts_phrase(value: str) -> str:
    escaped = value.replace('"', '""').strip()
    return f'"{escaped}"' if escaped else ""


def quote_fts_prefix(value: str) -> str:
    escaped = value.replace('"', '""').strip()
    return f'"{escaped}"*' if escaped else ""


def query_terms(query: str) -> list[str]:
    phrases = [normalized_query_term(phrase) for phrase in re.findall(r'"([^"]+)"', query)]
    without_phrases = re.sub(r'"[^"]+"', " ", query)
    words = [
        normalized_query_term(word)
        for word in re.findall(r"[A-Za-z0-9_]{2,}", without_phrases)
        if not is_query_stopword(word)
    ]
    terms: list[str] = []
    for term in [*phrases, *words]:
        if term and term not in terms:
            terms.append(term)
    return terms


def prefix_query_terms(query: str) -> list[str]:
    without_phrases = re.sub(r'"[^"]+"', " ", query)
    words = [
        normalized_query_term(word)
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", without_phrases)
        if not is_query_stopword(word)
    ]
    terms: list[str] = []
    for term in words:
        if len(term) >= 4 and term not in terms:
            terms.append(term)
    return terms


def normalized_query_term(value: str) -> str:
    return " ".join(value.split()).strip()


def is_query_stopword(value: str) -> bool:
    return value.casefold() in QUERY_STOPWORDS


def dedupe_by_post(results: list[SearchResult]) -> list[SearchResult]:
    seen: set[str] = set()
    deduped: list[SearchResult] = []
    for result in results:
        if result.post_id in seen:
            continue
        seen.add(result.post_id)
        deduped.append(result)
    return deduped
