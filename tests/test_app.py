from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient

from src.main import create_app


def make_event(event_id: str, topic: str = "app.logs") -> dict:
    return {
        "topic": topic,
        "event_id": event_id,
        "timestamp": "2026-04-21T10:00:00Z",
        "source": "unit-test",
        "payload": {"message": f"event-{event_id}"},
    }


def make_client(db_path: Path) -> TestClient:
    app = create_app(str(db_path))
    return TestClient(app)


def test_publish_single_event_and_fetch(tmp_path: Path) -> None:
    db_path = tmp_path / "single.db"
    with make_client(db_path) as client:
        publish_response = client.post("/publish", json=make_event("e1"))
        assert publish_response.status_code == 200
        body = publish_response.json()
        assert body["accepted"] == 1
        assert body["unique_processed"] == 1

        events_response = client.get("/events", params={"topic": "app.logs"})
        assert events_response.status_code == 200
        events_body = events_response.json()
        assert events_body["count"] == 1
        assert events_body["events"][0]["event_id"] == "e1"


def test_duplicate_event_is_dropped(tmp_path: Path) -> None:
    db_path = tmp_path / "dedup.db"
    with make_client(db_path) as client:
        client.post("/publish", json=make_event("dup-1"))
        response = client.post("/publish", json=make_event("dup-1"))

        assert response.status_code == 200
        body = response.json()
        assert body["received"] == 2
        assert body["unique_processed"] == 1
        assert body["duplicate_dropped"] == 1

        stats = client.get("/stats").json()
        assert stats["duplicate_dropped"] == 1


def test_batch_publish_with_duplicates(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.db"
    batch = [
        make_event("b1", topic="payments"),
        make_event("b2", topic="payments"),
        make_event("b1", topic="payments"),
    ]

    with make_client(db_path) as client:
        response = client.post("/publish", json=batch)
        assert response.status_code == 200

        stats = client.get("/stats").json()
        assert stats["received"] == 3
        assert stats["unique_processed"] == 2
        assert stats["duplicate_dropped"] == 1
        assert stats["topics"]["payments"] == 2


def test_validation_rejects_invalid_timestamp(tmp_path: Path) -> None:
    db_path = tmp_path / "validation1.db"
    invalid = make_event("bad-ts")
    invalid["timestamp"] = "not-a-timestamp"

    with make_client(db_path) as client:
        response = client.post("/publish", json=invalid)
        assert response.status_code == 422


def test_validation_rejects_missing_topic(tmp_path: Path) -> None:
    db_path = tmp_path / "validation2.db"
    invalid = make_event("bad-topic")
    invalid.pop("topic")

    with make_client(db_path) as client:
        response = client.post("/publish", json=invalid)
        assert response.status_code == 422


def test_stats_and_events_are_consistent(tmp_path: Path) -> None:
    db_path = tmp_path / "consistency.db"
    with make_client(db_path) as client:
        client.post("/publish", json=make_event("c1", topic="orders"))
        client.post("/publish", json=make_event("c2", topic="orders"))
        client.post("/publish", json=make_event("c1", topic="orders"))

        stats = client.get("/stats").json()
        events = client.get("/events", params={"topic": "orders"}).json()

        assert stats["received"] == 3
        assert stats["unique_processed"] == 2
        assert stats["duplicate_dropped"] == 1
        assert events["count"] == 2


def test_dedup_persists_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "restart.db"

    with make_client(db_path) as client:
        first = client.post("/publish", json=make_event("r1", topic="restart"))
        assert first.status_code == 200
        assert first.json()["unique_processed"] == 1

    with make_client(db_path) as client:
        second = client.post("/publish", json=make_event("r1", topic="restart"))
        assert second.status_code == 200
        assert second.json()["received"] == 2
        assert second.json()["unique_processed"] == 1
        assert second.json()["duplicate_dropped"] == 1


def test_legacy_db_reconciles_counters_from_processed_events(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE processed_events (
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
        conn.execute(
            """
            INSERT INTO processed_events (
                topic, event_id, event_timestamp, source, payload_json, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "demo.logs",
                "legacy-1",
                "2026-04-21T10:00:00+00:00",
                "legacy-test",
                '{"message":"legacy"}',
                "2026-04-21T10:00:01+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    with make_client(db_path) as client:
        stats = client.get("/stats").json()
        assert stats["received"] == 1
        assert stats["unique_processed"] == 1
        assert stats["duplicate_dropped"] == 0
        assert stats["topics"]["demo.logs"] == 1


def test_stress_5000_events_with_20_percent_duplicates(tmp_path: Path) -> None:
    db_path = tmp_path / "stress.db"
    unique_events = [make_event(f"s-{i}", topic="stress") for i in range(4000)]
    duplicates = [make_event(f"s-{i}", topic="stress") for i in range(1000)]
    payload = unique_events + duplicates

    with make_client(db_path) as client:
        start = time.perf_counter()
        response = client.post("/publish", json=payload)
        elapsed = time.perf_counter() - start

        assert response.status_code == 200
        stats = client.get("/stats").json()
        assert stats["received"] == 5000
        assert stats["unique_processed"] == 4000
        assert stats["duplicate_dropped"] == 1000
        assert elapsed < 20.0
