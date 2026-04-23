from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from pydantic import ValidationError

from .dedup_store import DedupStore
from .models import EventIn
from .service import AggregatorService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


def _parse_events(payload: Any) -> list[EventIn]:
    if isinstance(payload, list):
        raw_events = payload
    elif isinstance(payload, dict):
        raw_events = [payload]
    else:
        raise HTTPException(status_code=422, detail="Body must be an event object or list of event objects")

    validated_events: list[EventIn] = []
    validation_errors: list[dict[str, Any]] = []

    for index, raw_event in enumerate(raw_events):
        try:
            validated_events.append(EventIn.model_validate(raw_event))
        except ValidationError as exc:
            validation_errors.append({"index": index, "errors": exc.errors()})

    if validation_errors:
        raise HTTPException(status_code=422, detail={"message": "Validation failed", "items": validation_errors})

    return validated_events


def create_app(db_path: str | None = None) -> FastAPI:
    resolved_db_path = db_path or os.getenv("DEDUP_DB_PATH", "./data/dedup.db")
    store = DedupStore(resolved_db_path)
    service = AggregatorService(store)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(
        title="UTS Pub-Sub Log Aggregator",
        description="Local pub-sub log aggregator with idempotent consumer and deduplication",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/publish")
    async def publish(payload: Any = Body(...)) -> dict[str, int]:
        events = _parse_events(payload)
        result = await service.publish(events)
        stats_now = service.get_stats()
        return {
            "accepted": result["accepted"],
            "received": stats_now["received"],
            "unique_processed": stats_now["unique_processed"],
            "duplicate_dropped": stats_now["duplicate_dropped"],
        }

    @app.get("/events")
    async def list_events(topic: str | None = Query(default=None)) -> dict[str, Any]:
        events = service.get_events(topic=topic)
        return {"count": len(events), "events": events}

    @app.get("/stats")
    async def stats() -> dict[str, Any]:
        return service.get_stats()

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=False)
