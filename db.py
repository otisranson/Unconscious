"""
Unconscious — A psychotechnical approach to AI
Copyright 2026 Otis Ranson. Licensed under the Apache License, Version 2.0.

db.py — SQLite interface. Every other module reads and writes the corpus
through the functions here; nothing else touches unconscious.db directly.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "unconscious.db"

VALID_SOURCES = ("prompt", "dream", "environment", "system", "synthesis", "pressure")
VALID_TRIGGERS = ("user", "startup")

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    prompt TEXT,
    cairo_code TEXT NOT NULL,
    claude_caption TEXT,
    user_annotation TEXT,
    image_path TEXT NOT NULL,
    grammar_version INTEGER NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('prompt','dream','environment','system','synthesis','pressure')),
    trigger TEXT NOT NULL CHECK(trigger IN ('user','startup'))
);

CREATE TABLE IF NOT EXISTS grammar_versions (
    version INTEGER PRIMARY KEY AUTOINCREMENT,
    system_prompt TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ---- entries ----------------------------------------------------------

def insert_entry(timestamp, prompt, cairo_code, claude_caption, image_path,
                  grammar_version, source, trigger, user_annotation=None):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO entries
               (timestamp, prompt, cairo_code, claude_caption, user_annotation,
                image_path, grammar_version, source, trigger)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp, prompt, cairo_code, claude_caption, user_annotation,
             str(image_path), grammar_version, source, trigger),
        )
        return get_entry(cur.lastrowid, conn=conn)


def get_entry(entry_id, conn=None):
    if conn is not None:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None
    with get_conn() as c:
        row = c.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None


def list_entries(source=None, limit=None, order="ASC"):
    order = "DESC" if order.upper() == "DESC" else "ASC"
    query = "SELECT * FROM entries"
    params = []
    if source:
        query += " WHERE source = ?"
        params.append(source)
    query += f" ORDER BY id {order}"
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def recent_entries(limit=15, sources=None):
    """Most recent entries, oldest-first, optionally filtered to a set of sources."""
    query = "SELECT * FROM entries"
    params = []
    if sources:
        placeholders = ",".join("?" for _ in sources)
        query += f" WHERE source IN ({placeholders})"
        params.extend(sources)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in reversed(rows)]


def random_entries(limit=20):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM entries ORDER BY RANDOM() LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def count_entries():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]


def set_annotation(entry_id, annotation):
    with get_conn() as conn:
        conn.execute(
            "UPDATE entries SET user_annotation = ? WHERE id = ?",
            (annotation, entry_id),
        )
        return get_entry(entry_id, conn=conn)


def delete_entry(entry_id):
    with get_conn() as conn:
        row = conn.execute("SELECT image_path FROM entries WHERE id = ?", (entry_id,)).fetchone()
        conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        return dict(row) if row else None


# ---- grammar versions ---------------------------------------------------

def insert_grammar_version(system_prompt, created_at):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO grammar_versions (system_prompt, created_at) VALUES (?, ?)",
            (system_prompt, created_at),
        )
        return cur.lastrowid


def get_grammar_version(version):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM grammar_versions WHERE version = ?", (version,)
        ).fetchone()
        return dict(row) if row else None


def get_latest_grammar_version():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM grammar_versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


# ---- session log --------------------------------------------------------

def log_session_start(timestamp):
    with get_conn() as conn:
        conn.execute("INSERT INTO session_log (started_at) VALUES (?)", (timestamp,))


def get_previous_session_start():
    """The most recent session start *before* the one just logged, or None."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT started_at FROM session_log ORDER BY id DESC LIMIT 2"
        ).fetchall()
    return rows[1]["started_at"] if len(rows) == 2 else None


# ---- settings (API keys, editable live from the UI) ---------------------

def get_setting(key, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )


def delete_setting(key):
    with get_conn() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def all_settings_keys():
    with get_conn() as conn:
        rows = conn.execute("SELECT key FROM settings").fetchall()
        return [r["key"] for r in rows]
