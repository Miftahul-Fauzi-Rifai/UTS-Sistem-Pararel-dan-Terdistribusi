from __future__ import annotations

import argparse
import random
from datetime import datetime, timezone
import httpx


def build_events(total: int, duplicate_rate: float, topic: str, id_prefix: str) -> list[dict]:
    duplicate_total = int(total * duplicate_rate)
    unique_total = total - duplicate_total

    unique_events = []
    for index in range(unique_total):
        unique_events.append(
            {
                "topic": topic,
                "event_id": f"{id_prefix}-{index}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "publisher-sim",
                "payload": {"index": index, "level": "INFO"},
            }
        )

    duplicates = []
    for index in range(duplicate_total):
        source_idx = index % max(unique_total, 1)
        duplicates.append(
            {
                "topic": topic,
                "event_id": f"{id_prefix}-{source_idx}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "publisher-sim",
                "payload": {"index": source_idx, "level": "INFO", "duplicate": True},
            }
        )

    payload = unique_events + duplicates
    random.shuffle(payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish synthetic events with duplicates")
    parser.add_argument("--url", default="http://localhost:8080/publish", help="Publish endpoint")
    parser.add_argument("--count", type=int, default=5000, help="Total events to send")
    parser.add_argument("--duplicate-rate", type=float, default=0.2, help="Duplicate ratio from 0.0 to 1.0")
    parser.add_argument("--topic", default="demo.logs", help="Topic name to publish")
    parser.add_argument(
        "--id-prefix",
        default="demo",
        help="Prefix for event_id generation. Use a different value per run to avoid cross-run duplicates.",
    )
    args = parser.parse_args()

    payload = build_events(
        total=args.count,
        duplicate_rate=args.duplicate_rate,
        topic=args.topic,
        id_prefix=args.id_prefix,
    )

    with httpx.Client(timeout=60.0) as client:
        response = client.post(args.url, json=payload)
        response.raise_for_status()
        print(response.json())


if __name__ == "__main__":
    main()
