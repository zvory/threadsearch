from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from .db import connect_readonly
from .scrape import read_jsonl


def corpus_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    try:
        records = read_jsonl(path)
    except Exception as exc:
        return {"exists": True, "path": str(path), "ok": False, "error": str(exc)}

    categories = sorted({record.category_id for record in records})
    words = sum(record.word_count for record in records)
    first = records[0] if records else None
    last = records[-1] if records else None
    return {
        "exists": True,
        "ok": True,
        "path": str(path),
        "threadmarks": len(records),
        "words": words,
        "categories": categories,
        "first": threadmark_brief(first),
        "last": threadmark_brief(last),
    }


def db_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    try:
        with connect_readonly(path) as conn:
            threadmarks = int(conn.execute("SELECT COUNT(*) FROM threadmarks").fetchone()[0])
            chunks = int(conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0])
            stored_chunks = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
            words = int(conn.execute("SELECT COALESCE(SUM(word_count), 0) FROM threadmarks").fetchone()[0])
            categories = [
                int(row[0])
                for row in conn.execute("SELECT DISTINCT category_id FROM threadmarks ORDER BY category_id").fetchall()
            ]
    except sqlite3.Error as exc:
        return {"exists": True, "path": str(path), "ok": False, "error": str(exc)}

    return {
        "exists": True,
        "ok": True,
        "path": str(path),
        "threadmarks": threadmarks,
        "chunks": chunks,
        "stored_chunks": stored_chunks,
        "words": words,
        "categories": categories,
    }


def fetch_log_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}

    total = 0
    pages = 0
    robots = 0
    bytes_total = 0
    first: dict[str, Any] | None = None
    last: dict[str, Any] | None = None
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                receipt = json.loads(line)
                total += 1
                if receipt.get("kind") == "page":
                    pages += 1
                elif receipt.get("kind") == "robots":
                    robots += 1
                bytes_total += int(receipt.get("bytes") or 0)
                brief = fetch_receipt_brief(receipt)
                if first is None:
                    first = brief
                last = brief
    except Exception as exc:
        return {"exists": True, "path": str(path), "ok": False, "error": str(exc)}

    return {
        "exists": True,
        "ok": True,
        "path": str(path),
        "entries": total,
        "page_fetches": pages,
        "robots_fetches": robots,
        "bytes": bytes_total,
        "first": first,
        "last": last,
    }


def fetch_receipt_brief(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "fetched_at_utc": receipt.get("fetched_at_utc"),
        "kind": receipt.get("kind"),
        "url": receipt.get("url"),
        "cache_path": receipt.get("cache_path"),
        "bytes": receipt.get("bytes"),
    }


def threadmark_brief(record: Any | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "order": record.order,
        "post_id": record.post_id,
        "title": record.title,
        "source_url": record.source_url,
    }
