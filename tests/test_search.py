from pathlib import Path

from planquest.indexer import build_index
from planquest.models import Threadmark
from planquest.scrape import write_jsonl
from planquest.search import (
    list_threadmarks_db,
    search_db,
    search_totals_db,
    threadmark_detail,
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
    db = build_db(
        tmp_path,
        [
            record(1, "Cuba is discussed in an early turn."),
            record(2, "Cuba is discussed in a later turn."),
        ],
    )

    results = search_db(db, "Cuba", order_min=2)

    assert [result.threadmark_order for result in results] == [2]


def test_search_groups_multiple_hits_by_threadmark(tmp_path: Path) -> None:
    db = build_db(tmp_path, [record(1, "Cuba appears here.\n\nCuba appears in a second chunk.")])

    grouped = search_db(db, "Cuba", grouped=True)
    all_chunks = search_db(db, "Cuba", grouped=False)

    assert len(grouped) == 1
    assert len(all_chunks) >= len(grouped)


def test_search_can_sort_results_by_timeline(tmp_path: Path) -> None:
    db = build_db(
        tmp_path,
        [
            record(2, "Cuba appears in a later turn. Cuba appears again."),
            record(1, "Cuba appears in an early turn."),
        ],
    )

    results = search_db(db, "Cuba", limit=2, grouped=True, sort="timeline")

    assert [result.threadmark_order for result in results] == [1, 2]


def test_search_ignores_unquoted_stopwords(tmp_path: Path) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "The committee mentions Gosplan in passing."),
            record(2, "The committee appears without the planning agency."),
        ],
    )

    results = search_db(db, "The GosPlan", limit=5)
    totals = search_totals_db(db, "The GosPlan")

    assert [result.threadmark_order for result in results] == [1]
    assert results[0].match_query == '"GosPlan"'
    assert totals.total_threadmarks == 1
    assert totals.match_query == '"GosPlan"'


def test_search_totals_reports_prefix_fallback_counts(tmp_path: Path) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Cuban trade is discussed. Cuban policy continues."),
        ],
    )

    totals = search_totals_db(db, "Cuba")

    assert totals.total_threadmarks == 2
    assert totals.total_chunks == 2
    assert totals.match_kind == "prefix"
    assert totals.match_query == '"Cuba"*'


def test_search_falls_back_to_prefix_when_exact_query_is_empty(tmp_path: Path) -> None:
    db = build_db(tmp_path, [record(1, "Cuban exchange programs are discussed.")])

    results = search_db(db, "Cuba")

    assert len(results) == 1
    assert results[0].threadmark_order == 1
    assert "\x01Cuban\x02" in results[0].snippet
    assert results[0].match_kind == "prefix"
    assert "*" in results[0].match_query


def test_search_prefers_exact_results_before_prefix_fallback(tmp_path: Path) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Cuba is discussed directly."),
        ],
    )

    results = search_db(db, "Cuba")

    assert [result.threadmark_order for result in results] == [2]
    assert results[0].match_kind == "exact"


def test_search_prefix_variants_include_exact_and_prefix_hits(tmp_path: Path) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuban exchange programs are discussed."),
            record(2, "Cuba is discussed directly."),
        ],
    )

    default_results = search_db(db, "Cuba", grouped=True, sort="timeline")
    variant_results = search_db(db, "Cuba", grouped=True, sort="timeline", prefix_variants=True, limit=10)
    totals = search_totals_db(db, "Cuba", prefix_variants=True)

    assert [result.threadmark_order for result in default_results] == [2]
    assert [result.threadmark_order for result in variant_results] == [1, 2]
    assert {result.match_kind for result in variant_results} == {"prefix-variants"}
    assert totals.total_threadmarks == 2
    assert totals.total_chunks == 2
    assert totals.match_kind == "prefix-variants"
    assert totals.match_query == '"Cuba"*'


def test_threadmark_detail_returns_full_body(tmp_path: Path) -> None:
    db = build_db(tmp_path, [record(1, "Cuba appears here with full local text.")])

    detail = threadmark_detail(db, "2001")

    assert detail is not None
    assert detail.body == "Cuba appears here with full local text."
    assert detail.word_count == 7


def test_list_threadmarks_returns_metadata_without_body(tmp_path: Path) -> None:
    db = build_db(
        tmp_path,
        [
            record(1, "Cuba appears in the first turn."),
            record(2, "Cuba appears in the second turn."),
        ],
    )

    items = list_threadmarks_db(db, order_min=2)

    assert len(items) == 1
    assert items[0].threadmark_order == 2
    assert items[0].title == "Turn 2"
    assert not hasattr(items[0], "body")


def build_db(tmp_path: Path, records: list[Threadmark]) -> Path:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl(records, jsonl)
    build_index(jsonl, db)
    return db
