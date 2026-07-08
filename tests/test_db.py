from pathlib import Path
import sqlite3

import pytest

from planquest.db import connect_readonly


def make_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE items (name TEXT NOT NULL)")
        conn.execute("INSERT INTO items (name) VALUES ('Cuba')")
        conn.commit()


def test_connect_readonly_reads_existing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    make_db(db_path)

    with connect_readonly(db_path) as conn:
        row = conn.execute("SELECT name FROM items").fetchone()

    assert row == ("Cuba",)


def test_connect_readonly_rejects_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    make_db(db_path)

    with connect_readonly(db_path) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO items (name) VALUES ('Soviet')")


def test_connect_readonly_does_not_create_missing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.sqlite"

    with pytest.raises(sqlite3.OperationalError):
        connect_readonly(db_path)

    assert not db_path.exists()
