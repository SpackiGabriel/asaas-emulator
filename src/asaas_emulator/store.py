"""State lives in SQLite.

Why a database instead of dicts: the emulator is a server that test suites hit from
several threads (request handlers plus the webhook worker), and a few operations must
be atomic. Debiting a balance is the clearest example: "read balance, compare, write"
across two statements is a race; a single conditional UPDATE is not. SQLite also lets
the state survive a restart when ASAAS_EMULATOR_DATABASE points at a file.

Resources are stored as JSON documents keyed by (kind, id) and partitioned by account.
Each API key is an account, so one emulator can serve many tenants in parallel without
their customers, payments or balances ever crossing.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    api_key       TEXT PRIMARY KEY,
    balance_cents INTEGER NOT NULL DEFAULT 0 CHECK (balance_cents >= 0)
);
CREATE TABLE IF NOT EXISTS sequences (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS resources (
    kind    TEXT NOT NULL,
    id      TEXT NOT NULL,
    account TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    data    TEXT NOT NULL,
    PRIMARY KEY (kind, id)
);
CREATE INDEX IF NOT EXISTS resources_by_account ON resources (account, kind, seq);
CREATE TABLE IF NOT EXISTS events (
    id         TEXT PRIMARY KEY,
    account    TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    event      TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_account ON events (account, seq);
CREATE TABLE IF NOT EXISTS deliveries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    account          TEXT NOT NULL,
    webhook_id       TEXT NOT NULL,
    event_id         TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'PENDING',
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  REAL NOT NULL DEFAULT 0,
    last_status_code INTEGER,
    last_error       TEXT
);
CREATE INDEX IF NOT EXISTS deliveries_pending ON deliveries (status, next_attempt_at);
"""


