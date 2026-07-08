from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from itertools import combinations
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
class SearchTerm:
    query: str
    total_threadmarks: int
    total_chunks: int
    match_kind: str = "none"
    match_query: str = ""


@dataclass(frozen=True)
class ContextChunk:
    title: str
    body: str
    post_id: str
    threadmark_order: int
    chunk_index: int
    author: str
    published_at: str | None
    source_url: str
    rank: float


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


@dataclass(frozen=True)
class TopicMention:
    title: str
    post_id: str
    threadmark_order: int
    author: str
    published_at: str | None
    source_url: str
    hit_count: int
    best_snippet: str
    rank: float


@dataclass(frozen=True)
class TopicReport:
    query: str
    total_threadmarks: int
    total_chunks: int
    match_kind: str = "none"
    match_query: str = ""
    aliases: list[str] = field(default_factory=list)
    terms: list[SearchTerm] = field(default_factory=list)
    mentions: list[TopicMention] = field(default_factory=list)


@dataclass(frozen=True)
class CoverageItem:
    title: str
    post_id: str
    threadmark_order: int
    author: str
    published_at: str | None
    source_url: str
    hit_count: int
    rank: float


@dataclass(frozen=True)
class CoverageTerm:
    query: str
    total_threadmarks: int
    total_chunks: int
    match_kind: str = "none"
    match_query: str = ""


@dataclass(frozen=True)
class CoverageBucket:
    start_order: int
    end_order: int
    threadmark_count: int
    chunk_count: int


@dataclass(frozen=True)
class TopicCoverage:
    query: str
    total_threadmarks: int
    total_chunks: int
    match_kind: str = "none"
    match_query: str = ""
    aliases: list[str] = field(default_factory=list)
    terms: list[CoverageTerm] = field(default_factory=list)
    buckets: list[CoverageBucket] = field(default_factory=list)
    items: list[CoverageItem] = field(default_factory=list)


@dataclass(frozen=True)
class ConcordanceMention:
    title: str
    post_id: str
    threadmark_order: int
    chunk_index: int
    occurrence_index: int
    author: str
    published_at: str | None
    source_url: str
    snippet: str
    rank: float


@dataclass(frozen=True)
class ConcordanceTerm:
    query: str
    total_threadmarks: int
    total_mentions: int
    scanned_chunks: int
    match_kind: str = "none"
    match_query: str = ""


@dataclass(frozen=True)
class ConcordanceReport:
    query: str
    total_threadmarks: int
    total_mentions: int
    scanned_chunks: int
    match_kind: str = "none"
    match_query: str = ""
    aliases: list[str] = field(default_factory=list)
    terms: list[ConcordanceTerm] = field(default_factory=list)
    mentions: list[ConcordanceMention] = field(default_factory=list)


@dataclass(frozen=True)
class DossierTerm:
    query: str
    total_threadmarks: int
    total_chunks: int
    total_mentions: int
    match_kind: str = "none"
    match_query: str = ""


@dataclass(frozen=True)
class TopicDossier:
    query: str
    total_threadmarks: int
    total_chunks: int
    total_mentions: int
    scanned_chunks: int
    match_kind: str = "none"
    match_query: str = ""
    aliases: list[str] = field(default_factory=list)
    terms: list[DossierTerm] = field(default_factory=list)
    timeline: list[ConcordanceMention] = field(default_factory=list)
    threadmarks: list[TopicMention] = field(default_factory=list)
    mention_windows: list[ConcordanceMention] = field(default_factory=list)


@dataclass(frozen=True)
class CompareTopic:
    query: str
    total_threadmarks: int
    total_chunks: int
    match_kind: str = "none"
    match_query: str = ""
    first_threadmark: CoverageItem | None = None
    last_threadmark: CoverageItem | None = None
    buckets: list[CoverageBucket] = field(default_factory=list)


@dataclass(frozen=True)
class CompareOverlap:
    queries: list[str]
    total_threadmarks: int
    items: list[CoverageItem] = field(default_factory=list)


@dataclass(frozen=True)
class TopicComparison:
    queries: list[str]
    kind: str = "thread-search-topic-comparison"
    metadata_only: bool = True
    mode: str = "all"
    prefix_variants: bool = False
    bucket_size: int = 25
    topics: list[CompareTopic] = field(default_factory=list)
    all_overlap: CompareOverlap = field(default_factory=lambda: CompareOverlap(queries=[], total_threadmarks=0))
    pairwise_overlaps: list[CompareOverlap] = field(default_factory=list)


@dataclass(frozen=True)
class TermSuggestion:
    term: str
    chunk_count: int
    occurrence_count: int
    match_kind: str = "prefix"
    edit_distance: int = 0


@dataclass(frozen=True)
class TermIndexEntry:
    term: str
    chunk_count: int
    occurrence_count: int


@dataclass(frozen=True)
class TermIndexReport:
    kind: str = "thread-search-term-index"
    metadata_only: bool = True
    prefix: str = ""
    limit: int = 100
    min_chunk_count: int = 1
    stopwords_filtered: bool = True
    result_count: int = 0
    terms: list[TermIndexEntry] = field(default_factory=list)


@dataclass(frozen=True)
class QueryExplainCaution:
    code: str
    message: str


@dataclass(frozen=True)
class QueryTermExplain:
    query: str
    exact: SearchTotals = field(default_factory=lambda: SearchTotals(total_threadmarks=0, total_chunks=0))
    prefix: SearchTotals = field(default_factory=lambda: SearchTotals(total_threadmarks=0, total_chunks=0))
    resolved: SearchTotals = field(default_factory=lambda: SearchTotals(total_threadmarks=0, total_chunks=0))


@dataclass(frozen=True)
class QueryExplain:
    query: str
    kind: str = "thread-search-query-explain"
    metadata_only: bool = True
    mode: str = "all"
    prefix_variants: bool = False
    exact: SearchTotals = field(default_factory=lambda: SearchTotals(total_threadmarks=0, total_chunks=0))
    prefix: SearchTotals = field(default_factory=lambda: SearchTotals(total_threadmarks=0, total_chunks=0))
    resolved: SearchTotals = field(default_factory=lambda: SearchTotals(total_threadmarks=0, total_chunks=0))
    term_breakdown: list[QueryTermExplain] = field(default_factory=list)
    suggestions: list[TermSuggestion] = field(default_factory=list)
    indexed_terms: list[TermIndexEntry] = field(default_factory=list)
    cautions: list[QueryExplainCaution] = field(default_factory=list)


@dataclass(frozen=True)
class ClaimOverlapEvidence:
    title: str
    post_id: str
    threadmark_order: int
    author: str
    published_at: str | None
    source_url: str
    scope: str
    topic_snippet: str
    claim_snippet: str
    topic_chunk_index: int
    claim_chunk_index: int
    chunk_distance: int = 0
    proximity: str = "same-chunk"
    proximity_note: str = ""
    claim_negation_cues: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClaimOverlapTerm:
    query: str
    total_threadmarks: int
    total_chunks: int
    match_kind: str = "none"
    match_query: str = ""


@dataclass(frozen=True)
class ClaimOverlapReport:
    topic_query: str
    claim_query: str
    topic_threadmarks: int
    claim_threadmarks: int
    topic_chunks: int
    claim_chunks: int
    overlapping_threadmarks: int
    overlapping_chunks: int
    topic_match_kind: str = "none"
    claim_match_kind: str = "none"
    topic_match_query: str = ""
    claim_match_query: str = ""
    evidence_returned: int = 0
    evidence_limit: int = 0
    evidence: list[ClaimOverlapEvidence] = field(default_factory=list)
    topic_aliases: list[str] = field(default_factory=list)
    topic_terms: list[ClaimOverlapTerm] = field(default_factory=list)
    claim_terms: list[ClaimOverlapTerm] = field(default_factory=list)


@dataclass(frozen=True)
class ClaimCaution:
    code: str
    message: str


@dataclass(frozen=True)
class ClaimCheckReport:
    topic_query: str
    claim_query: str
    evidence_level: str
    assessment: str
    guidance: str
    negation_cue_evidence: int
    negation_cue_note: str
    topic_threadmarks: int
    claim_threadmarks: int
    topic_chunks: int
    claim_chunks: int
    topic_query_exact_threadmarks: int
    topic_query_exact_chunks: int
    claim_query_exact_threadmarks: int
    claim_query_exact_chunks: int
    overlapping_threadmarks: int
    overlapping_chunks: int
    topic_match_kind: str = "none"
    claim_match_kind: str = "none"
    topic_match_query: str = ""
    claim_match_query: str = ""
    evidence_returned: int = 0
    evidence_limit: int = 0
    evidence: list[ClaimOverlapEvidence] = field(default_factory=list)
    topic_aliases: list[str] = field(default_factory=list)
    topic_terms: list[ClaimOverlapTerm] = field(default_factory=list)
    claim_terms: list[ClaimOverlapTerm] = field(default_factory=list)
    cautions: list[ClaimCaution] = field(default_factory=list)


@dataclass(frozen=True)
class TopicRecap:
    query: str
    bounded_retrieval_only: bool
    total_threadmarks: int
    total_chunks: int
    total_mentions: int
    match_kind: str = "none"
    match_query: str = ""
    kind: str = "thread-search-topic-recap"
    aliases: list[str] = field(default_factory=list)
    terms: list[DossierTerm] = field(default_factory=list)
    timeline: list[ConcordanceMention] = field(default_factory=list)
    mention_windows: list[ConcordanceMention] = field(default_factory=list)
    claims: list[ClaimCheckReport] = field(default_factory=list)


