import pytest

from app.services.payments_sandbox import charge


@pytest.mark.parametrize("token,status,reason", [
    ("tok_success", "succeeded", "ok"),
    ("tok_declined", "declined", "card_declined"),
    ("tok_pending", "pending", "awaiting_confirmation"),
    ("tok_unknown", "declined", "invalid_token"),
    ("4242424242424242", "declined", "invalid_token"),
    ("TOK_SUCCESS", "declined", "invalid_token"),
    ("tok_success ", "declined", "invalid_token"),
    ("", "declined", "invalid_token"),
])
def test_token_mapping(token, status, reason):
    r = charge("p1", "std", 250.0, "TND", token)
    assert (r.status, r.reason) == (status, reason)
