from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .dedup_store import DedupStore
from .models import EventIn

LOGGER = logging.getLogger("aggregator")


class AggregatorService:
    def __init__(self, store: DedupStore) -> None:
        self.store = store
        self.queue: asyncio.Queue[EventIn | None] = asyncio.Queue()
        counters = self.store.get_counters()
        self.received = counters["received"]
        self.unique_processed = counters["unique_processed"]
        self.duplicate_dropped = counters["duplicate_dropped"]
        self._worker_task: asyncio.Task[None] | None = None
        self._started_monotonic = time.monotonic()

    async def start(self) -> None:
        if self._worker_task is not None:
            return
        self._worker_task = asyncio.create_task(self._worker_loop(), name="aggregator-consumer")

    async def stop(self) -> None:
        if self._worker_task is None:
            return
        await self.queue.put(None)
        await self.queue.join()
        await self._worker_task
        self._worker_task = None
        self.store.close()

    async def publish(self, events: list[EventIn]) -> dict[str, int]:
        for event in events:
            await self.queue.put(event)
        await self.queue.join()
        return {"accepted": len(events), "queue_size": self.queue.qsize()}

    def get_events(self, topic: str | None = None) -> list[dict[str, Any]]:
        return self.store.get_events(topic=topic)

    def get_stats(self) -> dict[str, Any]:
        counters = self.store.get_counters()
        self.received = counters["received"]
        self.unique_processed = counters["unique_processed"]
        self.duplicate_dropped = counters["duplicate_dropped"]
        topic_totals = self.store.topic_counts()
        return {
            "received": self.received,
            "unique_processed": self.unique_processed,
            "duplicate_dropped": self.duplicate_dropped,
            "topics": topic_totals,
            "topic_count": len(topic_totals),
            "uptime_seconds": round(time.monotonic() - self._started_monotonic, 3),
            "queue_size": self.queue.qsize(),
        }

    async def _worker_loop(self) -> None:
        while True:
            event = await self.queue.get()
            try:
                if event is None:
                    return

                inserted = self.store.insert_if_new(event)
                counters = self.store.bump_counters(inserted=inserted)
                self.received = counters["received"]
                self.unique_processed = counters["unique_processed"]
                self.duplicate_dropped = counters["duplicate_dropped"]

                if inserted:
                    LOGGER.info("Processed event topic=%s event_id=%s", event.topic, event.event_id)
                else:
                    LOGGER.warning("Duplicate dropped topic=%s event_id=%s", event.topic, event.event_id)
            finally:
                self.queue.task_done()