def search_db(
    db_path: Path,
    query: str,
    aliases: tuple[str, ...] = (),
    limit: int = 20,
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    grouped: bool = False,
    sort: str = "relevance",
    prefix_variants: bool = False,
) -> list[SearchResult]:
    term_queries = unique_queries((query, *aliases))
    if not term_queries:
        return []
    if len(term_queries) == 1:
        return search_single_query_db(
            db_path,
            query,
            limit=limit,
            mode=mode,
            order_min=order_min,
            order_max=order_max,
            grouped=grouped,
            sort=sort,
            prefix_variants=prefix_variants,
        )

    fetch_limit = max(limit, limit * 6 if grouped else limit)
    results = [
        result
        for term_query in term_queries
        for result in search_single_query_db(
            db_path,
            term_query,
            limit=fetch_limit,
            mode=mode,
            order_min=order_min,
            order_max=order_max,
            grouped=False,
            sort=sort,
            prefix_variants=prefix_variants,
        )
    ]
    results = merge_search_results(results, grouped=grouped, sort=sort)
    return results[:limit]


def search_single_query_db(
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

    fetch_limit = limit * 6 if grouped else limit
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
    return results[:limit]


def search_totals_db(
    db_path: Path,
    query: str,
    aliases: tuple[str, ...] = (),
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    prefix_variants: bool = False,
) -> SearchTotals:
    terms = search_terms_db(
        db_path,
        query,
        aliases=aliases,
        mode=mode,
        order_min=order_min,
        order_max=order_max,
        prefix_variants=prefix_variants,
    )
    if not terms:
        return SearchTotals(total_threadmarks=0, total_chunks=0)
    if len(terms) == 1:
        term = terms[0]
        return SearchTotals(
            total_threadmarks=term.total_threadmarks,
            total_chunks=term.total_chunks,
            match_kind=term.match_kind,
            match_query=term.match_query,
        )

    post_ids: set[str] = set()
    chunk_keys: set[tuple[str, int]] = set()
    for term_query in unique_queries((query, *aliases)):
        _match_kind, _match_query, keys = search_key_matches_db(
            db_path,
            term_query,
            mode=mode,
            order_min=order_min,
            order_max=order_max,
            prefix_variants=prefix_variants,
        )
        post_ids.update(post_id for post_id, _chunk_index in keys)
        chunk_keys.update(keys)

    primary = terms[0]
    return SearchTotals(
        total_threadmarks=len(post_ids),
        total_chunks=len(chunk_keys),
        match_kind=primary.match_kind,
        match_query=primary.match_query,
    )


def query_explain_db(
    db_path: Path,
    query: str,
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    prefix_variants: bool = False,
    term_limit: int = 12,
) -> QueryExplain:
    exact = exact_search_totals_db(
        db_path,
        query,
        mode=mode,
        order_min=order_min,
        order_max=order_max,
    )
    prefix = prefix_search_totals_db(
        db_path,
        query,
        mode=mode,
        order_min=order_min,
        order_max=order_max,
    )
    resolved = search_totals_db(
        db_path,
        query,
        mode=mode,
        order_min=order_min,
        order_max=order_max,
        prefix_variants=prefix_variants,
    )
    safe_term_limit = max(0, term_limit)
    indexed_terms = term_index_db(db_path, prefix=query, limit=safe_term_limit).terms if safe_term_limit else []
    suggestions = suggest_terms_db(db_path, query, limit=safe_term_limit) if safe_term_limit else []
    term_breakdown = query_term_explain_db(
        db_path,
        query,
        mode=mode,
        order_min=order_min,
        order_max=order_max,
        prefix_variants=prefix_variants,
    )
    cautions = query_explain_cautions(
        exact=exact,
        prefix=prefix,
        resolved=resolved,
        suggestions=suggestions,
        term_breakdown=term_breakdown,
        prefix_variants=prefix_variants,
    )
    return QueryExplain(
        query=query,
        mode=mode,
        prefix_variants=prefix_variants,
        exact=exact,
        prefix=prefix,
        resolved=resolved,
        term_breakdown=term_breakdown,
        suggestions=suggestions,
        indexed_terms=indexed_terms,
        cautions=cautions,
    )


def query_term_explain_db(
    db_path: Path,
    query: str,
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    prefix_variants: bool = False,
) -> list[QueryTermExplain]:
    terms = query_terms(query)
    if len(terms) <= 1:
        return []
    return [
        QueryTermExplain(
            query=term,
            exact=exact_search_totals_db(
                db_path,
                term,
                mode=mode,
                order_min=order_min,
                order_max=order_max,
            ),
            prefix=prefix_search_totals_db(
                db_path,
                term,
                mode=mode,
                order_min=order_min,
                order_max=order_max,
            ),
            resolved=search_totals_db(
                db_path,
                term,
                mode=mode,
                order_min=order_min,
                order_max=order_max,
                prefix_variants=prefix_variants,
            ),
        )
        for term in terms
    ]


def query_explain_cautions(
    *,
    exact: SearchTotals,
    prefix: SearchTotals,
    resolved: SearchTotals,
    suggestions: list[TermSuggestion],
    term_breakdown: list[QueryTermExplain],
    prefix_variants: bool,
) -> list[QueryExplainCaution]:
    cautions: list[QueryExplainCaution] = []
    if exact.total_chunks == 0 and prefix.total_chunks > 0:
        cautions.append(
            QueryExplainCaution(
                code="exact-missing-prefix-available",
                message=(
                    "The exact query has no indexed hits, but word-prefix matches exist. Inspect highlighted "
                    "wording before treating these as exact mentions of the requested term."
                ),
            )
        )
    elif exact.total_chunks > 0 and prefix.total_chunks > exact.total_chunks:
        cautions.append(
            QueryExplainCaution(
                code="prefix-variants-available",
                message=(
                    "Exact hits exist, and additional word-prefix variants are available if you intentionally "
                    "broaden the search."
                ),
            )
        )
    if prefix_variants and resolved.match_kind == "prefix-variants":
        cautions.append(
            QueryExplainCaution(
                code="prefix-variants-enabled",
                message="This query is explicitly including word-prefix variants in the resolved totals.",
            )
        )
    if exact.total_chunks == 0 and prefix.total_chunks == 0:
        if any(item.resolved.total_chunks > 0 for item in term_breakdown):
            cautions.append(
                QueryExplainCaution(
                    code="individual-terms-only",
                    message=(
                        "The combined query has no indexed hits, but one or more individual terms do. "
                        "Use the term breakdown, compare, or claim check before treating this as absence of each term."
                    ),
                )
            )
        elif suggestions:
            cautions.append(
                QueryExplainCaution(
                    code="near-suggestions-only",
                    message=(
                        "No exact or prefix hits were found, but near-term suggestions exist for typo recovery "
                        "or alternate spelling checks."
                    ),
                )
            )
        else:
            cautions.append(
                QueryExplainCaution(
                    code="no-indexed-match",
                    message="No exact, prefix, or near-term indexed match was found for this query.",
                )
            )
    return cautions


def exact_search_totals_db(
    db_path: Path,
    query: str,
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
) -> SearchTotals:
    match_query = make_match_query(query, mode=mode)
    if not match_query:
        return SearchTotals(total_threadmarks=0, total_chunks=0)
    where_sql, params = search_where(match_query, order_min=order_min, order_max=order_max)
    with connect_readonly(db_path) as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(DISTINCT post_id), COUNT(*)
            FROM chunks_fts
            WHERE {where_sql}
            """,
            params,
        ).fetchone()
    total_threadmarks = int(row[0] or 0) if row else 0
    total_chunks = int(row[1] or 0) if row else 0
    return SearchTotals(
        total_threadmarks=total_threadmarks,
        total_chunks=total_chunks,
        match_kind="exact" if total_chunks else "none",
        match_query=match_query,
    )


def prefix_search_totals_db(
    db_path: Path,
    query: str,
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
) -> SearchTotals:
    match_query = make_prefix_match_query(query, mode=mode)
    if not match_query:
        return SearchTotals(total_threadmarks=0, total_chunks=0)
    where_sql, params = search_where(match_query, order_min=order_min, order_max=order_max)
    with connect_readonly(db_path) as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(DISTINCT post_id), COUNT(*)
            FROM chunks_fts
            WHERE {where_sql}
            """,
            params,
        ).fetchone()
    total_threadmarks = int(row[0] or 0) if row else 0
    total_chunks = int(row[1] or 0) if row else 0
    return SearchTotals(
        total_threadmarks=total_threadmarks,
        total_chunks=total_chunks,
        match_kind="prefix" if total_chunks else "none",
        match_query=match_query,
    )


def search_terms_db(
    db_path: Path,
    query: str,
    aliases: tuple[str, ...] = (),
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    prefix_variants: bool = False,
) -> list[SearchTerm]:
    terms: list[SearchTerm] = []
    for term_query in unique_queries((query, *aliases)):
        totals = search_single_totals_db(
            db_path,
            term_query,
            mode=mode,
            order_min=order_min,
            order_max=order_max,
            prefix_variants=prefix_variants,
        )
        terms.append(
            SearchTerm(
                query=term_query,
                total_threadmarks=totals.total_threadmarks,
                total_chunks=totals.total_chunks,
                match_kind=totals.match_kind,
                match_query=totals.match_query,
            )
        )
    return terms


