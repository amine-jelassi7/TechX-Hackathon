"""Shared helpers for dashboard pages."""

from __future__ import annotations

from typing import Any

import streamlit as st

from dashboard import api


def call(fn, *args: Any, **kwargs: Any) -> tuple[bool, Any]:
    """Run an API call; on ApiError show st.error and return (False, None)."""
    try:
        return True, fn(*args, **kwargs)
    except api.ApiError as exc:
        st.error(str(exc))
        return False, None


def fmt_reasons(reasons: Any) -> str:
    if isinstance(reasons, list):
        return "; ".join(str(r) for r in reasons)
    return "" if reasons is None else str(reasons)


def quiet(fn, *args: Any, **kwargs: Any) -> Any:
    """Run an API call and return None on failure (for secondary data that must not raise an error banner)."""
    try:
        return fn(*args, **kwargs)
    except api.ApiError:
        return None


def bucket_series(events: list[dict], pred=None, n: int = 24) -> list[float]:
    """Count events (any order) into n equal time buckets, oldest first. pred filters what is counted."""
    from datetime import datetime

    stamped = []
    for e in events:
        try:
            stamped.append((datetime.fromisoformat(str(e.get("ts"))).timestamp(), e))
        except ValueError:
            continue
    if not stamped:
        return []
    lo = min(t for t, _ in stamped)
    span = (max(t for t, _ in stamped) - lo) or 1.0
    out = [0.0] * n
    for t, e in stamped:
        if pred is None or pred(e):
            out[min(n - 1, int((t - lo) / span * n))] += 1
    return out
