import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path


def now():
    return datetime.now(UTC).isoformat()


def uid():
    return uuid.uuid4().hex


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        self.db = sqlite3.connect(directory / "workflows.sqlite3", check_same_thread=False)
        os.chmod(directory / "workflows.sqlite3", 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS documents (
          kind TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
          PRIMARY KEY(kind, id)
        );
        CREATE TABLE IF NOT EXISTS received (
          session_id TEXT, event_id TEXT, PRIMARY KEY(session_id, event_id)
        );
        """)
        self.db.commit()

    def put(self, kind, doc):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO documents VALUES (?,?,?)", (kind, doc["id"], json.dumps(doc))
            )
        return doc

    def get(self, kind, ident):
        row = self.db.execute("SELECT body FROM documents WHERE kind=? AND id=?", (kind, ident)).fetchone()
        if not row:
            raise KeyError(f"{kind} not found")
        return json.loads(row[0])

    def all(self, kind):
        return [
            json.loads(r[0])
            for r in self.db.execute("SELECT body FROM documents WHERE kind=? ORDER BY rowid DESC", (kind,))
        ]

    def delete(self, kind, ident):
        with self.db:
            self.db.execute("DELETE FROM documents WHERE kind=? AND id=?", (kind, ident))

    def first_event(self, session_id, event_id):
        with self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO received VALUES (?,?)", (session_id, event_id))
        return cursor.rowcount == 1

    def forget_session(self, ident):
        self.delete("session", ident)
        with self.db:
            self.db.execute("DELETE FROM received WHERE session_id=?", (ident,))
