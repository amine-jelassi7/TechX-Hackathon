"""Thin HTTP client for the SoukSaaS owner API."""

from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

TIMEOUT = 10.0


class ApiError(Exception):
    """Single error type raised for any failed owner API call."""


def base_url() -> str:
    return os.getenv("API_URL", "http://127.0.0.1:8000").rstrip("/")


def unreachable_message() -> str:
    return f"Backend not reachable at {base_url()}, start it with `uv run python tasks.py run`"


def request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: Any = None,
    transport: httpx.BaseTransport | None = None,
) -> Any:
    headers = {"X-Owner-Token": os.getenv("OWNER_TOKEN", "change-me")}
    try:
        with httpx.Client(
            base_url=base_url(), timeout=TIMEOUT, headers=headers, transport=transport
        ) as client:
            resp = client.request(method, path, params=params, json=json)
    except httpx.HTTPError as exc:
        raise ApiError(unreachable_message()) from exc
    if resp.status_code >= 400:
        raise ApiError(f"{method} {path} failed with HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise ApiError(f"{method} {path} returned invalid JSON") from exc


def get(path: str, **params: Any) -> Any:
    return request("GET", path, params=params or None)


def post(path: str, json: Any = None, **params: Any) -> Any:
    return request("POST", path, params=params or None, json=json)


def put(path: str, json: Any) -> Any:
    return request("PUT", path, json=json)
