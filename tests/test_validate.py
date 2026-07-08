from pathlib import Path

from planquest.models import Threadmark
from planquest.scrape import write_jsonl
from planquest.indexer import build_index
from planquest.validate import validate_corpus, validate_launch_ready


def make_record(order: int, category_id: int = 1) -> Threadmark:
    return Threadmark(
        order=order,
        category_id=category_id,
        category_name="Threadmarks",
        threadmark_id=str(order),
        post_id=str(1000 + order),
        title=f"Turn {order}",
        author="Blackstar",
        published_at="2020-01-01T00:00:00-0500",
        source_url=f"https://forums.sufficientvelocity.com/threads/example.1/#post-{1000 + order}",
        reader_url="https://forums.sufficientvelocity.com/threads/example.1/reader/",
        text="Cuba is mentioned in this validation fixture.",
        word_count=8,
    )


def test_validate_corpus_accepts_expected_main_threadmarks(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    write_jsonl([make_record(1), make_record(2)], path)

    result = validate_corpus(path, expected_threadmarks=2)

    assert result.ok
    assert not result.errors


def test_validate_corpus_rejects_excluded_category(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    write_jsonl([make_record(1), make_record(2, category_id=5)], path)

    result = validate_corpus(path, expected_threadmarks=2)

    assert not result.ok
    assert any("excluded categories" in error for error in result.errors)


def test_launch_check_accepts_public_snippet_mode(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([make_record(1), make_record(2)], jsonl)
    build_index(jsonl, db)

    result = validate_launch_ready(jsonl, db, expected_threadmarks=2, probes=("Cuba",))

    assert result.ok
    assert any("public full-text routes: disabled" in check for check in result.checks)


def test_launch_check_rejects_private_fulltext_for_public_launch(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    db = tmp_path / "records.sqlite"
    write_jsonl([make_record(1)], jsonl)
    build_index(jsonl, db)

    result = validate_launch_ready(jsonl, db, expected_threadmarks=1, private_fulltext=True, probes=("Cuba",))

    assert not result.ok
    assert any("private-fulltext" in error for error in result.errors)
