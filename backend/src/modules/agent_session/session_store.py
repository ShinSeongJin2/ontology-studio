"""SQLite-backed session store for persisting conversation history."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "sessions.db"
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection (created lazily)."""

    conn = getattr(_local, "conn", None)
    if conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""

    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL DEFAULT '',
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            mode        TEXT NOT NULL,
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            metadata    TEXT NOT NULL DEFAULT '{}',
            created_at  REAL NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_messages_session_mode
            ON messages(session_id, mode, id);

        CREATE TABLE IF NOT EXISTS build_context (
            session_id  TEXT PRIMARY KEY,
            context_json TEXT NOT NULL DEFAULT '{}',
            updated_at  REAL NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS frontend_state (
            session_id  TEXT NOT NULL,
            mode        TEXT NOT NULL,
            state_json  TEXT NOT NULL DEFAULT '[]',
            updated_at  REAL NOT NULL,
            PRIMARY KEY (session_id, mode),
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------

def list_sessions() -> list[dict[str, Any]]:
    """Return all sessions ordered by most recent first."""

    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC"
    ).fetchall()
    return [
        {"id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3]}
        for r in rows
    ]


def get_session(session_id: str) -> dict[str, Any] | None:
    """Return a single session metadata or None."""

    conn = _get_conn()
    row = conn.execute(
        "SELECT id, title, created_at, updated_at FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "title": row[1], "created_at": row[2], "updated_at": row[3]}


def ensure_session(session_id: str, title: str = "") -> dict[str, Any]:
    """Create session if it doesn't exist; return session metadata."""

    existing = get_session(session_id)
    if existing:
        return existing
    now = time.time()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (session_id, title, now, now),
    )
    conn.commit()
    return {"id": session_id, "title": title, "created_at": now, "updated_at": now}


def update_session_title(session_id: str, title: str) -> None:
    """Update the title of an existing session."""

    conn = _get_conn()
    conn.execute(
        "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
        (title, time.time(), session_id),
    )
    conn.commit()


def touch_session(session_id: str) -> None:
    """Bump updated_at timestamp."""

    conn = _get_conn()
    conn.execute(
        "UPDATE sessions SET updated_at = ? WHERE id = ?",
        (time.time(), session_id),
    )
    conn.commit()


def delete_session(session_id: str) -> None:
    """Delete a session and all its messages (CASCADE)."""

    conn = _get_conn()
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Message persistence
# ---------------------------------------------------------------------------

def _serialize_message(msg: Any) -> dict[str, Any]:
    """Convert a LangChain message to a serializable dict."""

    if isinstance(msg, HumanMessage):
        return {"role": "human", "content": msg.content, "metadata": {}}
    elif isinstance(msg, AIMessage):
        meta: dict[str, Any] = {}
        if msg.tool_calls:
            meta["tool_calls"] = msg.tool_calls
        return {"role": "ai", "content": msg.content, "metadata": meta}
    elif isinstance(msg, ToolMessage):
        return {
            "role": "tool",
            "content": msg.content if isinstance(msg.content, str) else str(msg.content),
            "metadata": {
                "tool_call_id": msg.tool_call_id,
                "name": msg.name or "",
            },
        }
    # Fallback
    return {
        "role": "unknown",
        "content": str(getattr(msg, "content", "")),
        "metadata": {},
    }


def _deserialize_message(row: dict[str, Any]) -> Any:
    """Reconstruct a LangChain message from stored data."""

    role = row["role"]
    content = row["content"]
    meta = row.get("metadata", {})

    if role == "human":
        return HumanMessage(content=content)
    elif role == "ai":
        tool_calls = meta.get("tool_calls", [])
        return AIMessage(content=content, tool_calls=tool_calls)
    elif role == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=meta.get("tool_call_id", ""),
            name=meta.get("name", ""),
        )
    # Fallback: treat as human
    return HumanMessage(content=content)


def save_messages(session_id: str, mode: str, messages: list) -> None:
    """Replace all messages for a session+mode with the given list."""

    conn = _get_conn()
    conn.execute(
        "DELETE FROM messages WHERE session_id = ? AND mode = ?",
        (session_id, mode),
    )
    now = time.time()
    rows = []
    for msg in messages:
        serialized = _serialize_message(msg)
        rows.append((
            session_id,
            mode,
            serialized["role"],
            serialized["content"],
            json.dumps(serialized["metadata"], ensure_ascii=False),
            now,
        ))
    if rows:
        conn.executemany(
            "INSERT INTO messages (session_id, mode, role, content, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
    conn.execute(
        "UPDATE sessions SET updated_at = ? WHERE id = ?",
        (now, session_id),
    )
    conn.commit()


def load_messages(session_id: str, mode: str) -> list:
    """Load all messages for a session+mode, ordered by insertion order."""

    conn = _get_conn()
    rows = conn.execute(
        "SELECT role, content, metadata FROM messages "
        "WHERE session_id = ? AND mode = ? ORDER BY id",
        (session_id, mode),
    ).fetchall()
    result = []
    for role, content, metadata_str in rows:
        meta = json.loads(metadata_str) if metadata_str else {}
        result.append(_deserialize_message({"role": role, "content": content, "metadata": meta}))
    return result


def append_message(session_id: str, mode: str, msg: Any) -> None:
    """Append a single message (used during streaming for incremental saves)."""

    conn = _get_conn()
    serialized = _serialize_message(msg)
    now = time.time()
    conn.execute(
        "INSERT INTO messages (session_id, mode, role, content, metadata, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            session_id,
            mode,
            serialized["role"],
            serialized["content"],
            json.dumps(serialized["metadata"], ensure_ascii=False),
            now,
        ),
    )
    conn.execute(
        "UPDATE sessions SET updated_at = ? WHERE id = ?",
        (now, session_id),
    )
    conn.commit()


def delete_session_messages(session_id: str) -> None:
    """Delete all messages for a session (both modes)."""

    conn = _get_conn()
    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM frontend_state WHERE session_id = ?", (session_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Frontend state persistence
# ---------------------------------------------------------------------------

def save_frontend_state(session_id: str, mode: str, state_json: str) -> None:
    """Save (upsert) serialised frontend messages for a session+mode."""

    conn = _get_conn()
    now = time.time()
    conn.execute(
        "INSERT INTO frontend_state (session_id, mode, state_json, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(session_id, mode) DO UPDATE SET state_json = excluded.state_json, "
        "updated_at = excluded.updated_at",
        (session_id, mode, state_json, now),
    )
    conn.execute(
        "UPDATE sessions SET updated_at = ? WHERE id = ?",
        (now, session_id),
    )
    conn.commit()


def load_frontend_state(session_id: str, mode: str) -> str | None:
    """Load serialised frontend messages for a session+mode, or None."""

    conn = _get_conn()
    row = conn.execute(
        "SELECT state_json FROM frontend_state WHERE session_id = ? AND mode = ?",
        (session_id, mode),
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Build context persistence (for resuming interrupted builds)
# ---------------------------------------------------------------------------

def save_build_context(session_id: str, context_json: str) -> None:
    """Save (upsert) build context for a session."""

    conn = _get_conn()
    now = time.time()
    conn.execute(
        "INSERT INTO build_context (session_id, context_json, updated_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET context_json = excluded.context_json, "
        "updated_at = excluded.updated_at",
        (session_id, context_json, now),
    )
    conn.commit()


def load_build_context(session_id: str) -> str | None:
    """Load saved build context for a session, or None."""

    conn = _get_conn()
    row = conn.execute(
        "SELECT context_json FROM build_context WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return row[0] if row else None