def search_single_totals_db(
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


def search_key_matches_db(
    db_path: Path,
    query: str,
    mode: str,
    order_min: int | None,
    order_max: int | None,
    prefix_variants: bool = False,
) -> tuple[str, str, set[tuple[str, int]]]:
    match_candidates = make_match_query_candidates(query, mode=mode, prefix_variants=prefix_variants)
    if not match_candidates:
        return "none", "", set()
    for match_kind, match_query in match_candidates:
        where_sql, params = search_where(match_query, order_min=order_min, order_max=order_max)
        with connect_readonly(db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT post_id, chunk_index
                FROM chunks_fts
                WHERE {where_sql}
                """,
                params,
            ).fetchall()
        keys = {(str(row[0]), int(row[1])) for row in rows}
        if keys:
            return match_kind, match_query, keys
    return "none", "", set()


def merge_search_results(
    results: list[SearchResult],
    *,
    grouped: bool,
    sort: str,
) -> list[SearchResult]:
    by_chunk = best_by_chunk(results)
    merged = sorted(by_chunk.values(), key=lambda item: search_result_order_key(item, sort))
    if grouped:
        return dedupe_by_post(merged)
    return merged


def search_result_order_key(result: SearchResult, sort: str) -> tuple[int | float, int, float, str, str]:
    if sort == "timeline":
        return (result.threadmark_order, result.chunk_index, result.rank, result.post_id, result.match_query)
    return (result.rank, result.threadmark_order, result.chunk_index, result.post_id, result.match_query)


def claim_overlap_report(
    db_path: Path,
    topic_query: str,
    claim_query: str,
    topic_aliases: tuple[str, ...] = (),
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    limit: int = 50,
    max_chunks: int = 5000,
    prefix_variants: bool = False,
) -> ClaimOverlapReport:
    topic_queries = unique_queries((topic_query, *topic_aliases))
    claim_queries = unique_queries((claim_query,))
    topic_hits, topic_terms = claim_overlap_hits_for_queries(
        db_path,
        topic_queries,
        mode=mode,
        order_min=order_min,
        order_max=order_max,
        max_chunks=max_chunks,
        prefix_variants=prefix_variants,
    )
    claim_hits, claim_terms = claim_overlap_hits_for_queries(
        db_path,
        claim_queries,
        mode=mode,
        order_min=order_min,
        order_max=order_max,
        max_chunks=max_chunks,
        prefix_variants=prefix_variants,
    )

    topic_by_chunk = best_by_chunk(topic_hits)
    claim_by_chunk = best_by_chunk(claim_hits)
    topic_by_post = best_by_post(topic_hits)
    claim_by_post = best_by_post(claim_hits)

    chunk_overlap = set(topic_by_chunk).intersection(claim_by_chunk)
    post_overlap = set(topic_by_post).intersection(claim_by_post)
    evidence: list[ClaimOverlapEvidence] = []
    for post_id in sorted(post_overlap, key=lambda item: topic_by_post[item].threadmark_order):
        topic_hit, claim_hit = closest_claim_overlap_pair(
            [hit for (chunk_post_id, _chunk_index), hit in topic_by_chunk.items() if chunk_post_id == post_id],
            [hit for (chunk_post_id, _chunk_index), hit in claim_by_chunk.items() if chunk_post_id == post_id],
        )
        scope = "chunk" if topic_hit.chunk_index == claim_hit.chunk_index else "threadmark"
        chunk_distance, proximity, proximity_note = evidence_proximity(
            scope,
            topic_hit.chunk_index,
            claim_hit.chunk_index,
        )
        evidence.append(
            ClaimOverlapEvidence(
                title=topic_hit.title,
                post_id=topic_hit.post_id,
                threadmark_order=topic_hit.threadmark_order,
                author=topic_hit.author,
                published_at=topic_hit.published_at,
                source_url=topic_hit.source_url,
                scope=scope,
                topic_snippet=topic_hit.snippet,
                claim_snippet=claim_hit.snippet,
                topic_chunk_index=topic_hit.chunk_index,
                claim_chunk_index=claim_hit.chunk_index,
                chunk_distance=chunk_distance,
                proximity=proximity,
                proximity_note=proximity_note,
            )
        )
        if len(evidence) >= limit:
            break

    return ClaimOverlapReport(
        topic_query=topic_query,
        claim_query=claim_query,
        topic_threadmarks=len(topic_by_post),
        claim_threadmarks=len(claim_by_post),
        topic_chunks=len(topic_by_chunk),
        claim_chunks=len(claim_by_chunk),
        overlapping_threadmarks=len(post_overlap),
        overlapping_chunks=len(chunk_overlap),
        topic_match_kind=result_match_kind(topic_hits),
        claim_match_kind=result_match_kind(claim_hits),
        topic_match_query=result_match_query(topic_hits),
        claim_match_query=result_match_query(claim_hits),
        evidence_returned=len(evidence),
        evidence_limit=limit,
        evidence=evidence,
        topic_aliases=topic_queries[1:],
        topic_terms=topic_terms,
        claim_terms=claim_terms,
    )


def closest_claim_overlap_pair(
    topic_hits: list[SearchResult],
    claim_hits: list[SearchResult],
) -> tuple[SearchResult, SearchResult]:
    if not topic_hits or not claim_hits:
        raise ValueError("closest claim overlap pair requires hits from both sides")
    pairs = (
        (topic_hit, claim_hit)
        for topic_hit in topic_hits
        for claim_hit in claim_hits
    )
    return min(pairs, key=claim_overlap_pair_sort_key)


def claim_overlap_pair_sort_key(
    pair: tuple[SearchResult, SearchResult],
) -> tuple[
    int,
    int,
    int,
    tuple[float, int, int, str],
    tuple[float, int, int, str],
]:
    topic_hit, claim_hit = pair
    lower_chunk = min(topic_hit.chunk_index, claim_hit.chunk_index)
    upper_chunk = max(topic_hit.chunk_index, claim_hit.chunk_index)
    return (
        abs(topic_hit.chunk_index - claim_hit.chunk_index),
        lower_chunk,
        upper_chunk,
        search_hit_sort_key(topic_hit),
        search_hit_sort_key(claim_hit),
    )


def evidence_proximity(scope: str, topic_chunk_index: int, claim_chunk_index: int) -> tuple[int, str, str]:
    distance = abs(topic_chunk_index - claim_chunk_index)
    if scope == "chunk" or distance == 0:
        return (
            distance,
            "same-chunk",
            "Both queries appear in the same indexed chunk.",
        )
    if distance == 1:
        return (
            distance,
            "adjacent-chunk",
            "Both queries appear in the same threadmark but in adjacent indexed chunks.",
        )
    return (
        distance,
        "separated-chunks",
        f"Both queries appear in the same threadmark but their best snippets are {distance} indexed chunks apart.",
    )


def claim_check_report(
    db_path: Path,
    topic_query: str,
    claim_query: str,
    topic_aliases: tuple[str, ...] = (),
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    limit: int = 25,
    max_chunks: int = 5000,
    prefix_variants: bool = False,
) -> ClaimCheckReport:
    report = claim_overlap_report(
        db_path,
        topic_query,
        claim_query,
        topic_aliases=topic_aliases,
        mode=mode,
        order_min=order_min,
        order_max=order_max,
        limit=limit,
        max_chunks=max_chunks,
        prefix_variants=prefix_variants,
    )
    evidence_level, assessment, guidance = classify_claim_overlap(report)
    evidence = annotate_claim_negation_cues(report.evidence)
    negation_cue_evidence = sum(1 for item in evidence if item.claim_negation_cues)
    topic_exact = exact_search_totals_db(
        db_path,
        topic_query,
        mode=mode,
        order_min=order_min,
        order_max=order_max,
    )
    claim_exact = exact_search_totals_db(
        db_path,
        claim_query,
        mode=mode,
        order_min=order_min,
        order_max=order_max,
    )
    cautions = claim_cautions(
        report,
        evidence_level,
        negation_cue_evidence,
        topic_query_exact_threadmarks=topic_exact.total_threadmarks,
    )
    return ClaimCheckReport(
        topic_query=report.topic_query,
        claim_query=report.claim_query,
        evidence_level=evidence_level,
        assessment=assessment,
        guidance=guidance,
        negation_cue_evidence=negation_cue_evidence,
        negation_cue_note=claim_negation_note(negation_cue_evidence),
        topic_threadmarks=report.topic_threadmarks,
        claim_threadmarks=report.claim_threadmarks,
        topic_chunks=report.topic_chunks,
        claim_chunks=report.claim_chunks,
        topic_query_exact_threadmarks=topic_exact.total_threadmarks,
        topic_query_exact_chunks=topic_exact.total_chunks,
        claim_query_exact_threadmarks=claim_exact.total_threadmarks,
        claim_query_exact_chunks=claim_exact.total_chunks,
        overlapping_threadmarks=report.overlapping_threadmarks,
        overlapping_chunks=report.overlapping_chunks,
        topic_match_kind=report.topic_match_kind,
        claim_match_kind=report.claim_match_kind,
        topic_match_query=report.topic_match_query,
        claim_match_query=report.claim_match_query,
        evidence_returned=report.evidence_returned,
        evidence_limit=report.evidence_limit,
        evidence=evidence,
        topic_aliases=report.topic_aliases,
        topic_terms=report.topic_terms,
        claim_terms=report.claim_terms,
        cautions=cautions,
    )


def classify_claim_overlap(report: ClaimOverlapReport) -> tuple[str, str, str]:
    if report.topic_threadmarks == 0 and report.claim_threadmarks == 0:
        return (
            "missing-both",
            "No indexed hits for either query.",
            "Try different names, spellings, or narrower terms before treating this as evidence.",
        )
    if report.topic_threadmarks == 0:
        return (
            "missing-topic",
            "No indexed hits for the topic query.",
            "The claim cannot be checked against this topic until the topic query matches the index.",
        )
    if report.claim_threadmarks == 0:
        return (
            "missing-claim",
            "No indexed hits for the claim query.",
            "The topic appears in the index, but this claim wording does not; try synonyms or alternate wording.",
        )
    if report.overlapping_chunks > 0:
        return (
            "strong-chunk-overlap",
            "Strong overlap: both queries appear in the same indexed chunk.",
            "Review the cited snippets and source links; overlap is evidence of proximity, not an automatic truth verdict.",
        )
    if any(item.proximity == "adjacent-chunk" for item in report.evidence):
        return (
            "adjacent-chunk-overlap",
            "Adjacent overlap: both queries appear in neighboring indexed chunks within the same threadmark.",
            "Treat this as moderate proximity evidence and inspect the source; it is still not an automatic truth verdict.",
        )
    if report.overlapping_threadmarks > 0:
        return (
            "weak-threadmark-overlap",
            "Weak overlap: both queries appear in the same threadmark, but not the same indexed chunk.",
            "Treat this as a loose association and inspect the source; it is not strong support for the claim.",
        )
    return (
        "no-overlap",
        "No overlap: both queries appear, but not in the same threadmark.",
        "With these terms, the index does not surface source-linked support for the association.",
    )


def claim_cautions(
    report: ClaimOverlapReport,
    evidence_level: str,
    negation_cue_evidence: int,
    *,
    topic_query_exact_threadmarks: int = 0,
) -> list[ClaimCaution]:
    cautions: list[ClaimCaution] = []
    if report.topic_match_kind in {"prefix", "prefix-variants"}:
        cautions.append(
            ClaimCaution(
                code="topic-prefix-match",
                message=(
                    "The topic side matched by word prefix rather than exact term only; inspect the "
                    "highlighted wording before treating it as the requested topic."
                ),
            )
        )
        if topic_query_exact_threadmarks == 0:
            cautions.append(
                ClaimCaution(
                    code="topic-exact-missing",
                    message=(
                        "The exact topic query has no indexed hits; this claim check is using prefix "
                        "matches for the topic side, so inspect the highlighted topic wording closely."
                    ),
                )
            )
    if report.claim_match_kind in {"prefix", "prefix-variants"}:
        cautions.append(
            ClaimCaution(
                code="claim-prefix-match",
                message=(
                    "The claim side matched by word prefix rather than exact term only; inspect the "
                    "highlighted wording before treating it as the requested claim."
                ),
            )
        )
    if evidence_level == "adjacent-chunk-overlap":
        cautions.append(
            ClaimCaution(
                code="adjacent-only",
                message=(
                    "The closest overlap is in adjacent indexed chunks, not the same chunk; this is "
                    "moderate proximity evidence and needs source review."
                ),
            )
        )
    elif evidence_level == "weak-threadmark-overlap":
        cautions.append(
            ClaimCaution(
                code="threadmark-only",
                message=(
                    "The terms appear in the same threadmark but not the same indexed chunk; treat this "
                    "as a loose association until the source is inspected."
                ),
            )
        )
    elif evidence_level == "no-overlap":
        cautions.append(
            ClaimCaution(
                code="no-overlap",
                message=(
                    "Both terms appear in the corpus, but this query pair has no same-threadmark overlap."
                ),
            )
        )
    elif evidence_level.startswith("missing-"):
        cautions.append(
            ClaimCaution(
                code="missing-query-side",
                message=(
                    "At least one side of the claim has no indexed hits; try alternate names, spellings, "
                    "or aliases before treating the absence as story evidence."
                ),
            )
        )
    if negation_cue_evidence:
        cautions.append(
            ClaimCaution(
                code="negation-cues",
                message=(
                    "Returned claim snippets contain nearby negation wording; this can indicate a denied "
                    "or reversed claim and should be checked against the source."
                ),
            )
        )
    return cautions


NEGATION_LOOKBACK_TOKENS = 8
NEGATION_LOOKAHEAD_TOKENS = 2

NEGATION_CUE_SEQUENCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("did not", ("did", "not")),
    ("does not", ("does", "not")),
    ("do not", ("do", "not")),
    ("is not", ("is", "not")),
    ("are not", ("are", "not")),
    ("was not", ("was", "not")),
    ("were not", ("were", "not")),
    ("has not", ("has", "not")),
    ("have not", ("have", "not")),
    ("had not", ("had", "not")),
    ("will not", ("will", "not")),
    ("would not", ("would", "not")),
    ("could not", ("could", "not")),
    ("should not", ("should", "not")),
    ("didn't", ("didn't",)),
    ("doesn't", ("doesn't",)),
    ("don't", ("don't",)),
    ("isn't", ("isn't",)),
    ("aren't", ("aren't",)),
    ("wasn't", ("wasn't",)),
    ("weren't", ("weren't",)),
    ("hasn't", ("hasn't",)),
    ("haven't", ("haven't",)),
    ("hadn't", ("hadn't",)),
    ("won't", ("won't",)),
    ("wouldn't", ("wouldn't",)),
    ("couldn't", ("couldn't",)),
    ("shouldn't", ("shouldn't",)),
    ("never", ("never",)),
    ("without", ("without",)),
    ("non", ("non",)),
    ("not", ("not",)),
    ("no", ("no",)),
)


def annotate_claim_negation_cues(evidence: list[ClaimOverlapEvidence]) -> list[ClaimOverlapEvidence]:
    return [
        replace(item, claim_negation_cues=detect_claim_negation_cues(item.claim_snippet))
        for item in evidence
    ]


def claim_negation_note(negation_cue_evidence: int) -> str:
    if negation_cue_evidence:
        return (
            f"{negation_cue_evidence} returned evidence row(s) include lexical negation cues near the "
            "highlighted claim term. Treat this as a triage hint, not proof of what happened."
        )
    return "No lexical negation cues were detected near the highlighted claim term in returned evidence snippets."


def detect_claim_negation_cues(snippet: str) -> tuple[str, ...]:
    text, marked_spans = strip_highlight_markers_with_spans(snippet)
    if not marked_spans:
        return ()

    tokens = [
        (normalize_negation_token(match.group(0)), match.start(), match.end())
        for match in re.finditer(r"[A-Za-z]+(?:['’][A-Za-z]+)?", text)
    ]
    cues: list[str] = []
    matched_ranges: list[set[int]] = []
    for span_start, span_end in marked_spans:
        first_match_index = next(
            (
                index
                for index, (_token, token_start, token_end) in enumerate(tokens)
                if token_end > span_start and token_start < span_end
            ),
            None,
        )
        if first_match_index is None:
            continue
        window_start = max(0, first_match_index - NEGATION_LOOKBACK_TOKENS)
        window_end = min(len(tokens), first_match_index + NEGATION_LOOKAHEAD_TOKENS + 1)
        window_tokens = [token for token, _start, _end in tokens[window_start:window_end]]
        for label, sequence in NEGATION_CUE_SEQUENCES:
            for offset in matching_sequence_offsets(window_tokens, sequence):
                token_range = set(range(window_start + offset, window_start + offset + len(sequence)))
                if any(token_range.intersection(existing) for existing in matched_ranges):
                    continue
                matched_ranges.append(token_range)
                if label not in cues:
                    cues.append(label)
    return tuple(cues)


def strip_highlight_markers_with_spans(snippet: str) -> tuple[str, list[tuple[int, int]]]:
    chars: list[str] = []
    spans: list[tuple[int, int]] = []
    marker_start: int | None = None
    for char in snippet:
        if char == "\x01":
            marker_start = len(chars)
            continue
        if char == "\x02":
            if marker_start is not None:
                spans.append((marker_start, len(chars)))
            marker_start = None
            continue
        chars.append(char)
    return "".join(chars), spans


def normalize_negation_token(token: str) -> str:
    return token.casefold().replace("’", "'")


def matching_sequence_offsets(tokens: list[str], sequence: tuple[str, ...]) -> list[int]:
    if not sequence or len(sequence) > len(tokens):
        return []
    return [
        index
        for index in range(0, len(tokens) - len(sequence) + 1)
        if tuple(tokens[index : index + len(sequence)]) == sequence
    ]


def claim_overlap_hits_for_queries(
    db_path: Path,
    queries: list[str],
    *,
    mode: str,
    order_min: int | None,
    order_max: int | None,
    max_chunks: int,
    prefix_variants: bool = False,
) -> tuple[list[SearchResult], list[ClaimOverlapTerm]]:
    all_hits: list[SearchResult] = []
    terms: list[ClaimOverlapTerm] = []
    for query in queries:
        hits = search_db(
            db_path,
            query,
            limit=max_chunks,
            mode=mode,
            order_min=order_min,
            order_max=order_max,
            grouped=False,
            sort="timeline",
            prefix_variants=prefix_variants,
        )
        by_post = best_by_post(hits)
        by_chunk = best_by_chunk(hits)
        terms.append(
            ClaimOverlapTerm(
                query=query,
                total_threadmarks=len(by_post),
                total_chunks=len(by_chunk),
                match_kind=result_match_kind(hits),
                match_query=result_match_query(hits),
            )
        )
        all_hits.extend(hits)
    return all_hits, terms


def best_by_post(results: list[SearchResult]) -> dict[str, SearchResult]:
    by_post: dict[str, SearchResult] = {}
    for result in results:
        current = by_post.get(result.post_id)
        if current is None or search_hit_sort_key(result) < search_hit_sort_key(current):
            by_post[result.post_id] = result
    return by_post


def best_by_chunk(results: list[SearchResult]) -> dict[tuple[str, int], SearchResult]:
    by_chunk: dict[tuple[str, int], SearchResult] = {}
    for result in results:
        key = (result.post_id, result.chunk_index)
        current = by_chunk.get(key)
        if current is None or search_hit_sort_key(result) < search_hit_sort_key(current):
            by_chunk[key] = result
    return by_chunk


def search_hit_sort_key(result: SearchResult) -> tuple[float, int, int, str]:
    return (result.rank, result.threadmark_order, result.chunk_index, result.post_id)


def result_match_kind(results: list[SearchResult]) -> str:
    return results[0].match_kind if results else "none"


def result_match_query(results: list[SearchResult]) -> str:
    return results[0].match_query if results else ""


def topic_report(
    db_path: Path,
    query: str,
    aliases: tuple[str, ...] = (),
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    limit: int = 200,
    max_chunks: int = 5000,
    sort: str = "coverage",
    prefix_variants: bool = False,
) -> TopicReport:
    term_queries = unique_queries((query, *aliases))
    if not term_queries:
        return TopicReport(query=query, total_threadmarks=0, total_chunks=0)

    if len(term_queries) == 1:
        report = topic_report_single_query(
            db_path,
            query,
            mode=mode,
            order_min=order_min,
            order_max=order_max,
            limit=limit,
            max_chunks=max_chunks,
            sort=sort,
            prefix_variants=prefix_variants,
        )
        return replace(
            report,
            terms=search_terms_db(
                db_path,
                query,
                mode=mode,
                order_min=order_min,
                order_max=order_max,
                prefix_variants=prefix_variants,
            ),
        )

    reports = [
        topic_report_single_query(
            db_path,
            term_query,
            mode=mode,
            order_min=order_min,
            order_max=order_max,
            limit=max(limit, max_chunks),
            max_chunks=max_chunks,
            sort=sort,
            prefix_variants=prefix_variants,
        )
        for term_query in term_queries
    ]
    totals = search_totals_db(
        db_path,
        query,
        aliases=tuple(term_queries[1:]),
        mode=mode,
        order_min=order_min,
        order_max=order_max,
        prefix_variants=prefix_variants,
    )
    terms = search_terms_db(
        db_path,
        query,
        aliases=tuple(term_queries[1:]),
        mode=mode,
        order_min=order_min,
        order_max=order_max,
        prefix_variants=prefix_variants,
    )
    mentions = merge_topic_mentions(
        [mention for report in reports for mention in report.mentions],
        limit=limit,
        sort=sort,
    )
    return TopicReport(
        query=query,
        total_threadmarks=totals.total_threadmarks,
        total_chunks=totals.total_chunks,
        match_kind=terms[0].match_kind if terms else "none",
        match_query=terms[0].match_query if terms else "",
        aliases=term_queries[1:],
        terms=terms,
        mentions=mentions,
    )


def topic_report_single_query(
    db_path: Path,
    query: str,
    mode: str,
    order_min: int | None,
    order_max: int | None,
    limit: int,
    max_chunks: int,
    sort: str,
    prefix_variants: bool = False,
) -> TopicReport:
    chunk_hits = search_db(
        db_path,
        query,
        limit=max_chunks,
        mode=mode,
        order_min=order_min,
        order_max=order_max,
        grouped=False,
        sort="timeline" if sort == "timeline" else "relevance",
        prefix_variants=prefix_variants,
    )
    by_post: dict[str, TopicMention] = {}
    counts: dict[str, int] = {}

    for hit in chunk_hits:
        counts[hit.post_id] = counts.get(hit.post_id, 0) + 1
        if hit.post_id not in by_post:
            by_post[hit.post_id] = TopicMention(
                title=hit.title,
                post_id=hit.post_id,
                threadmark_order=hit.threadmark_order,
                author=hit.author,
                published_at=hit.published_at,
                source_url=hit.source_url,
                hit_count=1,
                best_snippet=hit.snippet,
                rank=hit.rank,
            )

    mentions = [
        TopicMention(
            title=mention.title,
            post_id=mention.post_id,
            threadmark_order=mention.threadmark_order,
            author=mention.author,
            published_at=mention.published_at,
            source_url=mention.source_url,
            hit_count=counts[mention.post_id],
            best_snippet=mention.best_snippet,
            rank=mention.rank,
        )
        for mention in by_post.values()
    ]
    if sort == "timeline":
        mentions.sort(key=lambda item: item.threadmark_order)
    else:
        mentions.sort(key=lambda item: (-item.hit_count, item.threadmark_order))
    return TopicReport(
        query=query,
        total_threadmarks=len(mentions),
        total_chunks=len(chunk_hits),
        match_kind=result_match_kind(chunk_hits),
        match_query=result_match_query(chunk_hits),
        mentions=mentions[:limit],
    )


def topic_coverage(
    db_path: Path,
    query: str,
    aliases: tuple[str, ...] = (),
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    limit: int = 300,
    max_chunks: int = 5000,
    sort: str = "timeline",
    bucket_size: int = 25,
    prefix_variants: bool = False,
) -> TopicCoverage:
    term_queries = unique_queries((query, *aliases))
    if not term_queries:
        return TopicCoverage(query=query, total_threadmarks=0, total_chunks=0)

    term_coverages = [
        coverage_for_query(
            db_path,
            term_query,
            mode=mode,
            order_min=order_min,
            order_max=order_max,
            max_chunks=max_chunks,
            sort=sort,
            prefix_variants=prefix_variants,
        )
        for term_query in term_queries
    ]
    all_items = [item for coverage in term_coverages for item in coverage.items]
    merged_all_items = merge_coverage_items(all_items, limit=len(all_items), sort="timeline")
    merged_items = merge_coverage_items(all_items, limit=limit, sort=sort)
    primary = term_coverages[0]
    terms = [
        CoverageTerm(
            query=coverage.query,
            total_threadmarks=coverage.total_threadmarks,
            total_chunks=coverage.total_chunks,
            match_kind=coverage.match_kind,
            match_query=coverage.match_query,
        )
        for coverage in term_coverages
    ]
    return TopicCoverage(
        query=query,
        total_threadmarks=len({item.post_id for item in all_items}),
        total_chunks=sum(coverage.total_chunks for coverage in term_coverages),
        match_kind=primary.match_kind,
        match_query=primary.match_query,
        aliases=term_queries[1:],
        terms=terms,
        buckets=coverage_buckets(merged_all_items, bucket_size=bucket_size),
        items=merged_items,
    )


def topic_comparison(
    db_path: Path,
    queries: tuple[str, ...],
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    max_chunks: int = 5000,
    overlap_limit: int = 100,
    bucket_size: int = 25,
    prefix_variants: bool = False,
) -> TopicComparison:
    topic_queries = unique_queries(queries)
    if len(topic_queries) < 2:
        return TopicComparison(
            queries=topic_queries,
            mode=mode,
            prefix_variants=prefix_variants,
            bucket_size=bucket_size,
        )

    coverages = [
        coverage_for_query(
            db_path,
            query,
            mode=mode,
            order_min=order_min,
            order_max=order_max,
            max_chunks=max_chunks,
            sort="timeline",
            prefix_variants=prefix_variants,
        )
        for query in topic_queries
    ]
    items_by_query = {
        coverage.query: {item.post_id: item for item in coverage.items}
        for coverage in coverages
    }
    topics = [
        CompareTopic(
            query=coverage.query,
            total_threadmarks=coverage.total_threadmarks,
            total_chunks=coverage.total_chunks,
            match_kind=coverage.match_kind,
            match_query=coverage.match_query,
            first_threadmark=coverage.items[0] if coverage.items else None,
            last_threadmark=coverage.items[-1] if coverage.items else None,
            buckets=coverage_buckets(coverage.items, bucket_size=bucket_size),
        )
        for coverage in coverages
    ]

    return TopicComparison(
        queries=topic_queries,
        mode=mode,
        prefix_variants=prefix_variants,
        bucket_size=max(1, bucket_size),
        topics=topics,
        all_overlap=compare_overlap(
            topic_queries,
            items_by_query,
            limit=overlap_limit,
        ),
        pairwise_overlaps=[
            compare_overlap(
                list(pair),
                items_by_query,
                limit=overlap_limit,
            )
            for pair in combinations(topic_queries, 2)
        ],
    )


def compare_overlap(
    queries: list[str],
    items_by_query: dict[str, dict[str, CoverageItem]],
    *,
    limit: int,
) -> CompareOverlap:
    if not queries:
        return CompareOverlap(queries=[], total_threadmarks=0)

    post_sets = [set(items_by_query.get(query, {})) for query in queries]
    if not post_sets:
        return CompareOverlap(queries=queries, total_threadmarks=0)
    shared_posts = set.intersection(*post_sets) if post_sets else set()
    primary_items = items_by_query.get(queries[0], {})
    items = sorted(
        (primary_items[post_id] for post_id in shared_posts if post_id in primary_items),
        key=lambda item: item.threadmark_order,
    )
    return CompareOverlap(
        queries=queries,
        total_threadmarks=len(shared_posts),
        items=items[:limit],
    )


def coverage_for_query(
    db_path: Path,
    query: str,
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    max_chunks: int = 5000,
    sort: str = "timeline",
    prefix_variants: bool = False,
) -> TopicCoverage:
    match_candidates = make_match_query_candidates(query, mode=mode, prefix_variants=prefix_variants)
    if not match_candidates:
        return TopicCoverage(query=query, total_threadmarks=0, total_chunks=0)

    order_sql = chunk_order_sql("timeline" if sort == "timeline" else "relevance")
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
                    post_id,
                    threadmark_order,
                    author,
                    published_at,
                    source_url,
                    bm25(chunks_fts) AS rank
                FROM chunks_fts
                WHERE {where_sql}
                ORDER BY {order_sql}
                LIMIT ?
                """,
                (*params, max_chunks),
            ).fetchall()
        if rows:
            used_match_kind = match_kind
            used_match_query = match_query
            break

    by_post: dict[str, CoverageItem] = {}
    counts: dict[str, int] = {}
    for row in rows:
        post_id = row["post_id"]
        counts[post_id] = counts.get(post_id, 0) + 1
        rank = float(row["rank"])
        current = by_post.get(post_id)
        if current is not None and current.rank <= rank:
            continue
        by_post[post_id] = CoverageItem(
            title=row["title"],
            post_id=post_id,
            threadmark_order=int(row["threadmark_order"]),
            author=row["author"],
            published_at=row["published_at"],
            source_url=row["source_url"],
            hit_count=1,
            rank=rank,
        )

    items = [
        CoverageItem(
            title=item.title,
            post_id=item.post_id,
            threadmark_order=item.threadmark_order,
            author=item.author,
            published_at=item.published_at,
            source_url=item.source_url,
            hit_count=counts[item.post_id],
            rank=item.rank,
        )
        for item in by_post.values()
    ]
    items = merge_coverage_items(items, limit=len(items), sort=sort)
    return TopicCoverage(
        query=query,
        total_threadmarks=len(items),
        total_chunks=len(rows),
        match_kind=used_match_kind,
        match_query=used_match_query,
        items=items,
    )


def concordance_db(
    db_path: Path,
    query: str,
    aliases: tuple[str, ...] = (),
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    limit: int = 100,
    max_chunks: int = 5000,
    window_chars: int = 320,
    sort: str = "relevance",
    prefix_variants: bool = False,
) -> ConcordanceReport:
    term_queries = unique_queries((query, *aliases))
    if not term_queries:
        return ConcordanceReport(query=query, total_threadmarks=0, total_mentions=0, scanned_chunks=0)

    reports: list[ConcordanceReport] = []
    post_ids: set[str] = set()
    for term_query in term_queries:
        report, report_post_ids = concordance_single_query(
            db_path,
            term_query,
            mode=mode,
            order_min=order_min,
            order_max=order_max,
            limit=limit,
            max_chunks=max_chunks,
            window_chars=window_chars,
            sort=sort,
            prefix_variants=prefix_variants,
        )
        reports.append(report)
        post_ids.update(report_post_ids)

    primary = reports[0]
    merged_mentions = merge_concordance_mentions(
        [mention for report in reports for mention in report.mentions],
        limit=limit,
        sort=sort,
    )
    terms = [
        ConcordanceTerm(
            query=report.query,
            total_threadmarks=report.total_threadmarks,
            total_mentions=report.total_mentions,
            scanned_chunks=report.scanned_chunks,
            match_kind=report.match_kind,
            match_query=report.match_query,
        )
        for report in reports
    ]
    return ConcordanceReport(
        query=query,
        total_threadmarks=len(post_ids),
        total_mentions=sum(report.total_mentions for report in reports),
        scanned_chunks=sum(report.scanned_chunks for report in reports),
        match_kind=primary.match_kind,
        match_query=primary.match_query,
        aliases=term_queries[1:],
        terms=terms,
        mentions=merged_mentions,
    )


def concordance_single_query(
    db_path: Path,
    query: str,
    mode: str,
    order_min: int | None,
    order_max: int | None,
    limit: int,
    max_chunks: int,
    window_chars: int,
    sort: str,
    prefix_variants: bool = False,
) -> tuple[ConcordanceReport, set[str]]:
    match_candidates = make_match_query_candidates(query, mode=mode, prefix_variants=prefix_variants)
    terms = query_terms(query)
    if not match_candidates or not terms:
        return ConcordanceReport(query=query, total_threadmarks=0, total_mentions=0, scanned_chunks=0), set()

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
                    chunks.body AS body,
                    snippet(chunks_fts, 1, x'01', x'02', ' ... ', 42) AS fts_snippet,
                    chunks_fts.post_id AS post_id,
                    chunks_fts.threadmark_order AS threadmark_order,
                    chunks_fts.chunk_index AS chunk_index,
                    author,
                    published_at,
                    source_url,
                    bm25(chunks_fts) AS rank
                FROM chunks_fts
                JOIN chunks ON chunks.id = chunks_fts.rowid
                WHERE {where_sql}
                ORDER BY {order_sql}
                LIMIT ?
                """,
                (*params, max_chunks),
            ).fetchall()
        if rows:
            used_match_kind = match_kind
            used_match_query = match_query
            break

    mentions: list[ConcordanceMention] = []
    post_ids: set[str] = set()
    total_mentions = 0
    for row in rows:
        snippets = extract_mention_windows(row["body"], terms, max_chars=window_chars)
        if not snippets and row["fts_snippet"]:
            snippets = [row["fts_snippet"]]

        for snippet in snippets:
            total_mentions += 1
            post_ids.add(row["post_id"])
            if len(mentions) >= limit:
                continue
            mentions.append(
                ConcordanceMention(
                    title=row["title"],
                    post_id=row["post_id"],
                    threadmark_order=int(row["threadmark_order"]),
                    chunk_index=int(row["chunk_index"]),
                    occurrence_index=total_mentions,
                    author=row["author"],
                    published_at=row["published_at"],
                    source_url=row["source_url"],
                    snippet=snippet,
                    rank=float(row["rank"]),
                )
            )

    return (
        ConcordanceReport(
            query=query,
            total_threadmarks=len(post_ids),
            total_mentions=total_mentions,
            scanned_chunks=len(rows),
            match_kind=used_match_kind,
            match_query=used_match_query,
            mentions=mentions,
        ),
        post_ids,
    )


def topic_dossier(
    db_path: Path,
    query: str,
    aliases: tuple[str, ...] = (),
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    threadmark_limit: int = 100,
    mention_limit: int = 100,
    max_chunks: int = 5000,
    window_chars: int = 320,
    sort: str = "timeline",
    prefix_variants: bool = False,
) -> TopicDossier:
    report_sort = "timeline" if sort == "timeline" else "coverage"
    mention_sort = "timeline" if sort == "timeline" else "relevance"
    term_queries = unique_queries((query, *aliases))
    if not term_queries:
        return TopicDossier(query=query, total_threadmarks=0, total_chunks=0, total_mentions=0, scanned_chunks=0)

    reports: list[TopicReport] = []
    concordances: list[ConcordanceReport] = []
    term_summaries: list[DossierTerm] = []
    for term_query in term_queries:
        report = topic_report(
            db_path,
            term_query,
            mode=mode,
            order_min=order_min,
            order_max=order_max,
            limit=max(threadmark_limit, max_chunks),
            max_chunks=max_chunks,
            sort=report_sort,
            prefix_variants=prefix_variants,
        )
        concordance = concordance_db(
            db_path,
            term_query,
            mode=mode,
            order_min=order_min,
            order_max=order_max,
            limit=mention_limit,
            max_chunks=max_chunks,
            window_chars=window_chars,
            sort=mention_sort,
            prefix_variants=prefix_variants,
        )
        reports.append(report)
        concordances.append(concordance)
        term_summaries.append(
            DossierTerm(
                query=term_query,
                total_threadmarks=report.total_threadmarks,
                total_chunks=report.total_chunks,
                total_mentions=report.total_threadmarks,
                match_kind=report.match_kind if report.match_kind != "none" else concordance.match_kind,
                match_query=report.match_query or concordance.match_query,
            )
        )

    all_threadmark_mentions = [mention for report in reports for mention in report.mentions]
    merged_threadmarks = merge_topic_mentions(
        all_threadmark_mentions,
        limit=threadmark_limit,
        sort=sort,
    )
    all_concordance_mentions = [mention for concordance in concordances for mention in concordance.mentions]
    merged_mentions = merge_concordance_threadmarks(
        all_concordance_mentions,
        limit=mention_limit,
        sort=mention_sort,
    )
    primary = term_summaries[0]
    return TopicDossier(
        query=query,
        total_threadmarks=len({mention.post_id for mention in all_threadmark_mentions}),
        total_chunks=sum(report.total_chunks for report in reports),
        total_mentions=sum(concordance.total_mentions for concordance in concordances),
        scanned_chunks=sum(concordance.scanned_chunks for concordance in concordances),
        match_kind=primary.match_kind,
        match_query=primary.match_query,
        aliases=term_queries[1:],
        terms=term_summaries,
        timeline=merged_mentions,
        threadmarks=merged_threadmarks,
    )


def topic_recap(
    db_path: Path,
    query: str,
    aliases: tuple[str, ...] = (),
    claim_queries: tuple[str, ...] = (),
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    timeline_limit: int = 25,
    mention_limit: int = 25,
    claim_limit: int = 10,
    max_chunks: int = 5000,
    window_chars: int = 320,
    prefix_variants: bool = False,
) -> TopicRecap:
    dossier = topic_dossier(
        db_path,
        query,
        aliases=aliases,
        mode=mode,
        order_min=order_min,
        order_max=order_max,
        threadmark_limit=timeline_limit,
        mention_limit=mention_limit,
        max_chunks=max_chunks,
        window_chars=window_chars,
        sort="timeline",
        prefix_variants=prefix_variants,
    )
    claims = [
        claim_check_report(
            db_path,
            query,
            claim_query,
            topic_aliases=aliases,
            mode=mode,
            order_min=order_min,
            order_max=order_max,
            limit=claim_limit,
            max_chunks=max_chunks,
            prefix_variants=prefix_variants,
        )
        for claim_query in unique_queries(claim_queries)
    ]
    return TopicRecap(
        query=query,
        bounded_retrieval_only=True,
        total_threadmarks=dossier.total_threadmarks,
        total_chunks=dossier.total_chunks,
        total_mentions=dossier.total_mentions,
        match_kind=dossier.match_kind,
        match_query=dossier.match_query,
        aliases=dossier.aliases,
        terms=dossier.terms,
        timeline=dossier.timeline,
        claims=claims,
    )


def unique_queries(queries: tuple[str, ...]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for item in queries:
        normalized = " ".join(item.split())
        folded = normalized.casefold()
        if normalized and folded not in seen:
            seen.add(folded)
            unique.append(normalized)
    return unique


def merge_topic_mentions(mentions: list[TopicMention], limit: int, sort: str) -> list[TopicMention]:
    by_post: dict[str, TopicMention] = {}
    counts: dict[str, int] = {}
    for mention in mentions:
        counts[mention.post_id] = counts.get(mention.post_id, 0) + mention.hit_count
        current = by_post.get(mention.post_id)
        if current is None or mention.rank < current.rank:
            by_post[mention.post_id] = mention

    merged = [
        TopicMention(
            title=mention.title,
            post_id=mention.post_id,
            threadmark_order=mention.threadmark_order,
            author=mention.author,
            published_at=mention.published_at,
            source_url=mention.source_url,
            hit_count=counts[mention.post_id],
            best_snippet=mention.best_snippet,
            rank=mention.rank,
        )
        for mention in by_post.values()
    ]
    if sort == "timeline":
        merged.sort(key=lambda item: item.threadmark_order)
    else:
        merged.sort(key=lambda item: (-item.hit_count, item.threadmark_order))
    return merged[:limit]


def merge_coverage_items(items: list[CoverageItem], limit: int, sort: str) -> list[CoverageItem]:
    by_post: dict[str, CoverageItem] = {}
    counts: dict[str, int] = {}
    for item in items:
        counts[item.post_id] = counts.get(item.post_id, 0) + item.hit_count
        current = by_post.get(item.post_id)
        if current is None or item.rank < current.rank:
            by_post[item.post_id] = item

    merged = [
        CoverageItem(
            title=item.title,
            post_id=item.post_id,
            threadmark_order=item.threadmark_order,
            author=item.author,
            published_at=item.published_at,
            source_url=item.source_url,
            hit_count=counts[item.post_id],
            rank=item.rank,
        )
        for item in by_post.values()
    ]
    if sort == "timeline":
        merged.sort(key=lambda item: item.threadmark_order)
    else:
        merged.sort(key=lambda item: (-item.hit_count, item.threadmark_order))
    return merged[:limit]


def coverage_buckets(items: list[CoverageItem], bucket_size: int = 25) -> list[CoverageBucket]:
    size = max(1, bucket_size)
    by_start: dict[int, dict[str, int]] = {}
    for item in items:
        start = ((item.threadmark_order - 1) // size) * size + 1
        bucket = by_start.setdefault(start, {"threadmarks": 0, "chunks": 0})
        bucket["threadmarks"] += 1
        bucket["chunks"] += item.hit_count
    return [
        CoverageBucket(
            start_order=start,
            end_order=start + size - 1,
            threadmark_count=values["threadmarks"],
            chunk_count=values["chunks"],
        )
        for start, values in sorted(by_start.items())
    ]


def merge_concordance_mentions(
    mentions: list[ConcordanceMention],
    limit: int,
    sort: str,
) -> list[ConcordanceMention]:
    by_key: dict[tuple[str, int, int, str], ConcordanceMention] = {}
    for mention in mentions:
        key = (mention.post_id, mention.chunk_index, mention.occurrence_index, mention.snippet)
        current = by_key.get(key)
        if current is None or mention.rank < current.rank:
            by_key[key] = mention

    merged = list(by_key.values())
    if sort == "timeline":
        merged.sort(key=lambda item: (item.threadmark_order, item.chunk_index, item.occurrence_index))
    else:
        merged.sort(key=lambda item: (item.rank, item.threadmark_order, item.chunk_index, item.occurrence_index))

    renumbered: list[ConcordanceMention] = []
    for index, mention in enumerate(merged[:limit], start=1):
        renumbered.append(
            ConcordanceMention(
                title=mention.title,
                post_id=mention.post_id,
                threadmark_order=mention.threadmark_order,
                chunk_index=mention.chunk_index,
                occurrence_index=index,
                author=mention.author,
                published_at=mention.published_at,
                source_url=mention.source_url,
                snippet=mention.snippet,
                rank=mention.rank,
            )
        )
    return renumbered


def merge_concordance_threadmarks(
    mentions: list[ConcordanceMention],
    limit: int,
    sort: str,
) -> list[ConcordanceMention]:
    by_post: dict[str, ConcordanceMention] = {}
    for mention in mentions:
        current = by_post.get(mention.post_id)
        if current is None:
            by_post[mention.post_id] = mention
            continue
        if sort == "timeline":
            if (mention.threadmark_order, mention.chunk_index, mention.occurrence_index) < (
                current.threadmark_order,
                current.chunk_index,
                current.occurrence_index,
            ):
                by_post[mention.post_id] = mention
        elif (mention.rank, mention.threadmark_order, mention.chunk_index, mention.occurrence_index) < (
            current.rank,
            current.threadmark_order,
            current.chunk_index,
            current.occurrence_index,
        ):
            by_post[mention.post_id] = mention

    merged = list(by_post.values())
    if sort == "timeline":
        merged.sort(key=lambda item: (item.threadmark_order, item.chunk_index, item.occurrence_index))
    else:
        merged.sort(key=lambda item: (item.rank, item.threadmark_order, item.chunk_index, item.occurrence_index))

    renumbered: list[ConcordanceMention] = []
    for index, mention in enumerate(merged[:limit], start=1):
        renumbered.append(
            ConcordanceMention(
                title=mention.title,
                post_id=mention.post_id,
                threadmark_order=mention.threadmark_order,
                chunk_index=mention.chunk_index,
                occurrence_index=index,
                author=mention.author,
                published_at=mention.published_at,
                source_url=mention.source_url,
                snippet=mention.snippet,
                rank=mention.rank,
            )
        )
    return renumbered


def dedupe_by_post(results: list[SearchResult]) -> list[SearchResult]:
    seen: set[str] = set()
    deduped: list[SearchResult] = []
    for result in results:
        if result.post_id in seen:
            continue
        seen.add(result.post_id)
        deduped.append(result)
    return deduped


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


def suggest_terms_db(db_path: Path, prefix: str, limit: int = 10) -> list[TermSuggestion]:
    normalized = normalize_suggestion_prefix(prefix)
    if len(normalized) < 2:
        return []

    upper = next_prefix_bound(normalized)
    with connect_readonly(db_path) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT term, doc, cnt
                FROM terms_vocab
                WHERE term >= ? AND term < ?
                ORDER BY doc DESC, cnt DESC, term
                LIMIT ?
                """,
                (normalized, upper, limit),
            ).fetchall()
        except sqlite3.Error:
            return []

    return [
        TermSuggestion(
            term=row["term"],
            chunk_count=int(row["doc"]),
            occurrence_count=int(row["cnt"]),
        )
        for row in rows
    ] or near_term_suggestions_db(db_path, normalized, limit=limit)


