from __future__ import annotations

import os
import random
import time
import uuid

from common import http_json, now_ms


SENSOR_ID = os.getenv("SENSOR_ID", "sensor-unknown")
SECTOR_ID = os.getenv("SECTOR_ID", "sector-unknown")
BROKER_URL = os.getenv("BROKER_URL", "http://broker-a:8000")
INTERVAL_SECONDS = float(os.getenv("INTERVAL_SECONDS", "2"))

EVENT_TYPES = [
    "route_blockage",
    "signal_failure",
    "civil_vessel_adrift",
    "traffic_congestion",
    "unknown_object",
    "urgent_visual_inspection",
    "environmental_risk",
]


def build_event() -> dict[str, object]:
    severity = random.randint(1, 10)
    event_type = random.choice(EVENT_TYPES)
    return {
        "event_id": str(uuid.uuid4()),
        "sensor_id": SENSOR_ID,
        "sector_id": SECTOR_ID,
        "event_type": event_type,
        "severity": severity,
        "lat": round(random.uniform(24.0, 28.8), 5),
        "lon": round(random.uniform(52.0, 58.8), 5),
        "timestamp_ms": now_ms(),
        "requires_drone": severity >= 7,
    }


def main() -> None:
    print(f"[{SENSOR_ID}] sending events to {BROKER_URL}")
    while True:
        event = build_event()
        status, payload = http_json("POST", f"{BROKER_URL}/sensor", event, timeout=2)
        print(f"[{SENSOR_ID}] event={event['event_type']} severity={event['severity']} status={status} response={payload}")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
