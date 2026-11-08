"""Simulated payment provider (spec 6.3). Pure: no DB, no network, no card data."""
from pydantic import BaseModel

_TOKEN_MAP = {
    "tok_success": ("succeeded", "ok"),
    "tok_declined": ("declined", "card_declined"),
    "tok_pending": ("pending", "awaiting_confirmation"),
}


class SandboxResult(BaseModel):
    status: str  # succeeded | declined | pending
    reason: str


def charge(prospect_id: str, plan_id: str, amount: float, currency: str,
           test_token: str) -> SandboxResult:
    """Map a test token to an outcome. Anything not an exact known token is invalid."""
    status, reason = _TOKEN_MAP.get(test_token, ("declined", "invalid_token"))
    return SandboxResult(status=status, reason=reason)
