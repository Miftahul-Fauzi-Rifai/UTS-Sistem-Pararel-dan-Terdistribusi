from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import EventIn


class DedupStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_events (
                    processing_order INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_timestamp TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    UNIQUE(topic, event_id)
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_processed_events_topic_order
                ON processed_events(topic, processing_order)
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS service_counters (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    received INTEGER NOT NULL DEFAULT 0,
                    unique_processed INTEGER NOT NULL DEFAULT 0,
                    duplicate_dropped INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._conn.execute(
                """
                INSERT OR IGNORE INTO service_counters (id, received, unique_processed, duplicate_dropped)
                VALUES (1, 0, 0, 0)
                """
            )

            # Reconcile counters when opening a legacy DB that already has processed events.
            # Older versions did not persist counters, so unique/received can start at 0
            # while processed_events is non-empty.
            processed_count_row = self._conn.execute(
                "SELECT COUNT(*) AS total FROM processed_events"
            ).fetchone()
            counter_row = self._conn.execute(
                """
                SELECT received, unique_processed, duplicate_dropped
                FROM service_counters
                WHERE id = 1
                """
            ).fetchone()

            processed_total = int(processed_count_row["total"]) if processed_count_row is not None else 0
            if counter_row is not None and processed_total > int(counter_row["unique_processed"]):
                next_unique = processed_total
                next_received = max(int(counter_row["received"]), next_unique)
                self._conn.execute(
                    """
                    UPDATE service_counters
                    SET received = ?, unique_processed = ?
                    WHERE id = 1
                    """,
                    (next_received, next_unique),
                )

    def get_counters(self) -> dict[str, int]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT received, unique_processed, duplicate_dropped
                FROM service_counters
                WHERE id = 1
                """
            ).fetchone()

        if row is None:
            return {"received": 0, "unique_processed": 0, "duplicate_dropped": 0}

        return {
            "received": int(row["received"]),
            "unique_processed": int(row["unique_processed"]),
            "duplicate_dropped": int(row["duplicate_dropped"]),
        }

    def bump_counters(self, inserted: bool) -> dict[str, int]:
        unique_delta = 1 if inserted else 0
        duplicate_delta = 0 if inserted else 1

        with self._lock:
            self._conn.execute(
                """
                UPDATE service_counters
                SET received = received + 1,
                    unique_processed = unique_processed + ?,
                    duplicate_dropped = duplicate_dropped + ?
                WHERE id = 1
                """,
                (unique_delta, duplicate_delta),
            )
            self._conn.commit()
            row = self._conn.execute(
                """
                SELECT received, unique_processed, duplicate_dropped
                FROM service_counters
                WHERE id = 1
                """
            ).fetchone()

        if row is None:
            return {"received": 0, "unique_processed": 0, "duplicate_dropped": 0}

        return {
            "received": int(row["received"]),
            "unique_processed": int(row["unique_processed"]),
            "duplicate_dropped": int(row["duplicate_dropped"]),
        }

    def insert_if_new(self, event: EventIn) -> bool:
        event_timestamp = event.timestamp.astimezone(timezone.utc).isoformat()
        processed_at = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(event.payload, separators=(",", ":"), sort_keys=True)

        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO processed_events (
                        topic, event_id, event_timestamp, source, payload_json, processed_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (event.topic, event.event_id, event_timestamp, event.source, payload_json, processed_at),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get_events(self, topic: str | None = None, limit: int = 10000) -> list[dict[str, Any]]:
        with self._lock:
            if topic:
                cursor = self._conn.execute(
                    """
                    SELECT topic, event_id, event_timestamp, source, payload_json, processed_at
                    FROM processed_events
                    WHERE topic = ?
                    ORDER BY processing_order ASC
                    LIMIT ?
                    """,
                    (topic, limit),
                )
            else:
                cursor = self._conn.execute(
                    """
                    SELECT topic, event_id, event_timestamp, source, payload_json, processed_at
                    FROM processed_events
                    ORDER BY processing_order ASC
                    LIMIT ?
                    """,
                    (limit,),
                )
            rows = cursor.fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            results.append(
                {
                    "topic": row["topic"],
                    "event_id": row["event_id"],
                    "timestamp": row["event_timestamp"],
                    "source": row["source"],
                    "payload": json.loads(row["payload_json"]),
                    "processed_at": row["processed_at"],
                }
            )
        return results

    def topic_counts(self) -> dict[str, int]:
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT topic, COUNT(*) as total
                FROM processed_events
                GROUP BY topic
                ORDER BY topic ASC
                """
            )
            rows = cursor.fetchall()
        return {row["topic"]: row["total"] for row in rows}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
