from pathlib import Path
import json

from planquest.indexer import build_index
from planquest.models import Threadmark
from planquest.scrape import write_jsonl
from planquest.status import corpus_summary, db_summary, fetch_log_summary


def record() -> Threadmark:
    return Threadmark(
        order=1,
        category_id=1,
        category_name="Threadmarks",
        threadmark_id="1",
        post_id="1001",
        title="Turn 1",
        author="Blackstar",
        published_at=None,
        source_url="https://forums.sufficientvelocity.com/threads/example.1/#post-1001",
        reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
        text="Cuba appears here.",
        word_count=3,
    )


def test_corpus_summary_reports_record_counts(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    write_jsonl([record()], path)

    summary = corpus_summary(path)

    assert summary["exists"] is True
    assert summary["threadmarks"] == 1
    assert summary["words"] == 3
    assert summary["categories"] == [1]
    assert summary["first"]["title"] == "Turn 1"


def test_db_summary_reports_index_counts(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([record()], jsonl)
    build_index(jsonl, db)

    summary = db_summary(db)

    assert summary["exists"] is True
    assert summary["threadmarks"] == 1
    assert summary["chunks"] == 1
    assert summary["stored_chunks"] == 1


def test_fetch_log_summary_counts_receipts(tmp_path: Path) -> None:
    path = tmp_path / "fetch-log.jsonl"
    receipts = [
        {"fetched_at_utc": "2026-07-08T00:00:00Z", "kind": "robots", "url": "https://example.invalid/robots.txt", "bytes": 12},
        {"fetched_at_utc": "2026-07-08T00:01:00Z", "kind": "page", "url": "https://example.invalid/reader/", "bytes": 34},
    ]
    path.write_text("\n".join(json.dumps(receipt) for receipt in receipts) + "\n", encoding="utf-8")

    summary = fetch_log_summary(path)

    assert summary["exists"] is True
    assert summary["entries"] == 2
    assert summary["robots_fetches"] == 1
    assert summary["page_fetches"] == 1
    assert summary["bytes"] == 46
    assert summary["last"]["url"] == "https://example.invalid/reader/"
