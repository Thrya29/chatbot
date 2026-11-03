"""Bridge state in SQLite: conversation mapping, processed message IDs, dead letters.

Webhooks are delivered at least once, so every handler checks `seen` before acting.
Use a file path for persistence across restarts; ':memory:' for tests.
"""
from __future__ import annotations

import json
import sqlite3
import time


class Store:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS mapping (
                assistant_conversation_id TEXT PRIMARY KEY,
                genesys_conversation_id   TEXT UNIQUE,
                client_id                 TEXT NOT NULL,
                status                    TEXT NOT NULL,
                created_at                REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS seen (message_id TEXT PRIMARY KEY, seen_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS dead_letter (
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, payload TEXT, error TEXT, at REAL
            );
            """
        )

    # --- idempotency -------------------------------------------------------
    def first_time(self, message_id: str) -> bool:
        """True the first time a message ID is seen; False for every redelivery."""
        try:
            self.db.execute("INSERT INTO seen VALUES (?, ?)", (message_id, time.time()))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    # --- mapping -----------------------------------------------------------
    def link(self, assistant_id: str, genesys_id: str, client_id: str) -> None:
        self.db.execute("INSERT INTO mapping VALUES (?, ?, ?, 'open', ?)",
                        (assistant_id, genesys_id, client_id, time.time()))
        self.db.commit()

    def by_assistant(self, assistant_id: str):
        return self.db.execute("SELECT * FROM mapping WHERE assistant_conversation_id=?", (assistant_id,)).fetchone()

    def by_genesys(self, genesys_id: str):
        return self.db.execute("SELECT * FROM mapping WHERE genesys_conversation_id=?", (genesys_id,)).fetchone()

    def set_status(self, assistant_id: str, status: str) -> None:
        self.db.execute("UPDATE mapping SET status=? WHERE assistant_conversation_id=?", (status, assistant_id))
        self.db.commit()

    # --- failures ----------------------------------------------------------
    def dead_letter(self, kind: str, payload: dict, error: str) -> None:
        self.db.execute("INSERT INTO dead_letter (kind, payload, error, at) VALUES (?, ?, ?, ?)",
                        (kind, json.dumps(payload), error, time.time()))
        self.db.commit()

    def dead_letters(self) -> list[tuple]:
        return self.db.execute("SELECT kind, payload, error FROM dead_letter").fetchall()
