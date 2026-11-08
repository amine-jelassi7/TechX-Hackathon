"""Security gateway (spec 7.1/7.2): the single choke point for input, output and tool calls.

Layers, cheapest first: deterministic filters -> LLM classifier (only when uncertain) -> permission matrix ->
output/argument screening -> human-in-the-loop. Every call writes a hash-chained audit event; block and
escalate decisions also write a SecurityAlert row.
"""
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session

from app import audit
from app.config import settings
from app.gateway import classifier as _classifier
from app.gateway import filters
from app.gateway.fraud import score_payment
from app.models import SecurityAlert
from app.permissions import is_allowed
from app.schemas import Ctx, Decision, Product

PRODUCT_PATH = Path(__file__).resolve().parents[2] / "data" / "product.json"
CLASSIFIER_BLOCK_CONFIDENCE = 0.5


@lru_cache(maxsize=1)
def _load_product() -> Product:
    return Product.model_validate(json.loads(PRODUCT_PATH.read_text("utf-8")))


# ---- tool argument schemas (extra fields rejected: no card fields can sneak in) ----------------
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaymentCreateArgs(_Strict):
    prospect_id: str = Field(min_length=1, max_length=64)
    plan_id: str = Field(min_length=1, max_length=32)
    amount: float = Field(gt=0, lt=1_000_000)
    currency: str | None = Field(default=None, max_length=3)
    test_token: str | None = Field(default=None, pattern=r"^tok_[a-z0-9_]{1,32}$")
    payment_id: str | None = None  # existing Payment row to exclude from fraud history
    session_id: str | None = None


class PaymentReadArgs(_Strict):
    payment_id: str | None = None
    prospect_id: str | None = None


class ApprovalCreateArgs(_Strict):
    kind: str = Field(pattern=r"^(payment|escalation|draft)$")
    ref_id: str | None = None
    reasons: list[str] = Field(default_factory=list, max_length=20)


class QASearchArgs(_Strict):
    query: str = Field(min_length=1, max_length=1000)


class InteractionWriteArgs(BaseModel):
    model_config = ConfigDict(extra="allow")
    session_id: str
    text: str = Field(max_length=5000)


TOOL_ARG_MODELS: dict[str, type[BaseModel]] = {
    "payment.create": PaymentCreateArgs,
    "payment.read": PaymentReadArgs,
    "approval.create": ApprovalCreateArgs,
    "qa.search": QASearchArgs,
    "interaction.write": InteractionWriteArgs,
}


def _strings(obj: Any, depth: int = 0) -> list[str]:
    if depth > 5:
        return []
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for k, v in obj.items() for s in [str(k), *_strings(v, depth + 1)]]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in _strings(v, depth + 1)]
    return []


