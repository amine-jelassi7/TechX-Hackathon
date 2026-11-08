"""Least-privilege permission matrix (spec 7.2 layer 3). Data, not logic: deny by default."""

ALL_TOOLS: tuple[str, ...] = (
    "qa.search",
    "product.read",
    "interaction.write",
    "message.send.payment",
    "payment.create",
    "payment.read",
    "approval.create",
    "interaction.read_anonymized",
    "kpi.read",
    "lead.read_consented",
    "draft.write",
    "recommendation.write",
)

PERMISSIONS: dict[str, frozenset[str]] = {
    "sales": frozenset({"qa.search", "product.read", "interaction.write", "message.send.payment"}),
    "payment": frozenset({"product.read", "payment.create", "payment.read", "approval.create"}),
    "insights": frozenset({
        "interaction.read_anonymized", "kpi.read", "lead.read_consented", "draft.write", "recommendation.write",
    }),
    "analytics": frozenset({"kpi.read"}),
}


def is_allowed(actor: str, tool: str) -> bool:
    """True only if `actor` is a known agent and `tool` is explicitly granted to it."""
    return tool in PERMISSIONS.get(actor, frozenset())
