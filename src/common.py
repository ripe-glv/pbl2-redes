from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse


def env_list(name: str, default: str = "") -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def now_ms() -> int:
    return int(time.time() * 1000)


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("content-length", "0"))
    if length == 0:
        return {}
    body = handler.rfile.read(length).decode("utf-8")
    return json.loads(body)


def write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json")
    handler.send_header("access-control-allow-origin", "*")
    handler.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
    handler.send_header("access-control-allow-headers", "content-type")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def write_cors_preflight(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(204)
    handler.send_header("access-control-allow-origin", "*")
    handler.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
    handler.send_header("access-control-allow-headers", "content-type")
    handler.end_headers()


def http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 2.0,
) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"content-type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return exc.code, {"error": raw}
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, {"error": str(exc)}


@dataclass(frozen=True)
class Route:
    path: str
    query: str


def route_for(handler: BaseHTTPRequestHandler) -> Route:
    parsed = urlparse(handler.path)
    return Route(path=parsed.path, query=parsed.query)
