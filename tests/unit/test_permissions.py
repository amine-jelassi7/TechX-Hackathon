import itertools

import pytest

from app.permissions import ALL_TOOLS, is_allowed

# Written out independently of app.permissions.PERMISSIONS (spec 7.2 table).
EXPECTED = {
    "sales": {"qa.search", "product.read", "interaction.write", "message.send.payment"},
    "payment": {"product.read", "payment.create", "payment.read", "approval.create"},
    "insights": {
        "interaction.read_anonymized",
        "kpi.read",
        "lead.read_consented",
        "draft.write",
        "recommendation.write",
    },
    "analytics": {"kpi.read"},
}
EXPECTED["insights"].add("notification.write")


@pytest.mark.parametrize("actor,tool", list(itertools.product(EXPECTED, ALL_TOOLS)))
def test_matrix_every_actor_tool_pair(actor, tool):
    assert is_allowed(actor, tool) is (tool in EXPECTED[actor])


@pytest.mark.parametrize(
    "actor,tool",
    [
        ("sales", "payment.create"),
        ("sales", "lead.read_consented"),
        ("insights", "payment.create"),
        ("insights", "message.send.payment"),
        ("analytics", "draft.write"),
        ("analytics", "recommendation.write"),
        ("payment", "interaction.write"),
    ],
)
def test_explicit_denials_from_spec(actor, tool):
    assert not is_allowed(actor, tool)


@pytest.mark.parametrize(
    "actor,tool",
    [
        ("hacker", "kpi.read"),
        ("", "kpi.read"),
        ("SALES", "qa.search"),
        ("sales", "email.send"),
        ("sales", ""),
        ("sales", "qa.*"),
        ("*", "*"),
        ("orchestrator", "qa.search"),
    ],
)
def test_unknown_actor_or_tool_denied(actor, tool):
    assert not is_allowed(actor, tool)