class Store:
    def __init__(self, database: str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._db = sqlite3.connect(database, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    # -- infrastructure ---------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialise writers and make a block of statements atomic.

        Re-entrant: nested calls join the outer transaction.
        """
        with self._lock:
            if self._db.in_transaction:
                yield self._db
                return
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def _fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self._db.execute(sql, params).fetchone()
            return row

    def _fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def reset(self) -> None:
        with self.transaction() as db:
            for table in ("accounts", "sequences", "resources", "events", "deliveries"):
                db.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names

    def next_seq(self, name: str) -> int:
        with self.transaction() as db:
            row = db.execute(
                "INSERT INTO sequences (name, value) VALUES (?, 1) "
                "ON CONFLICT(name) DO UPDATE SET value = value + 1 RETURNING value",
                (name,),
            ).fetchone()
            return int(row["value"])

    def next_id(self, prefix: str) -> str:
        """Deterministic ids: pay_000000000001, cus_000000000001, ...

        Determinism is the point: a test can assert on an id, and two runs of the same
        suite against a fresh emulator produce byte-identical traffic.
        """
        return f"{prefix}_{self.next_seq(prefix):012d}"

    def next_uuid(self, name: str) -> str:
        return str(uuid.UUID(int=self.next_seq(name)))

    # -- accounts and balance ---------------------------------------------------------

    def ensure_account(self, api_key: str) -> None:
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO accounts (api_key) VALUES (?)", (api_key,))

    def balance_cents(self, account: str) -> int:
        row = self._fetchone("SELECT balance_cents FROM accounts WHERE api_key = ?", (account,))
        return int(row["balance_cents"]) if row else 0

    def credit(self, account: str, cents: int) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE accounts SET balance_cents = balance_cents + ? WHERE api_key = ?",
                (cents, account),
            )

    def debit_if_available(self, account: str, cents: int) -> bool:
        """Debit only if the balance covers it, in one statement. Returns success."""
        with self.transaction() as db:
            cursor = db.execute(
                "UPDATE accounts SET balance_cents = balance_cents - ? "
                "WHERE api_key = ? AND balance_cents >= ?",
                (cents, account, cents),
            )
            return cursor.rowcount == 1

    def set_balance(self, account: str, cents: int) -> None:
        with self.transaction() as db:
            db.execute("UPDATE accounts SET balance_cents = ? WHERE api_key = ?", (cents, account))

    # -- resources --------------------------------------------------------------------

    def insert(self, kind: str, account: str, resource: dict[str, Any]) -> dict[str, Any]:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO resources (kind, id, account, seq, data) VALUES (?, ?, ?, ?, ?)",
                (
                    kind,
                    resource["id"],
                    account,
                    self.next_seq(f"order:{kind}"),
                    json.dumps(resource),
                ),
            )
        return resource

    def save(self, kind: str, account: str, resource: dict[str, Any]) -> dict[str, Any]:
        with self.transaction() as db:
            cursor = db.execute(
                "UPDATE resources SET data = ? WHERE kind = ? AND id = ? AND account = ?",
                (json.dumps(resource), kind, resource["id"], account),
            )
            if cursor.rowcount != 1:
                raise KeyError(resource["id"])
        return resource

    def get(self, kind: str, account: str, resource_id: str) -> dict[str, Any] | None:
        row = self._fetchone(
            "SELECT data FROM resources WHERE kind = ? AND id = ? AND account = ?",
            (kind, resource_id, account),
        )
        return json.loads(row["data"]) if row else None

    def all(self, kind: str, account: str) -> list[dict[str, Any]]:
        """Newest first, which is how Asaas orders its list endpoints."""
        rows = self._fetchall(
            "SELECT data FROM resources WHERE kind = ? AND account = ? ORDER BY seq DESC",
            (kind, account),
        )
        return [json.loads(r["data"]) for r in rows]

    def all_accounts(self, kind: str) -> list[tuple[str, dict[str, Any]]]:
        rows = self._fetchall(
            "SELECT account, data FROM resources WHERE kind = ? ORDER BY seq", (kind,)
        )
        return [(r["account"], json.loads(r["data"])) for r in rows]

    # -- events and webhook deliveries ------------------------------------------------

    def record_event(self, account: str, event: dict[str, Any]) -> None:
        """Persist an event and enqueue one delivery per subscribed webhook.

        Runs inside the caller's transaction, so a state change and the event that
        announces it commit together or not at all (a transactional outbox).
        """
        with self.transaction() as db:
            db.execute(
                "INSERT INTO events (id, account, seq, event, payload) VALUES (?, ?, ?, ?, ?)",
                (
                    event["id"],
                    account,
                    self.next_seq("order:event"),
                    event["event"],
                    json.dumps(event),
                ),
            )
            for webhook in self.all("webhook", account):
                if webhook["enabled"] and event["event"] in webhook["events"]:
                    db.execute(
                        "INSERT INTO deliveries (account, webhook_id, event_id) VALUES (?, ?, ?)",
                        (account, webhook["id"], event["id"]),
                    )

    def events(self, account: str) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT payload FROM events WHERE account = ? ORDER BY seq", (account,)
        )
        return [json.loads(r["payload"]) for r in rows]

    def event(self, event_id: str) -> dict[str, Any]:
        row = self._fetchone("SELECT payload FROM events WHERE id = ?", (event_id,))
        if row is None:
            raise KeyError(event_id)
        event: dict[str, Any] = json.loads(row["payload"])
        return event

    def due_deliveries(self, now: float | None = None) -> list[sqlite3.Row]:
        now = time.time() if now is None else now
        return self._fetchall(
            "SELECT d.* FROM deliveries d JOIN events e ON e.id = d.event_id "
            "WHERE d.status = 'PENDING' AND d.next_attempt_at <= ? ORDER BY e.seq",
            (now,),
        )

    def deliveries(self, account: str) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT d.*, e.event FROM deliveries d JOIN events e ON e.id = d.event_id "
            "WHERE d.account = ? ORDER BY d.id",
            (account,),
        )
        return [dict(r) for r in rows]

    def mark_delivery(
        self,
        delivery_id: int,
        *,
        status: str,
        status_code: int | None,
        error: str | None,
        next_attempt_at: float = 0,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE deliveries SET status = ?, attempts = attempts + 1, "
                "last_status_code = ?, last_error = ?, next_attempt_at = ? WHERE id = ?",
                (status, status_code, error, next_attempt_at, delivery_id),
            )

    def pending_ahead(self, webhook_id: str, event_id: str) -> bool:
        """True if an older event for this webhook is still undelivered."""
        row = self._fetchone(
            "SELECT 1 FROM deliveries d JOIN events e ON e.id = d.event_id "
            "WHERE d.webhook_id = ? AND d.status = 'PENDING' "
            "AND e.seq < (SELECT seq FROM events WHERE id = ?) LIMIT 1",
            (webhook_id, event_id),
        )
        return row is not None