def near_term_suggestions_db(db_path: Path, term: str, limit: int = 10) -> list[TermSuggestion]:
    normalized = normalize_suggestion_prefix(term)
    if len(normalized) < 4 or limit <= 0:
        return []

    max_distance = near_suggestion_distance_limit(normalized)
    min_length = max(1, len(normalized) - max_distance)
    max_length = len(normalized) + max_distance
    try:
        with connect_readonly(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT term, doc, cnt
                FROM terms_vocab
                WHERE length(term) BETWEEN ? AND ?
                """,
                (min_length, max_length),
            ).fetchall()
    except sqlite3.Error:
        return []

    suggestions: list[TermSuggestion] = []
    for row in rows:
        candidate = row["term"]
        if not re.fullmatch(r"[a-z0-9_]+", candidate):
            continue
        distance = bounded_edit_distance(normalized, candidate, max_distance=max_distance)
        if distance == 0 or distance > max_distance:
            continue
        suggestions.append(
            TermSuggestion(
                term=candidate,
                chunk_count=int(row["doc"]),
                occurrence_count=int(row["cnt"]),
                match_kind="near",
                edit_distance=distance,
            )
        )

    suggestions.sort(key=lambda item: (item.edit_distance, -item.chunk_count, -item.occurrence_count, item.term))
    return suggestions[:limit]


def term_index_db(
    db_path: Path,
    prefix: str = "",
    limit: int = 100,
    min_chunk_count: int = 1,
    include_stopwords: bool = False,
) -> TermIndexReport:
    normalized_prefix = normalize_suggestion_prefix(prefix) if prefix else ""
    effective_limit = max(0, limit)
    effective_min_chunk_count = max(1, min_chunk_count)
    if effective_limit <= 0:
        return TermIndexReport(
            prefix=normalized_prefix,
            limit=effective_limit,
            min_chunk_count=effective_min_chunk_count,
            stopwords_filtered=not include_stopwords,
        )

    where = ["doc >= ?"]
    params: list[object] = [effective_min_chunk_count]
    if normalized_prefix:
        where.append("term >= ?")
        where.append("term < ?")
        params.extend([normalized_prefix, next_prefix_bound(normalized_prefix)])
    fetch_limit = effective_limit if include_stopwords else max(effective_limit, effective_limit * 8)
    params.append(fetch_limit)
    try:
        with connect_readonly(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT term, doc, cnt
                FROM terms_vocab
                WHERE {" AND ".join(where)}
                ORDER BY doc DESC, cnt DESC, term
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
    except sqlite3.Error:
        rows = []

    stopwords = term_index_stopwords() if not include_stopwords else set()
    entries: list[TermIndexEntry] = []
    for row in rows:
        term = str(row["term"])
        if not re.fullmatch(r"[a-z0-9_]{2,}", term):
            continue
        if term in stopwords:
            continue
        entries.append(
            TermIndexEntry(
                term=term,
                chunk_count=int(row["doc"]),
                occurrence_count=int(row["cnt"]),
            )
        )
        if len(entries) >= effective_limit:
            break

    return TermIndexReport(
        prefix=normalized_prefix,
        limit=effective_limit,
        min_chunk_count=effective_min_chunk_count,
        stopwords_filtered=not include_stopwords,
        result_count=len(entries),
        terms=entries,
    )


def context_db(
    db_path: Path,
    query: str,
    limit: int = 8,
    mode: str = "all",
    order_min: int | None = None,
    order_max: int | None = None,
    max_chars: int = 2200,
) -> list[ContextChunk]:
    match_queries = make_match_queries(query, mode=mode)
    if not match_queries:
        return []

    rows: list[sqlite3.Row] = []
    for match_query in match_queries:
        where_sql, params = search_where(match_query, order_min=order_min, order_max=order_max)
        with connect_readonly(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT
                    title,
                    chunks.body AS body,
                    chunks_fts.post_id AS post_id,
                    chunks_fts.threadmark_order AS threadmark_order,
                    chunks_fts.chunk_index AS chunk_index,
                    author,
                    published_at,
                    source_url,
                    bm25(chunks_fts) AS rank
                FROM chunks_fts
                JOIN chunks ON chunks.id = chunks_fts.rowid
                WHERE {where_sql}
                ORDER BY rank
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        if rows:
            break

    return [
        ContextChunk(
            title=row["title"],
            body=trim_context(row["body"], max_chars=max_chars),
            post_id=row["post_id"],
            threadmark_order=int(row["threadmark_order"]),
            chunk_index=int(row["chunk_index"]),
            author=row["author"],
            published_at=row["published_at"],
            source_url=row["source_url"],
            rank=float(row["rank"]),
        )
        for row in rows
    ]


def search_where(
    match_query: str,
    order_min: int | None = None,
    order_max: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    clauses = ["chunks_fts MATCH ?"]
    params: list[Any] = [match_query]
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


def make_match_query(query: str, mode: str = "all") -> str:
    parts = [quote_fts_phrase(term) for term in query_terms(query)]
    parts = [part for part in parts if part]
    if not parts:
        return ""

    joiner = " OR " if mode == "any" else " AND "
    return joiner.join(parts)


def make_match_queries(query: str, mode: str = "all", prefix_variants: bool = False) -> list[str]:
    return [
        match_query
        for _kind, match_query in make_match_query_candidates(
            query,
            mode=mode,
            prefix_variants=prefix_variants,
        )
    ]


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
    unique: list[str] = []
    candidates: list[tuple[str, str]] = []
    for kind, item in queries:
        if item and item not in unique:
            unique.append(item)
            candidates.append((kind, item))
    return candidates


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


def escape_fts_token(value: str) -> str:
    return quote_fts_phrase(value)


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


def is_query_stopword(value: str) -> bool:
    return value.casefold() in QUERY_STOPWORDS


def normalized_query_term(value: str) -> str:
    return " ".join(value.split()).strip()


def normalize_suggestion_prefix(value: str) -> str:
    without_phrases = re.sub(r'"[^"]+"', " ", value)
    terms = [
        normalized_query_term(word)
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_]{1,}", without_phrases)
    ]
    return terms[-1].lower() if terms else ""


def term_index_stopwords() -> set[str]:
    return set(QUERY_STOPWORDS)


def next_prefix_bound(value: str) -> str:
    if not value:
        return "\U0010ffff"
    return value[:-1] + chr(ord(value[-1]) + 1)


def near_suggestion_distance_limit(term: str) -> int:
    return 1 if len(term) <= 5 else 2


def bounded_edit_distance(left: str, right: str, max_distance: int) -> int:
    if abs(len(left) - len(right)) > max_distance:
        return max_distance + 1

    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        row_min = left_index
        for right_index, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            value = min(
                previous[right_index] + 1,
                current[right_index - 1] + 1,
                previous[right_index - 1] + cost,
            )
            current.append(value)
            row_min = min(row_min, value)
        if row_min > max_distance:
            return max_distance + 1
        previous = current
    return previous[-1]


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


MENTION_CLUSTER_MAX_GAP = 80


def extract_mention_windows(text: str, terms: list[str], max_chars: int = 320) -> list[str]:
    pattern = mention_pattern(terms)
    if pattern is None:
        return []
    matches = list(pattern.finditer(text))
    if not matches:
        return []

    clusters: list[tuple[int, int]] = []
    start, end = matches[0].span()
    for match in matches[1:]:
        next_start, next_end = match.span()
        gap_text = text[end:next_start]
        if next_start - end <= MENTION_CLUSTER_MAX_GAP and "\n\n" not in gap_text:
            end = next_end
            continue
        clusters.append((start, end))
        start, end = next_start, next_end
    clusters.append((start, end))
    return [snippet_window_for_span(text, start, end, pattern, max_chars=max_chars) for start, end in clusters]


def mention_pattern(terms: list[str]) -> re.Pattern[str] | None:
    parts: list[str] = []
    for term in sorted((term for term in terms if term.strip()), key=len, reverse=True):
        escaped = re.escape(term)
        escaped = re.sub(r"\\\s+", r"\\s+", escaped)
        if re.fullmatch(r"[A-Za-z0-9_]+", term):
            escaped = rf"(?<!\w){escaped}(?!\w)"
        parts.append(escaped)
    if not parts:
        return None
    return re.compile("|".join(parts), flags=re.IGNORECASE)


def snippet_window(text: str, match: re.Match[str], pattern: re.Pattern[str], max_chars: int) -> str:
    return snippet_window_for_span(text, match.start(), match.end(), pattern, max_chars=max_chars)


def snippet_window_for_span(text: str, match_start: int, match_end: int, pattern: re.Pattern[str], max_chars: int) -> str:
    if max_chars <= 0:
        return pattern.sub(lambda item: f"\x01{item.group(0)}\x02", text)

    match_length = match_end - match_start
    window = max(max_chars, match_length)
    padding = max(0, (window - match_length) // 2)
    start = max(0, match_start - padding)
    end = min(len(text), match_end + padding)

    if start > 0:
        boundary = max(text.rfind("\n", 0, start + 1), text.rfind(". ", 0, start + 1))
        if boundary >= max(0, match_start - window):
            start = boundary + (2 if text[boundary : boundary + 2] == ". " else 1)
    if end < len(text):
        boundary = text.find(". ", end)
        if boundary != -1 and boundary <= match_end + window:
            end = boundary + 1

    snippet = text[start:end].strip()
    if start > 0:
        snippet = "[...] " + snippet
    if end < len(text):
        snippet = snippet + " [...]"
    return pattern.sub(lambda item: f"\x01{item.group(0)}\x02", snippet)


def trim_context(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cutoff = text.rfind("\n\n", 0, max_chars)
    if cutoff < max_chars // 2:
        cutoff = text.rfind(". ", 0, max_chars)
        if cutoff >= max_chars // 2:
            cutoff += 1
    if cutoff < max_chars // 2:
        cutoff = max_chars
    return text[:cutoff].rstrip() + "\n[...]"
