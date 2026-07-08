from __future__ import annotations

from pathlib import Path
import sqlite3


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite database for reads only."""
    return sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