class SecurityGateway:
    """Stateless apart from the DB session used for audit events, alerts and fraud history.

    Escalation contract: `check_tool_call` returning `escalate` does NOT create the ApprovalRequest. The
    caller (the payment agent) must create it (and the held Payment) after seeing the decision.
    """

    def __init__(self, session: Session, product: Product | None = None):
        self.session = session
        self.product = product or _load_product()

    # ---- helpers -------------------------------------------------------------------------
    def _alert(self, ctx: Ctx, alert_type: str, severity: str, reasons: list[str]) -> None:
        self.session.add(SecurityAlert(
            trace_id=ctx.trace_id, alert_type=alert_type, severity=severity,
            detail=f"actor={ctx.actor}; " + ", ".join(reasons)[:400],
        ))
        self.session.commit()

    def _finish(self, ctx: Ctx, kind: str, decision: Decision, payload: dict, *,
                alert_type: str | None = None, severity: str = "medium",
                event_type: str | None = None) -> Decision:
        if event_type is None:
            event_type = {"block": "gateway_block", "escalate": "gateway_escalate"}.get(
                decision.action, "gateway_allow")
        audit.log_event(
            self.session, trace_id=ctx.trace_id, actor=ctx.actor, event_type=event_type,
            decision=decision.action, reasons=decision.reasons,
            payload={"check": kind, "risk": decision.risk, **payload},
        )
        if decision.action in ("block", "escalate"):
            self._alert(ctx, alert_type or "injection", severity, decision.reasons)
        return decision

    # ---- input -----------------------------------------------------------------------------
    def check_input(self, text: str, ctx: Ctx) -> Decision:
        """Screen untrusted user/lead text before any agent sees or stores it."""
        res = filters.analyze(text)
        payload = {"text": res.redacted}

        def block(reasons, risk, alert, sev="high", sanitized=None):
            d = Decision(action="block", reasons=reasons, risk=risk, sanitized=sanitized)
            return self._finish(ctx, "input", d, payload, alert_type=alert, severity=sev)

        if res.too_long:
            return block(["input_too_long"], 0.5, "injection", "low")
        if res.card or res.secret:
            return block([r for r in res.reasons if r in ("card_number", "secret_pattern")], 1.0,
                         "card_data", sanitized=res.redacted)
        if res.score >= settings.classifier_high:
            return block(res.reasons, res.score, "injection")
        reasons = list(res.reasons)
        risk = res.score
        if settings.classifier_low <= res.score < settings.classifier_high:
            verdict = _classifier.classify(res.text)
            if verdict.failed:
                d = Decision(action="escalate", reasons=[*reasons, "classifier_unavailable"], risk=res.score)
                return self._finish(ctx, "input", d, payload, alert_type="injection")
            if verdict.injection and verdict.confidence >= CLASSIFIER_BLOCK_CONFIDENCE:
                return block([*reasons, "classifier_injection"], max(res.score, verdict.confidence), "injection")
        if res.markup:
            d = Decision(action="sanitize", reasons=reasons, risk=max(risk, 0.4), sanitized=res.stripped)
        elif res.invisible_removed:
            d = Decision(action="sanitize", reasons=[*reasons, "invisible_chars_removed"], risk=risk,
                         sanitized=res.text)
        else:
            d = Decision(action="allow", reasons=reasons, risk=risk)
        return self._finish(ctx, "input", d, payload)

    # ---- output ----------------------------------------------------------------------------
    def check_output(self, text: str, ctx: Ctx) -> Decision:
        """Screen an agent reply/draft: leaked prompts, secrets, card numbers, URLs other than the contact."""
        found = filters.output_findings(text, (self.product.contact,))
        payload = {"text": filters.redact(text)}
        if found:
            reasons = [f"{k}:{'|'.join(v)}"[:120] for k, v in found.items()]
            sanitized = filters.redact(text) if ({"card_number", "secret"} & found.keys()) else None
            d = Decision(action="block", reasons=reasons, risk=1.0, sanitized=sanitized)
            return self._finish(ctx, "output", d, payload, alert_type="output_leak", severity="high")
        return self._finish(ctx, "output", Decision(action="allow"), payload)

    # ---- tool calls ------------------------------------------------------------------------
    def check_tool_call(self, tool: str, args: dict, ctx: Ctx) -> Decision:
        """Gate a tool call: permission matrix, argument schema/content screening, payment fraud checks.

        For `payment.create`: plan/amount mismatch with product.json -> block; fraud level medium/high or
        amount >= PAYMENT_APPROVAL_THRESHOLD -> `escalate` (the caller must create the held Payment and the
        ApprovalRequest itself; the gateway never does).
        """
        args = args if isinstance(args, dict) else {}
        shown = {"tool": tool, "args": args}

        if not is_allowed(ctx.actor, tool):
            d = Decision(action="block", reasons=[f"permission_denied:{ctx.actor}->{tool}"], risk=1.0)
            return self._finish(ctx, "tool", d, shown, alert_type="permission_denied", severity="high",
                                event_type="permission_denied")

        model = TOOL_ARG_MODELS.get(tool)
        parsed = None
        if model is not None:
            try:
                parsed = model.model_validate(args)
            except ValidationError as exc:
                bad = sorted({".".join(str(x) for x in e["loc"]) or "?" for e in exc.errors()})
                card_key = any(filters._SENSITIVE_KEY.search(b) for b in bad)
                d = Decision(action="block", reasons=[f"invalid_args:{b}" for b in bad], risk=0.9)
                return self._finish(ctx, "tool", d, shown, alert_type="card_data" if card_key else "invalid_args",
                                    severity="high" if card_key else "medium")

        for s in _strings(args):
            res = filters.analyze(s, max_length=5000)
            if res.card or res.secret:
                d = Decision(action="block", reasons=["card_or_secret_in_args"], risk=1.0)
                return self._finish(ctx, "tool", d, shown, alert_type="card_data", severity="high")
            if res.score >= settings.classifier_high:
                d = Decision(action="block", reasons=["injection_in_args", *res.reasons], risk=res.score)
                return self._finish(ctx, "tool", d, shown, alert_type="injection", severity="high")

        if tool == "payment.create" and isinstance(parsed, PaymentCreateArgs):
            return self._check_payment(parsed, ctx, shown)
        return self._finish(ctx, "tool", Decision(action="allow"), shown)

    def _check_payment(self, a: PaymentCreateArgs, ctx: Ctx, shown: dict) -> Decision:
        fr = score_payment(
            self.session, prospect_id=a.prospect_id, plan_id=a.plan_id, amount=a.amount, product=self.product,
            currency=a.currency, exclude_payment_id=a.payment_id,
        )
        shown = {**shown, "fraud": {"score": fr.score, "level": fr.level, "reasons": fr.reasons}}
        if fr.mismatch:
            d = Decision(action="block", reasons=fr.reasons, risk=fr.score)
            return self._finish(ctx, "tool", d, shown, alert_type="fraud", severity="high")
        if fr.level != "low" or a.amount >= settings.payment_approval_threshold:
            reasons = fr.reasons or ["amount_over_threshold"]
            d = Decision(action="escalate", reasons=reasons, risk=max(fr.score, 0.3))
            return self._finish(ctx, "tool", d, shown, alert_type="fraud",
                                severity="high" if fr.level == "high" else "medium")
        return self._finish(ctx, "tool", Decision(action="allow", risk=fr.score), shown)
