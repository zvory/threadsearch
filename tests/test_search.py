from pathlib import Path

from planquest.indexer import build_index
from planquest.models import Threadmark
from planquest.scrape import write_jsonl
from planquest.search import (
    claim_check_report,
    claim_overlap_report,
    concordance_db,
    context_db,
    list_threadmarks_db,
    query_explain_db,
    query_terms,
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


def record(order: int, text: str) -> Threadmark:
    return Threadmark(
        order=order,
        category_id=1,
        category_name="Threadmarks",
        threadmark_id=str(order),
        post_id=str(2000 + order),
        title=f"Turn {order}",
        author="Blackstar",
        published_at="2020-01-01T00:00:00-0500",
        source_url=f"https://forums.sufficientvelocity.com/threads/example.1/#post-{2000 + order}",
        reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
        text=text,
        word_count=len(text.split()),
    )


def test_search_filters_by_threadmark_order(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba is discussed in an early turn."),
            record(2, "Cuba is discussed in a later turn."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    results = search_db(db, "Cuba", order_min=2)

    assert [result.threadmark_order for result in results] == [2]


def test_search_groups_multiple_hits_by_threadmark(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "Cuba appears here.\n\nCuba appears in a second chunk.")], jsonl)
    build_index(jsonl, db)

    grouped = search_db(db, "Cuba", grouped=True)
    all_chunks = search_db(db, "Cuba", grouped=False)

    assert len(grouped) == 1
    assert len(all_chunks) >= len(grouped)


def test_search_can_sort_results_by_timeline(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(2, "Cuba appears in a later turn. Cuba appears again."),
            record(1, "Cuba appears in an early turn."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    results = search_db(db, "Cuba", limit=2, grouped=True, sort="timeline")

    assert [result.threadmark_order for result in results] == [1, 2]


def test_search_merges_alias_terms(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba appears in the first turn."),
            record(2, "Castro appears in the second turn."),
            record(3, "No matching country here."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    results = search_db(db, "Cuba", aliases=("Castro",), sort="timeline", limit=10)
    totals = search_totals_db(db, "Cuba", aliases=("Castro",))
    terms = search_terms_db(db, "Cuba", aliases=("Castro",))

    assert [result.threadmark_order for result in results] == [1, 2]
    assert totals.total_threadmarks == 2
    assert totals.total_chunks == 2
    assert [term.query for term in terms] == ["Cuba", "Castro"]
    assert [term.total_threadmarks for term in terms] == [1, 1]
    assert terms[1].match_kind == "exact"


def test_search_ignores_unquoted_stopwords(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "The committee mentions Gosplan in passing."),
            record(2, "The committee appears without the planning agency."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    results = search_db(db, "The GosPlan", limit=5)
    totals = search_totals_db(db, "The GosPlan")

    assert [result.threadmark_order for result in results] == [1]
    assert results[0].match_query == '"GosPlan"'
    assert totals.total_threadmarks == 1
    assert totals.match_query == '"GosPlan"'


def test_search_totals_reports_prefix_fallback_counts(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Cuban trade is discussed. Cuban policy continues."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    totals = search_totals_db(db, "Cuba")

    assert totals.total_threadmarks == 2
    assert totals.total_chunks == 2
    assert totals.match_kind == "prefix"
    assert totals.match_query == '"Cuba"*'


def test_search_falls_back_to_prefix_when_exact_query_is_empty(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "Cuban exchange programs are discussed.")], jsonl)
    build_index(jsonl, db)

    results = search_db(db, "Cuba")

    assert len(results) == 1
    assert results[0].threadmark_order == 1
    assert "\x01Cuban\x02" in results[0].snippet
    assert results[0].match_kind == "prefix"
    assert "*" in results[0].match_query


def test_search_prefers_exact_results_before_prefix_fallback(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Cuba is discussed directly."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    results = search_db(db, "Cuba")

    assert [result.threadmark_order for result in results] == [2]
    assert results[0].match_kind == "exact"


def test_search_prefix_variants_include_exact_and_prefix_hits(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Cuba is discussed directly."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    default_results = search_db(db, "Cuba", grouped=True, sort="timeline")
    variant_results = search_db(db, "Cuba", grouped=True, sort="timeline", prefix_variants=True, limit=10)
    totals = search_totals_db(db, "Cuba", prefix_variants=True)
    terms = search_terms_db(db, "Cuba", prefix_variants=True)

    assert [result.threadmark_order for result in default_results] == [2]
    assert [result.threadmark_order for result in variant_results] == [1, 2]
    assert {result.match_kind for result in variant_results} == {"prefix-variants"}
    assert totals.total_threadmarks == 2
    assert totals.total_chunks == 2
    assert totals.match_kind == "prefix-variants"
    assert totals.match_query == '"Cuba"*'
    assert terms[0].match_kind == "prefix-variants"


def test_topic_comparison_reports_metadata_only_overlap(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba appears in an early turn."),
            record(2, "Cuba and communist parties are discussed together."),
            record(3, "Soviet planning is discussed elsewhere."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    comparison = topic_comparison(db, ("Cuba", "communist"), bucket_size=2)

    assert comparison.kind == "thread-search-topic-comparison"
    assert comparison.metadata_only is True
    assert [topic.query for topic in comparison.topics] == ["Cuba", "communist"]
    assert [topic.total_threadmarks for topic in comparison.topics] == [2, 1]
    assert comparison.topics[0].first_threadmark is not None
    assert comparison.topics[0].first_threadmark.threadmark_order == 1
    assert comparison.topics[0].last_threadmark is not None
    assert comparison.topics[0].last_threadmark.threadmark_order == 2
    assert comparison.topics[0].buckets[0].start_order == 1
    assert comparison.all_overlap.total_threadmarks == 1
    assert [item.threadmark_order for item in comparison.all_overlap.items] == [2]
    assert comparison.pairwise_overlaps[0].queries == ["Cuba", "communist"]
    assert comparison.pairwise_overlaps[0].total_threadmarks == 1


def test_suggest_terms_returns_indexed_prefix_terms(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba is discussed directly."),
            record(2, "Cuban exchange programs are discussed."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    suggestions = suggest_terms_db(db, "Cub", limit=10)

    assert [item.term for item in suggestions] == ["cuba", "cuban"]
    assert all(item.occurrence_count >= 1 for item in suggestions)
    assert all(item.match_kind == "prefix" for item in suggestions)
    assert all(item.edit_distance == 0 for item in suggestions)


def test_suggest_terms_falls_back_to_near_terms_when_prefix_misses(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba Cuba is discussed directly."),
            record(2, "Cuban exchange programs are discussed."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    suggestions = suggest_terms_db(db, "Cubaa", limit=10)

    assert suggestions[0].term == "cuba"
    assert suggestions[0].match_kind == "near"
    assert suggestions[0].edit_distance == 1


def test_suggest_terms_does_not_fuzzy_match_short_terms(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "Cuba is discussed directly.")], jsonl)
    build_index(jsonl, db)

    assert suggest_terms_db(db, "Cbb") == []


def test_suggest_terms_ignores_too_short_prefixes(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "Cuba is discussed directly.")], jsonl)
    build_index(jsonl, db)

    assert suggest_terms_db(db, "C") == []


def test_term_index_returns_metadata_only_vocabulary_counts(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba and Cuban trade appear together."),
            record(2, "Cuba planning and Soviet planning continue."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = term_index_db(db, prefix="Cub", limit=10)

    assert report.kind == "thread-search-term-index"
    assert report.metadata_only is True
    assert report.prefix == "cub"
    assert report.stopwords_filtered is True
    assert [(term.term, term.chunk_count) for term in report.terms] == [("cuba", 2), ("cuban", 1)]
    assert report.result_count == 2


def test_term_index_filters_stopwords_by_default(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "The Cuba topic appears and the Cuba topic repeats.")], jsonl)
    build_index(jsonl, db)

    filtered = term_index_db(db, limit=20)
    unfiltered = term_index_db(db, limit=20, include_stopwords=True)

    assert "the" not in {term.term for term in filtered.terms}
    assert "the" in {term.term for term in unfiltered.terms}


def test_query_explain_reports_exact_prefix_and_suggestions(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Castro appears separately."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = query_explain_db(db, "Cuba")

    assert report.kind == "thread-search-query-explain"
    assert report.metadata_only is True
    assert report.exact.total_threadmarks == 0
    assert report.prefix.total_threadmarks == 1
    assert report.resolved.match_kind == "prefix"
    assert [item.term for item in report.indexed_terms] == ["cuban"]
    assert [item.code for item in report.cautions] == ["exact-missing-prefix-available"]


def test_query_explain_reports_multi_term_breakdown_without_snippets(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Communist parties are discussed separately."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = query_explain_db(db, "Cuba communist")

    assert report.exact.total_threadmarks == 0
    assert report.prefix.total_threadmarks == 0
    assert [(item.query, item.exact.total_threadmarks, item.prefix.total_threadmarks) for item in report.term_breakdown] == [
        ("Cuba", 0, 1),
        ("communist", 1, 1),
    ]
    assert report.term_breakdown[0].resolved.match_kind == "prefix"
    assert report.term_breakdown[1].resolved.match_kind == "exact"
    assert [item.code for item in report.cautions] == ["individual-terms-only"]


def test_query_explain_reports_near_suggestions_when_query_misses(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "Cuba is discussed directly.")], jsonl)
    build_index(jsonl, db)

    report = query_explain_db(db, "Cubaa")

    assert report.exact.total_threadmarks == 0
    assert report.prefix.total_threadmarks == 0
    assert report.suggestions[0].term == "cuba"
    assert report.suggestions[0].match_kind == "near"
    assert [item.code for item in report.cautions] == ["near-suggestions-only"]


def test_topic_recap_returns_timeline_and_claims(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba did not turn communist in this timeline."),
            record(2, "Cuba trade policy is discussed later."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    recap = topic_recap(db, "Cuba", claim_queries=("communist",), timeline_limit=10)

    assert recap.kind == "thread-search-topic-recap"
    assert recap.bounded_retrieval_only is True
    assert recap.total_threadmarks == 2
    assert [item.threadmark_order for item in recap.timeline] == [1, 2]
    assert recap.claims[0].claim_query == "communist"
    assert recap.claims[0].evidence_level == "strong-chunk-overlap"
    assert recap.claims[0].negation_cue_evidence == 1


def test_context_returns_trimmed_retrieval_chunk(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "Cuba appears here. " * 80)], jsonl)
    build_index(jsonl, db)

    chunks = context_db(db, "Cuba", max_chars=80)

    assert len(chunks) == 1
    assert chunks[0].body.endswith("[...]")
    assert chunks[0].source_url.startswith("https://forums.sufficientvelocity.com/")


def test_context_uses_prefix_fallback_when_exact_query_is_empty(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "Cuban exchange programs are discussed.")], jsonl)
    build_index(jsonl, db)

    chunks = context_db(db, "Cuba")

    assert len(chunks) == 1
    assert "Cuban exchange" in chunks[0].body


def test_threadmark_detail_returns_full_body(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "Cuba appears here with full local text.")], jsonl)
    build_index(jsonl, db)

    detail = threadmark_detail(db, "2001")

    assert detail is not None
    assert detail.body == "Cuba appears here with full local text."
    assert detail.word_count == 7


def test_list_threadmarks_returns_metadata_without_body(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba appears in the first turn."),
            record(2, "Cuba appears in the second turn."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    items = list_threadmarks_db(db, order_min=2)

    assert len(items) == 1
    assert items[0].threadmark_order == 2
    assert items[0].title == "Turn 2"
    assert not hasattr(items[0], "body")


def test_topic_report_counts_threadmarks_and_chunks(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba appears here.\n\nCuba appears again."),
            record(2, "Cuba appears in another turn."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = topic_report(db, "Cuba")

    assert report.total_threadmarks == 2
    assert report.total_chunks >= 2
    assert report.mentions[0].hit_count >= 1
    assert report.match_kind == "exact"


def test_topic_report_merges_alias_terms(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba appears in an early turn."),
            record(2, "Castro appears in a later turn."),
            record(3, "No matching country here."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = topic_report(db, "Cuba", aliases=("Castro",), sort="timeline")

    assert report.aliases == ["Castro"]
    assert [term.query for term in report.terms] == ["Cuba", "Castro"]
    assert report.total_threadmarks == 2
    assert report.total_chunks == 2
    assert [item.threadmark_order for item in report.mentions] == [1, 2]
    assert "\x01Cuba\x02" in report.mentions[0].best_snippet
    assert "\x01Castro\x02" in report.mentions[1].best_snippet


def test_topic_report_can_sort_by_timeline(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba appears once."),
            record(2, f"Cuba appears twice. {'filler ' * 450}\n\nCuba appears again. {'filler ' * 450}"),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    coverage = topic_report(db, "Cuba")
    timeline = topic_report(db, "Cuba", sort="timeline")

    assert [item.threadmark_order for item in coverage.mentions] == [2, 1]
    assert [item.threadmark_order for item in timeline.mentions] == [1, 2]


def test_topic_coverage_returns_metadata_without_snippets(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba appears here."),
            record(2, f"Cuba appears twice. {'filler ' * 450}\n\nCuba appears again."),
            record(3, "No matching country here."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    coverage = topic_coverage(db, "Cuba", limit=1, sort="coverage", bucket_size=1)

    assert coverage.total_threadmarks == 2
    assert coverage.total_chunks >= 2
    assert coverage.match_kind == "exact"
    assert [(bucket.start_order, bucket.threadmark_count) for bucket in coverage.buckets] == [(1, 1), (2, 1)]
    assert len(coverage.items) == 1
    assert coverage.items[0].threadmark_order == 2
    assert not hasattr(coverage.items[0], "snippet")
    assert not hasattr(coverage.items[0], "body")


def test_topic_coverage_prefix_variants_include_inflected_threadmarks(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Cuba appears directly."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    default_coverage = topic_coverage(db, "Cuba", limit=10)
    variant_coverage = topic_coverage(db, "Cuba", limit=10, prefix_variants=True)

    assert [item.threadmark_order for item in default_coverage.items] == [2]
    assert [item.threadmark_order for item in variant_coverage.items] == [1, 2]
    assert variant_coverage.total_threadmarks == 2
    assert variant_coverage.match_kind == "prefix-variants"
    assert variant_coverage.match_query == '"Cuba"*'
    assert variant_coverage.terms[0].match_kind == "prefix-variants"


def test_topic_coverage_merges_aliases_and_prefix_fallback(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Castro appears in a separate update."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    coverage = topic_coverage(db, "Cuba", aliases=("Castro",), limit=10)

    assert coverage.aliases == ["Castro"]
    assert [term.query for term in coverage.terms] == ["Cuba", "Castro"]
    assert [term.match_kind for term in coverage.terms] == ["prefix", "exact"]
    assert coverage.total_threadmarks == 2
    assert coverage.buckets[0].threadmark_count == 2
    assert [item.threadmark_order for item in coverage.items] == [1, 2]


def test_claim_overlap_report_counts_threadmark_and_chunk_overlap(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba and communist theory are in one chunk."),
            record(2, f"Cuba appears here. {'filler ' * 450}\n\nCommunist appears separately."),
            record(3, "Only Cuba appears here."),
            record(4, "Only communist appears here."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = claim_overlap_report(db, "Cuba", "communist")

    assert report.topic_threadmarks == 3
    assert report.claim_threadmarks == 3
    assert report.overlapping_threadmarks == 2
    assert report.overlapping_chunks == 1
    assert report.evidence_returned == 2
    assert report.evidence_limit == 50
    assert [item.scope for item in report.evidence] == ["chunk", "threadmark"]
    assert [item.threadmark_order for item in report.evidence] == [1, 2]
    assert [item.proximity for item in report.evidence] == ["same-chunk", "separated-chunks"]
    assert report.evidence[0].chunk_distance == 0
    assert report.evidence[1].chunk_distance > 1
    assert "same indexed chunk" in report.evidence[0].proximity_note
    assert "indexed chunks apart" in report.evidence[1].proximity_note


def test_claim_overlap_report_uses_closest_chunk_pair_in_threadmark(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(
                1,
                "\n\n".join(
                    [
                        "Cuba appears in an early overview.",
                        "filler " * 360,
                        "A later note says Cuba did not turn communist in this timeline.",
                    ]
                ),
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = claim_overlap_report(db, "Cuba", "communist")
    claim = claim_check_report(db, "Cuba", "communist")

    assert report.overlapping_chunks == 1
    assert report.evidence[0].scope == "chunk"
    assert report.evidence[0].proximity == "same-chunk"
    assert report.evidence[0].chunk_distance == 0
    assert "\x01Cuba\x02" in report.evidence[0].topic_snippet
    assert "\x01communist\x02" in report.evidence[0].claim_snippet
    assert claim.evidence_level == "strong-chunk-overlap"
    assert "did not" in claim.evidence[0].claim_negation_cues


def test_claim_overlap_report_exposes_evidence_limit(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba and communist theory appear together."),
            record(2, "Cuba and communist theory appear together again."),
            record(3, "Cuba and communist theory appear together later."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = claim_overlap_report(db, "Cuba", "communist", limit=2)

    assert report.overlapping_threadmarks == 3
    assert report.evidence_returned == 2
    assert report.evidence_limit == 2
    assert [item.threadmark_order for item in report.evidence] == [1, 2]


def test_claim_overlap_report_reports_zero_overlap(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Communist theory is discussed separately."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = claim_overlap_report(db, "Cuba", "communist")

    assert report.topic_threadmarks == 1
    assert report.claim_threadmarks == 1
    assert report.overlapping_threadmarks == 0
    assert report.overlapping_chunks == 0
    assert report.topic_match_kind == "prefix"
    assert report.claim_match_kind == "exact"
    assert report.evidence == []


def test_claim_overlap_report_merges_topic_aliases(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Castro and communist theory appear together."),
            record(2, "Cuban exchange programs are discussed separately."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = claim_overlap_report(db, "Cuba", "communist", topic_aliases=("Castro",))

    assert report.topic_aliases == ["Castro"]
    assert [term.query for term in report.topic_terms] == ["Cuba", "Castro"]
    assert [term.match_kind for term in report.topic_terms] == ["prefix", "exact"]
    assert report.topic_threadmarks == 2
    assert report.claim_threadmarks == 1
    assert report.overlapping_threadmarks == 1
    assert report.overlapping_chunks == 1
    assert report.evidence[0].threadmark_order == 1


def test_claim_check_prefix_variants_can_expand_topic_side(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba appears directly in an unrelated note."),
            record(2, "Cuban policy did not become communist in this timeline."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    default_claim = claim_check_report(db, "Cuba", "communist")
    variant_claim = claim_check_report(db, "Cuba", "communist", prefix_variants=True)

    assert default_claim.evidence_level == "no-overlap"
    assert default_claim.topic_match_kind == "exact"
    assert default_claim.topic_query_exact_threadmarks == 1
    assert default_claim.topic_query_exact_chunks == 1
    assert variant_claim.topic_threadmarks == 2
    assert variant_claim.topic_match_kind == "prefix-variants"
    assert variant_claim.topic_match_query == '"Cuba"*'
    assert variant_claim.topic_query_exact_threadmarks == 1
    assert variant_claim.topic_query_exact_chunks == 1
    assert variant_claim.evidence_level == "strong-chunk-overlap"
    assert variant_claim.negation_cue_evidence == 1


def test_claim_check_classifies_same_chunk_overlap(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "Cuba and communist theory are in one chunk.")], jsonl)
    build_index(jsonl, db)

    report = claim_check_report(db, "Cuba", "communist")

    assert report.evidence_level == "strong-chunk-overlap"
    assert "Strong overlap" in report.assessment
    assert report.overlapping_chunks == 1
    assert report.evidence_returned == 1
    assert report.evidence_limit == 25
    assert report.claim_threadmarks == 1
    assert report.topic_match_kind == "exact"
    assert report.claim_match_kind == "exact"
    assert report.topic_query_exact_threadmarks == 1
    assert report.topic_query_exact_chunks == 1
    assert report.claim_query_exact_threadmarks == 1
    assert report.claim_query_exact_chunks == 1
    assert report.negation_cue_evidence == 0
    assert report.evidence[0].claim_negation_cues == ()
    assert report.cautions == []


def test_claim_check_reports_negation_cues_near_claim_terms(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "Cuba did not turn communist in this timeline.")], jsonl)
    build_index(jsonl, db)

    report = claim_check_report(db, "Cuba", "communist")

    assert report.evidence_level == "strong-chunk-overlap"
    assert report.negation_cue_evidence == 1
    assert "did not" in report.evidence[0].claim_negation_cues
    assert "triage hint" in report.negation_cue_note
    assert [item.code for item in report.cautions] == ["negation-cues"]


def test_claim_check_merges_topic_aliases(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Castro and communist theory appear together."),
            record(2, "Cuban exchange programs are discussed separately."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = claim_check_report(db, "Cuba", "communist", topic_aliases=("Castro",))

    assert report.topic_aliases == ["Castro"]
    assert [term.query for term in report.topic_terms] == ["Cuba", "Castro"]
    assert [term.query for term in report.claim_terms] == ["communist"]
    assert report.evidence_level == "strong-chunk-overlap"
    assert report.topic_threadmarks == 2
    assert report.overlapping_chunks == 1


def test_claim_check_reports_prefix_fallback_per_side(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "Cuban exchange programs and communist theory are discussed.")], jsonl)
    build_index(jsonl, db)

    report = claim_check_report(db, "Cuba", "communist")

    assert report.evidence_level == "strong-chunk-overlap"
    assert report.topic_match_kind == "prefix"
    assert report.claim_match_kind == "exact"
    assert "*" in report.topic_match_query
    assert report.topic_query_exact_threadmarks == 0
    assert report.topic_query_exact_chunks == 0
    assert report.claim_query_exact_threadmarks == 1
    assert report.claim_query_exact_chunks == 1
    assert [item.code for item in report.cautions] == ["topic-prefix-match", "topic-exact-missing"]


def test_claim_check_classifies_threadmark_only_overlap(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, f"Cuba appears here. {'filler ' * 450}\n\nCommunist appears separately.")], jsonl)
    build_index(jsonl, db)

    report = claim_check_report(db, "Cuba", "communist")

    assert report.evidence_level == "weak-threadmark-overlap"
    assert report.overlapping_threadmarks == 1
    assert report.overlapping_chunks == 0
    assert [item.code for item in report.cautions] == ["threadmark-only"]


def test_claim_check_classifies_adjacent_chunk_overlap(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(
                1,
                "\n\n".join(
                    [
                        "Cuba " + ("filler " * 342),
                        "Communist appears in the next chunk.",
                    ]
                ),
            )
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = claim_check_report(db, "Cuba", "communist")

    assert report.evidence_level == "adjacent-chunk-overlap"
    assert "Adjacent overlap" in report.assessment
    assert report.overlapping_threadmarks == 1
    assert report.overlapping_chunks == 0
    assert report.evidence[0].proximity == "adjacent-chunk"
    assert report.evidence[0].chunk_distance == 1
    assert [item.code for item in report.cautions] == ["adjacent-only"]


def test_claim_check_classifies_no_overlap(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba appears here."),
            record(2, "Communist theory appears elsewhere."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = claim_check_report(db, "Cuba", "communist")

    assert report.evidence_level == "no-overlap"
    assert report.topic_threadmarks == 1
    assert report.claim_threadmarks == 1
    assert [item.code for item in report.cautions] == ["no-overlap"]


def test_claim_check_classifies_missing_terms(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "Cuba appears here.")], jsonl)
    build_index(jsonl, db)

    report = claim_check_report(db, "missingtopic", "missingclaim")

    assert report.evidence_level == "missing-both"
    assert report.topic_threadmarks == 0
    assert [item.code for item in report.cautions] == ["missing-query-side"]
    assert report.claim_threadmarks == 0


def test_concordance_returns_bounded_mention_windows(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Before Cuba appears here. After.\n\nCuba appears again in another paragraph."),
            record(2, "No matching country here."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = concordance_db(db, "Cuba", window_chars=36)

    assert report.total_threadmarks == 1
    assert report.total_mentions == 2
    assert len(report.mentions) == 2
    assert "\x01Cuba\x02" in report.mentions[0].snippet
    assert report.mentions[0].threadmark_order == 1


def test_concordance_respects_limit_while_counting_mentions(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, f"Cuba one. {'filler ' * 20}Cuba two. {'detail ' * 20}Cuba three.")], jsonl)
    build_index(jsonl, db)

    report = concordance_db(db, "Cuba", limit=1)

    assert report.total_mentions == 3
    assert len(report.mentions) == 1


def test_concordance_collapses_nearby_mentions(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "Cuba one. Cuba two. Cuba three.")], jsonl)
    build_index(jsonl, db)

    report = concordance_db(db, "Cuba", sort="timeline")

    assert report.total_mentions == 1
    assert len(report.mentions) == 1
    assert report.mentions[0].snippet.count("\x01Cuba\x02") == 3


def test_concordance_supports_quoted_phrase_windows(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "The notes say Cuba remains outside that alignment.")], jsonl)
    build_index(jsonl, db)

    report = concordance_db(db, '"Cuba remains"')

    assert report.total_mentions == 1
    assert "\x01Cuba remains\x02" in report.mentions[0].snippet


def test_concordance_uses_fts_snippet_for_prefix_fallback(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "Cuban exchange programs are discussed.")], jsonl)
    build_index(jsonl, db)

    report = concordance_db(db, "Cuba")

    assert report.total_threadmarks == 1
    assert report.total_mentions == 1
    assert "\x01Cuban\x02" in report.mentions[0].snippet
    assert report.match_kind == "prefix"


def test_concordance_can_sort_by_timeline(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba appears in the first turn."),
            record(2, f"Cuba appears twice. {'filler ' * 20}Cuba appears again."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = concordance_db(db, "Cuba", sort="timeline")

    assert [mention.threadmark_order for mention in report.mentions] == [1, 2, 2]


def test_concordance_merges_alias_terms(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba appears in the first turn."),
            record(2, "Castro appears in the second turn."),
            record(3, "No matching country here."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    report = concordance_db(db, "Cuba", aliases=("Castro",), sort="timeline")

    assert report.aliases == ["Castro"]
    assert [term.query for term in report.terms] == ["Cuba", "Castro"]
    assert report.total_threadmarks == 2
    assert report.total_mentions == 2
    assert [mention.threadmark_order for mention in report.mentions] == [1, 2]
    assert "\x01Cuba\x02" in report.mentions[0].snippet
    assert "\x01Castro\x02" in report.mentions[1].snippet


def test_topic_dossier_bundles_coverage_and_mentions(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba sugar policy."),
            record(2, "Cuba trade policy. Cuba policy again."),
            record(3, "No matching country here."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    dossier = topic_dossier(db, "Cuba", threadmark_limit=10, mention_limit=10)

    assert dossier.query == "Cuba"
    assert dossier.match_kind == "exact"
    assert dossier.total_threadmarks == 2
    assert dossier.total_mentions == 2
    assert [item.threadmark_order for item in dossier.threadmarks] == [1, 2]
    assert [item.threadmark_order for item in dossier.timeline] == [1, 2]
    assert dossier.mention_windows == []


def test_topic_dossier_totals_are_not_limited_by_display_caps(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuba sugar policy."),
            record(2, f"Cuba trade policy. {'filler ' * 20}Cuba policy again."),
            record(3, "No matching country here."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    dossier = topic_dossier(db, "Cuba", threadmark_limit=1, mention_limit=1)

    assert dossier.total_threadmarks == 2
    assert dossier.total_mentions == 3
    assert len(dossier.threadmarks) == 1
    assert len(dossier.timeline) == 1
    assert dossier.mention_windows == []


def test_topic_dossier_reports_prefix_fallback(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record(1, "Cuban exchange programs are discussed.")], jsonl)
    build_index(jsonl, db)

    dossier = topic_dossier(db, "Cuba")

    assert dossier.match_kind == "prefix"
    assert "*" in dossier.match_query
    assert dossier.total_threadmarks == 1
    assert "\x01Cuban\x02" in dossier.timeline[0].snippet


def test_topic_dossier_can_merge_alias_terms(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Castro appears in a separate update."),
            record(3, "No matching country here."),
        ],
        jsonl,
    )
    build_index(jsonl, db)

    dossier = topic_dossier(db, "Cuba", aliases=("Castro",), threadmark_limit=10, mention_limit=10)

    assert dossier.aliases == ["Castro"]
    assert [term.query for term in dossier.terms] == ["Cuba", "Castro"]
    assert [term.match_kind for term in dossier.terms] == ["prefix", "exact"]
    assert dossier.total_threadmarks == 2
    assert [item.threadmark_order for item in dossier.threadmarks] == [1, 2]
    assert [item.threadmark_order for item in dossier.timeline] == [1, 2]
    assert dossier.mention_windows == []


def test_query_terms_keep_quoted_phrases_and_words_once() -> None:
    assert query_terms('"Cuba remains" Cuba Cuba') == ["Cuba remains", "Cuba"]
    assert query_terms('"The GosPlan" The GosPlan') == ["The GosPlan", "GosPlan"]
    assert query_terms("The GosPlan of the VSNKh") == ["GosPlan", "VSNKh"]
