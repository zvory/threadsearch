from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sqlite3

from .config import DEFAULT_READINESS_PROBES
from .db import connect_readonly
from .models import Threadmark
from .scrape import read_jsonl
from .search import search_db


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    checks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def validate_corpus(
    jsonl_path: Path,
    db_path: Path | None = None,
    expected_threadmarks: int | None = 269,
    expected_category: int = 1,
    excluded_categories: tuple[int, ...] = (4, 5),
    probes: tuple[str, ...] = (),
) -> ValidationResult:
    checks: list[str] = []
    errors: list[str] = []

    if not jsonl_path.exists():
        return ValidationResult(ok=False, errors=[f"missing extracted corpus: {jsonl_path}"])

    try:
        records = read_jsonl(jsonl_path)
    except Exception as exc:
        return ValidationResult(ok=False, errors=[f"could not read {jsonl_path}: {exc}"])

    checks.append(f"jsonl records: {len(records)}")
    errors.extend(validate_records(records, expected_threadmarks, expected_category, excluded_categories))

    if db_path is not None:
        if not db_path.exists():
            errors.append(f"missing sqlite index: {db_path}")
        else:
            db_checks, db_errors = validate_db(db_path, len(records), expected_category, excluded_categories, probes)
            checks.extend(db_checks)
            errors.extend(db_errors)

    return ValidationResult(ok=not errors, checks=checks, errors=errors)


def validate_launch_ready(
    jsonl_path: Path,
    db_path: Path,
    expected_threadmarks: int = 269,
    expected_category: int = 1,
    excluded_categories: tuple[int, ...] = (4, 5),
    probes: tuple[str, ...] = DEFAULT_READINESS_PROBES,
    private_fulltext: bool = False,
    db_only: bool = False,
) -> ValidationResult:
    checks: list[str] = []
    errors: list[str] = []

    if private_fulltext:
        errors.append("public launch must not enable --private-fulltext")
    else:
        checks.append("public full-text routes: disabled")

    if db_only:
        if not db_path.exists():
            errors.append(f"missing sqlite index: {db_path}")
        else:
            db_checks, db_errors = validate_db(
                db_path,
                expected_records=expected_threadmarks,
                expected_category=expected_category,
                excluded_categories=excluded_categories,
                probes=probes,
            )
            checks.extend(db_checks)
            errors.extend(db_errors)
    else:
        result = validate_corpus(
            jsonl_path=jsonl_path,
            db_path=db_path,
            expected_threadmarks=expected_threadmarks,
            expected_category=expected_category,
            excluded_categories=excluded_categories,
            probes=probes,
        )
        checks.extend(result.checks)
        errors.extend(result.errors)

    return ValidationResult(ok=not errors, checks=checks, errors=errors)


def validate_records(
    records: list[Threadmark],
    expected_threadmarks: int | None,
    expected_category: int,
    excluded_categories: tuple[int, ...],
) -> list[str]:
    errors: list[str] = []

    if expected_threadmarks is not None and len(records) != expected_threadmarks:
        errors.append(f"expected {expected_threadmarks} threadmarks, found {len(records)}")

    post_ids = [record.post_id for record in records]
    duplicate_posts = sorted({post_id for post_id in post_ids if post_ids.count(post_id) > 1})
    if duplicate_posts:
        errors.append(f"duplicate post ids: {', '.join(duplicate_posts[:10])}")

    expected_orders = list(range(1, len(records) + 1))
    actual_orders = [record.order for record in records]
    if actual_orders != expected_orders:
        errors.append("threadmark order is not contiguous from 1")

    wrong_categories = sorted({record.category_id for record in records if record.category_id != expected_category})
    if wrong_categories:
        errors.append(f"unexpected category ids in extracted corpus: {wrong_categories}")

    present_excluded = sorted({record.category_id for record in records if record.category_id in excluded_categories})
    if present_excluded:
        errors.append(f"excluded categories present in extracted corpus: {present_excluded}")

    empty_posts = [record.post_id for record in records if not record.text.strip()]
    if empty_posts:
        errors.append(f"empty extracted text for posts: {', '.join(empty_posts[:10])}")

    missing_urls = [record.post_id for record in records if "forums.sufficientvelocity.com" not in record.source_url]
    if missing_urls:
        errors.append(f"source URLs missing SV host for posts: {', '.join(missing_urls[:10])}")

    return errors


def validate_db(
    db_path: Path,
    expected_records: int,
    expected_category: int,
    excluded_categories: tuple[int, ...],
    probes: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    checks: list[str] = []
    errors: list[str] = []

    try:
        with connect_readonly(db_path) as conn:
            threadmarks = int(conn.execute("SELECT COUNT(*) FROM threadmarks").fetchone()[0])
            chunks = int(conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0])
            stored_chunks = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
            categories = [
                int(row[0])
                for row in conn.execute("SELECT DISTINCT category_id FROM threadmarks ORDER BY category_id").fetchall()
            ]
    except sqlite3.Error as exc:
        return checks, [f"could not inspect sqlite index {db_path}: {exc}"]

    checks.append(f"db threadmarks: {threadmarks}")
    checks.append(f"db chunks: {chunks}")
    checks.append(f"db stored chunks: {stored_chunks}")
    checks.append(f"db categories: {categories}")

    if threadmarks != expected_records:
        errors.append(f"sqlite threadmarks mismatch: expected {expected_records}, found {threadmarks}")
    if chunks < threadmarks:
        errors.append(f"sqlite chunks look incomplete: {chunks} chunks for {threadmarks} threadmarks")
    if stored_chunks != chunks:
        errors.append(f"sqlite chunk storage mismatch: {stored_chunks} stored chunks vs {chunks} FTS chunks")
    if categories != [expected_category]:
        errors.append(f"sqlite contains unexpected category ids: {categories}")

    present_excluded = [category for category in categories if category in excluded_categories]
    if present_excluded:
        errors.append(f"excluded categories present in sqlite index: {present_excluded}")

    for probe in probes:
        results = search_db(db_path, probe, limit=1)
        checks.append(f"probe {probe!r}: {len(results)} result(s)")
        if not results:
            errors.append(f"probe search returned no results: {probe!r}")

    return checks, errors
