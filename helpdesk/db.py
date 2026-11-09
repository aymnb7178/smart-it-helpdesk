"""SQLite connection management and schema. Thin repository layer; parameterized SQL only."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','agent','requester')),
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    requester_id INTEGER NOT NULL REFERENCES users(id),
    assignee_id INTEGER REFERENCES users(id),
    suggested_category TEXT,
    suggested_priority TEXT,
    suggestion_source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    first_response_at TEXT,
    resolved_at TEXT,
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_requester ON tickets(requester_id);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    internal INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER,
    actor_id INTEGER,
    action TEXT NOT NULL,
    field TEXT,
    old_value TEXT,
    new_value TEXT,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ticket ON audit_log(ticket_id);
"""


class Database:
    """Opens short-lived connections to a SQLite file."""

    def __init__(self, path: str):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init(self) -> None:
        with self.session() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        """Commit on success, roll back on error, always close."""
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class UserRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, username: str, email: str, role: str, password_hash: str, created_at: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO users (username, email, role, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, email, role, password_hash, created_at),
        )
        return int(cur.lastrowid)

    def get(self, user_id: int) -> dict | None:
        return _row(self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    def get_by_username(self, username: str) -> dict | None:
        return _row(self.conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone())

    def list(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM users ORDER BY id")]

    def set_role(self, user_id: int, role: str) -> None:
        self.conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


class TicketRepository:
    #: Columns that may be written through :meth:`update` (guards the dynamic SET clause).
    UPDATABLE = frozenset(
        {
            "title", "description", "category", "priority", "status", "assignee_id",
            "updated_at", "first_response_at", "resolved_at", "closed_at",
        }
    )
    #: Columns that may be used as equality filters in :meth:`list`.
    FILTERABLE = frozenset({"status", "category", "priority", "assignee_id", "requester_id"})

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, **v: Any) -> int:
        cols = (
            "title", "description", "category", "priority", "status", "requester_id",
            "assignee_id", "suggested_category", "suggested_priority", "suggestion_source",
            "created_at", "updated_at",
        )
        cur = self.conn.execute(
            f"INSERT INTO tickets ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            tuple(v.get(c) for c in cols),
        )
        return int(cur.lastrowid)

    def get(self, ticket_id: int) -> dict | None:
        return _row(self.conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone())

    def list(self, limit: int = 200, offset: int = 0, **filters: Any) -> list[dict]:
        clauses, params = [], []
        for key, value in filters.items():
            if value is None:
                continue
            if key not in self.FILTERABLE:
                raise ValueError(f"Unsupported filter: {key}")
            clauses.append(f"{key} = ?")
            params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM tickets {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        return [dict(r) for r in self.conn.execute(sql, (*params, limit, offset))]

    def all(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM tickets ORDER BY id")]

    def update(self, ticket_id: int, **changes: Any) -> None:
        bad = set(changes) - self.UPDATABLE
        if bad:
            raise ValueError(f"Columns not updatable: {sorted(bad)}")
        if not changes:
            return
        sets = ", ".join(f"{c} = ?" for c in changes)
        self.conn.execute(f"UPDATE tickets SET {sets} WHERE id = ?", (*changes.values(), ticket_id))

    def delete(self, ticket_id: int) -> None:
        self.conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))


class CommentRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, ticket_id: int, author_id: int, body: str, internal: bool, created_at: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO comments (ticket_id, author_id, body, internal, created_at) VALUES (?, ?, ?, ?, ?)",
            (ticket_id, author_id, body, int(internal), created_at),
        )
        return int(cur.lastrowid)

    def list(self, ticket_id: int, include_internal: bool) -> list[dict]:
        sql = "SELECT * FROM comments WHERE ticket_id = ?"
        if not include_internal:
            sql += " AND internal = 0"
        rows = self.conn.execute(sql + " ORDER BY id", (ticket_id,)).fetchall()
        return [{**dict(r), "internal": bool(r["internal"])} for r in rows]


class AuditRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def log(self, ticket_id: int | None, actor_id: int | None, action: str, at: str,
            field: str | None = None, old: Any = None, new: Any = None) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (ticket_id, actor_id, action, field, old_value, new_value, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticket_id, actor_id, action, field,
             None if old is None else str(old), None if new is None else str(new), at),
        )

    def for_ticket(self, ticket_id: int) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM audit_log WHERE ticket_id = ? ORDER BY id", (ticket_id,))
        return [dict(r) for r in rows]

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
