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
AREA_X = float(os.getenv("AREA_X", "50"))
AREA_Y = float(os.getenv("AREA_Y", "50"))
AREA_SIZE = float(os.getenv("AREA_SIZE", "20"))
MAP_LAT_MIN = 24.0
MAP_LAT_RANGE = 4.8
MAP_LON_MIN = 52.0
MAP_LON_RANGE = 6.8

EVENT_TYPES = [
    "route_blockage",
    "signal_failure",
    "civil_vessel_adrift",
    "traffic_congestion",
    "unknown_object",
    "urgent_visual_inspection",
    "environmental_risk",
]


def clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def map_point_to_geo(x: float, y: float) -> tuple[float, float]:
    lat = MAP_LAT_MIN + ((100 - y) / 100) * MAP_LAT_RANGE
    lon = MAP_LON_MIN + (x / 100) * MAP_LON_RANGE
    return round(lat, 5), round(lon, 5)


def random_area_point() -> tuple[float, float]:
    half = AREA_SIZE / 2
    x = random.uniform(clamp(AREA_X - half, 0, 100), clamp(AREA_X + half, 0, 100))
    y = random.uniform(clamp(AREA_Y - half, 0, 100), clamp(AREA_Y + half, 0, 100))
    return map_point_to_geo(x, y)


def build_event() -> dict[str, object]:
    severity = random.randint(1, 10)
    event_type = random.choice(EVENT_TYPES)
    lat, lon = random_area_point()
    return {
        "event_id": str(uuid.uuid4()),
        "sensor_id": SENSOR_ID,
        "sector_id": SECTOR_ID,
        "event_type": event_type,
        "severity": severity,
        "lat": lat,
        "lon": lon,
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
