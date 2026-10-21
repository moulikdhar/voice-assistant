

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Iterable

from . import config

DB_PATH = config.ROOT_DIR / "data" / "claims.db"
DB_PATH.parent.mkdir(exist_ok=True)

_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.execute("PRAGMA journal_mode=WAL")    # much faster writes on Windows; reads never block writes
_conn.execute("PRAGMA synchronous=NORMAL")
_conn.execute(
    "CREATE TABLE IF NOT EXISTS docs ("
    " collection TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,"
    " PRIMARY KEY (collection, id))"
)
_conn.commit()


def put(collection: str, doc_id: str, data: dict) -> dict:
    with _lock:
        _conn.execute(
            "INSERT OR REPLACE INTO docs (collection, id, data) VALUES (?, ?, ?)",
            (collection, doc_id, json.dumps(data, ensure_ascii=False)),
        )
        _conn.commit()
    return data


def get(collection: str, doc_id: str) -> dict | None:
    with _lock:
        row = _conn.execute(
            "SELECT data FROM docs WHERE collection = ? AND id = ?", (collection, doc_id)
        ).fetchone()
    return json.loads(row[0]) if row else None


def all(collection: str) -> list[dict]:  # noqa: A001 (simple name on purpose)
    with _lock:
        rows = _conn.execute(
            "SELECT data FROM docs WHERE collection = ? ORDER BY rowid", (collection,)
        ).fetchall()
    return [json.loads(r[0]) for r in rows]


def find(collection: str, **match: Any) -> list[dict]:
    return [d for d in all(collection) if all_match(d, match)]


def all_match(doc: dict, match: dict) -> bool:
    return all_items(doc.get(k) == v for k, v in match.items())


def all_items(it: Iterable[bool]) -> bool:
    for x in it:
        if not x:
            return False
    return True


def delete(collection: str, doc_id: str) -> None:
    with _lock:
        _conn.execute("DELETE FROM docs WHERE collection = ? AND id = ?", (collection, doc_id))
        _conn.commit()


def replace_text(old: str, new: str) -> None:
    """Rename text inside every stored document (used for a one-time demo rename)."""
    with _lock:
        _conn.execute("UPDATE docs SET data = REPLACE(data, ?, ?) WHERE data LIKE ?", (old, new, f"%{old}%"))
        _conn.commit()


def wipe() -> None:
    with _lock:
        _conn.execute("DELETE FROM docs")
        _conn.commit()
