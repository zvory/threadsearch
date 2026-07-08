from __future__ import annotations

from pathlib import Path
import re
import sqlite3
from typing import Iterable

from .models import Threadmark
from .scrape import read_jsonl


def build_index(jsonl_path: Path, db_path: Path) -> tuple[int, int]:
    records = read_jsonl(jsonl_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        create_schema(conn)
        insert_records(conn, records)
        conn.commit()
    chunk_count = sum(len(chunk_text(record.text)) for record in records)
    return len(records), chunk_count


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS metadata;
        DROP TABLE IF EXISTS threadmarks;
        DROP TABLE IF EXISTS chunks;
        DROP TABLE IF EXISTS terms_vocab;
        DROP TABLE IF EXISTS chunks_fts;

        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE threadmarks (
            post_id TEXT PRIMARY KEY,
            threadmark_order INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            category_name TEXT NOT NULL,
            threadmark_id TEXT,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            published_at TEXT,
            source_url TEXT NOT NULL,
            reader_url TEXT NOT NULL,
            body TEXT NOT NULL,
            word_count INTEGER NOT NULL
        );

        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY,
            post_id TEXT NOT NULL,
            threadmark_order INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            body TEXT NOT NULL,
            FOREIGN KEY (post_id) REFERENCES threadmarks(post_id)
        );

        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            title,
            body,
            post_id UNINDEXED,
            threadmark_order UNINDEXED,
            chunk_index UNINDEXED,
            author UNINDEXED,
            published_at UNINDEXED,
            source_url UNINDEXED,
            tokenize='porter unicode61'
        );

        CREATE VIRTUAL TABLE terms_vocab USING fts5vocab(chunks_fts, 'row');
        """
    )


def insert_records(conn: sqlite3.Connection, records: Iterable[Threadmark]) -> None:
    conn.execute("INSERT INTO metadata(key, value) VALUES (?, ?)", ("schema_version", "1"))
    for record in records:
        conn.execute(
            """
            INSERT INTO threadmarks (
                post_id, threadmark_order, category_id, category_name, threadmark_id,
                title, author, published_at, source_url, reader_url, body, word_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.post_id,
                record.order,
                record.category_id,
                record.category_name,
                record.threadmark_id,
                record.title,
                record.author,
                record.published_at,
                record.source_url,
                record.reader_url,
                record.text,
                record.word_count,
            ),
        )
        for index, chunk in enumerate(chunk_text(record.text), start=1):
            cursor = conn.execute(
                """
                INSERT INTO chunks (post_id, threadmark_order, chunk_index, body)
                VALUES (?, ?, ?, ?)
                """,
                (record.post_id, record.order, index, chunk),
            )
            conn.execute(
                """
                INSERT INTO chunks_fts (
                    rowid,
                    title, body, post_id, threadmark_order, chunk_index,
                    author, published_at, source_url
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cursor.lastrowid,
                    record.title,
                    chunk,
                    record.post_id,
                    record.order,
                    index,
                    record.author,
                    record.published_at,
                    record.source_url,
                ),
            )


def chunk_text(text: str, max_chars: int = 2400) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n{2,}", text) if paragraph.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            chunks.extend(split_long_text(paragraph, max_chars=max_chars))
            continue

        added_len = len(paragraph) + (2 if current else 0)
        if current and current_len + added_len > max_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0

        current.append(paragraph)
        current_len += added_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def split_long_text(text: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            sentence_break = text.rfind(". ", start, end)
            if sentence_break > start + max_chars // 2:
                end = sentence_break + 1
        pieces.append(text[start:end].strip())
        start = end
    return [piece for piece in pieces if piece]
