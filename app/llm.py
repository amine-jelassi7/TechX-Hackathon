"""LLM client: mock mode (offline, deterministic) and live mode (LiteLLM).

Contract: docs/API_CONTRACT.md section 1. Never logs the API key or full prompts.
"""
import json
import logging
from collections import defaultdict
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.config import settings
from app.schemas import Product

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Timeout budget: one call is at most TIMEOUT_S * MAX_ATTEMPTS = 30 s; the orchestrator gives an agent
# handler 45 s (settings.orchestrator_timeout_s), so a handler is not abandoned in the middle of one call.
TIMEOUT_S = 15
MAX_ATTEMPTS = 2  # first try + one retry
PRODUCT_PATH = Path(__file__).resolve().parent.parent / "data" / "product.json"

call_count: dict[str, int] = defaultdict(int)


class LLMError(Exception):
    """Raised when the live model fails or returns invalid output twice."""


def reset_counts() -> None:
    call_count.clear()


def _is_mock() -> bool:
    return settings.llm_mode != "live"


def check_live_config() -> None:
    """Startup check: live mode without a key can only fail, so say so once and clearly (never log the key)."""
    if not _is_mock() and not settings.llm_api_key:
        log.error("LLM_MODE=live but LLM_API_KEY is not set: every LLM call will fail with LLMError. "
                  "Set LLM_API_KEY in .env or use LLM_MODE=mock.")


def _require_key() -> None:
    if not settings.llm_api_key:
        raise LLMError("LLM_MODE=live but LLM_API_KEY is not set")


def _is_auth_error(exc: Exception) -> bool:
    """Bad/missing credentials never succeed on retry."""
    return type(exc).__name__ in ("AuthenticationError", "PermissionDeniedError") or         getattr(exc, "status_code", None) in (401, 403)


def _completion(system: str, user: str, json_mode: bool) -> str:
    import litellm  # lazy: mock mode never needs it

    kwargs: dict = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "timeout": TIMEOUT_S,
        "api_key": settings.llm_api_key or None,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = litellm.completion(**kwargs)
    content = resp.choices[0].message.content
    if not content:
        raise ValueError("empty completion")
    return content


def chat(system: str, user: str, *, task: str, mock: Callable[[], str]) -> str:
    call_count[task] += 1
    if _is_mock():
        return mock()
    _require_key()
    last: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return _completion(system, user, json_mode=False)
        except Exception as exc:  # noqa: BLE001  network, timeout, provider errors
            last = exc
            log.warning("llm call failed task=%s attempt=%d err=%s", task, attempt + 1,
                        type(exc).__name__)
            if _is_auth_error(exc):
                break
    raise LLMError(f"LLM call failed for task {task}: {type(last).__name__}")


def chat_json(system: str, user: str, schema: type[T], *, task: str,
              mock: Callable[[], dict | T]) -> T:
    call_count[task] += 1
    if _is_mock():
        try:
            value = mock()
            if isinstance(value, schema):
                return value
            return schema.model_validate(value)
        except ValidationError as exc:
            raise LLMError(f"mock output invalid for task {task}") from exc
    _require_key()
    last: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            raw = _completion(system, user, json_mode=True)
            return schema.model_validate(json.loads(raw))
        except (ValidationError, ValueError) as exc:  # includes JSONDecodeError
            last = exc
            log.warning("llm invalid output task=%s attempt=%d", task, attempt + 1)
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("llm call failed task=%s attempt=%d err=%s", task, attempt + 1,
                        type(exc).__name__)
            if _is_auth_error(exc):
                break
    raise LLMError(f"LLM output unusable for task {task}: {type(last).__name__}")


@lru_cache(maxsize=1)
def load_product() -> Product:
    return Product.model_validate_json(PRODUCT_PATH.read_text(encoding="utf-8"))
