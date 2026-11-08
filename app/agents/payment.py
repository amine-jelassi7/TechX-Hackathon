"""Payment Agent (spec 6.3): verify plan/amount against product.json, gateway-check `payment.create`,
hold for human approval when escalated, otherwise call the sandbox. Plus owner approve/reject."""
import re
from typing import Any, ClassVar

from sqlalchemy import update
from sqlmodel import Session, select

from app import audit, llm
from app.agents.base import BaseAgent, ToolRefused
from app.gateway import SecurityGateway
from app.models import ApprovalRequest, Payment, utcnow
from app.schemas import TEST_TOKEN_PATTERN, AgentMessage, Product
from app.services import payments_sandbox

TOOLS = ("product.read", "payment.create", "payment.read", "approval.create")

MESSAGES = {
    "succeeded": "Payment succeeded (test mode). Welcome aboard!",
    "declined": "Payment declined (test mode).",
    "pending": "Payment is pending confirmation (test mode).",
    "held_for_approval": "Payment is held for review by a team member and will be processed once approved.",
    "rejected": "This payment request was rejected.",
    "awaiting_token": "Choose a plan and use the test payment buttons to pay.",
}


class PaymentAgent(BaseAgent):
    fallback_payload: ClassVar[dict[str, Any]] = {"status": "error", "message": "Payment service unavailable, please retry.",
                        "payment_id": None}

    def __init__(self, session: Session, gateway: SecurityGateway | None = None, orchestrator=None,
                 product: Product | None = None):
        super().__init__("payment", TOOLS, session, gateway, orchestrator)
        self.product = product or llm.load_product()

    def _result(self, msg: AgentMessage, status: str, payment_id: str | None, message: str | None = None):
        return self.reply(msg, "payment_result", {"status": status, "payment_id": payment_id,
                                                  "message": message or MESSAGES.get(status, status)},
                          recipient="sales" if msg.sender == "sales" else msg.sender)

    def handle(self, msg: AgentMessage) -> AgentMessage:
        if msg.type != "payment_request":
            return self._result(msg, "rejected", None, "unsupported message")
        p = msg.payload
        prospect_id, plan_id = str(p.get("prospect_id") or ""), str(p.get("plan_id") or "")
        token = p.get("test_token")
        plan = next((x for x in self.product.plans if x.id == plan_id), None)
        if not prospect_id or plan is None:
            self._audit(msg, "payment_rejected", None, ["unknown_plan_or_prospect"], {"plan_id": plan_id[:32]})
            return self._result(msg, "rejected", None, "Unknown plan.")
        if not token:  # chat-side intent only: nothing to charge yet
            return self._result(msg, "awaiting_token", None)
        if not isinstance(token, str) or not re.fullmatch(TEST_TOKEN_PATTERN, token):
            # Never persist (or echo) unvalidated input: it could be a real card number.
            self._audit(msg, "payment_rejected", None, ["invalid_test_token"], {"plan_id": plan.id})
            return self._result(msg, "rejected", None, "Invalid test token.")

        # Idempotency: one Payment per (trace_id, prospect, plan). A repeated/retried message finds the row.
        pay = self._existing(msg, prospect_id, plan.id)
        if pay is not None and pay.status != "created":
            return self._result(msg, pay.status, pay.id)
        if pay is None:
            # Amount ALWAYS comes from product.json, never from the request.
            pay = Payment(prospect_id=prospect_id, plan_id=plan.id, amount=plan.price,
                          currency=self.product.currency, status="created", test_token=token,
                          trace_id=msg.trace_id)
            self.session.add(pay)
            self.session.commit()
        args = {"prospect_id": prospect_id, "plan_id": plan.id, "amount": plan.price,
                "currency": self.product.currency, "test_token": str(token), "payment_id": pay.id}
        if p.get("session_id"):
            args["session_id"] = str(p["session_id"])
        ctx = self.ctx(msg, session_id=p.get("session_id"), prospect_id=prospect_id)
        try:
            decision = self.use_tool("payment.create", args, ctx, allow_escalate=True)
        except ToolRefused as exc:
            return self._finalize_block(msg, pay, exc.decision.reasons, exc.decision.risk)
        pay.risk_score, pay.reasons = decision.risk, list(decision.reasons)
        if decision.action == "escalate":
            return self._hold(msg, ctx, pay, decision.reasons)
        res = payments_sandbox.charge(pay.prospect_id, pay.plan_id, pay.amount, pay.currency, str(token))
        pay.status = res.status
        self._save(pay)
        self._audit(msg, "payment_created", pay, [res.reason], {"status": pay.status, "amount": pay.amount})
        self._notify_insights(msg, pay)
        return self._result(msg, pay.status, pay.id)

    def _existing(self, msg: AgentMessage, prospect_id: str, plan_id: str) -> Payment | None:
        if not msg.trace_id:
            return None
        return self.session.exec(select(Payment).where(
            Payment.trace_id == msg.trace_id, Payment.prospect_id == prospect_id,
            Payment.plan_id == plan_id)).first()

    def _notify_insights(self, msg: AgentMessage, pay: Payment) -> None:
        """Spec 6.3 step 4: a settled payment is visible to Insights/Analytics in the live feed.

        Notification only (`record`): the permission matrix has no payment -> insights message right."""
        if pay.status == "succeeded" and self.orchestrator is not None:
            self.orchestrator.record(AgentMessage(
                trace_id=msg.trace_id, sender="payment", recipient="insights", type="payment_result",
                payload={"payment_id": pay.id, "status": pay.status, "plan_id": pay.plan_id,
                         "amount": pay.amount}))

    # ---- branches --------------------------------------------------------------------------
    def _finalize_block(self, msg: AgentMessage, pay: Payment, reasons: list[str], risk: float):
        pay.status, pay.reasons, pay.risk_score = "rejected", list(reasons), risk
        self._save(pay)
        self._audit(msg, "payment_rejected", pay, reasons, {"stage": "gateway_block"})
        return self._result(msg, "rejected", pay.id, "The payment request was blocked by security checks.")

    def _hold(self, msg: AgentMessage, ctx, pay: Payment, reasons: list[str]):
        pay.status = "held_for_approval"
        self._save(pay)
        ref = {"kind": "payment", "ref_id": pay.id, "reasons": list(reasons)}
        try:
            self.use_tool("approval.create", ref, ctx)
        except ToolRefused:
            pay.status, pay.reasons = "rejected", [*reasons, "approval_unavailable"]
            self._save(pay)
            self._audit(msg, "payment_hold_failed", pay, pay.reasons, {"amount": pay.amount})
            return self._result(msg, "rejected", pay.id, "The payment could not be queued for review.")
        self.session.add(ApprovalRequest(kind="payment", ref_id=pay.id, reasons=list(reasons), trace_id=msg.trace_id))
        self.session.commit()
        self._audit(msg, "payment_held", pay, reasons, {"amount": pay.amount, "risk": pay.risk_score})
        return self._result(msg, "held_for_approval", pay.id)

    def _save(self, pay: Payment) -> None:
        self.session.add(pay)
        self.session.commit()

    def _audit(self, msg: AgentMessage, event: str, pay: Payment | None, reasons: list[str], extra: dict) -> None:
        audit.log_event(self.session, trace_id=msg.trace_id, actor="payment", event_type=event,
                        decision=pay.status if pay else None, reasons=reasons,
                        payload={"payment_id": pay.id if pay else None, **extra})


# ---- owner decisions (human in the loop) --------------------------------------------------------
class ApprovalStateError(Exception):
    """The approval request is not pending."""


def _pending(session: Session, approval_id: str) -> ApprovalRequest:
    req = session.get(ApprovalRequest, approval_id)
    if req is None:
        raise KeyError(approval_id)
    if req.status != "pending":
        raise ApprovalStateError(approval_id)
    return req


def _claim(session: Session, req: ApprovalRequest, status: str) -> None:
    """Atomically move pending -> `status`. Only one concurrent decider wins; the other gets ApprovalStateError."""
    res = session.execute(update(ApprovalRequest).where(
        ApprovalRequest.id == req.id, ApprovalRequest.status == "pending").values(status=status, decided_at=utcnow()))
    session.commit()
    if res.rowcount != 1:
        raise ApprovalStateError(req.id)
    session.refresh(req)


def approve(session: Session, approval_id: str) -> tuple[ApprovalRequest, Payment | None]:
    """Approve: for payments, run the sandbox with the stored test token and record the outcome."""
    req = _pending(session, approval_id)
    _claim(session, req, "approved")  # before the charge: a second approve can never charge again
    pay = session.get(Payment, req.ref_id) if req.kind == "payment" and req.ref_id else None
    if pay is not None:
        res = payments_sandbox.charge(pay.prospect_id, pay.plan_id, pay.amount, pay.currency, pay.test_token or "")
        pay.status = res.status
        session.add(pay)
        session.commit()
        session.refresh(pay)
    audit.log_event(session, trace_id=req.trace_id, actor="owner", event_type=f"{req.kind}_approved" if req.kind != "payment" else "payment_approved",
                    decision="approved", payload={"approval_id": req.id, "kind": req.kind, "payment_id": req.ref_id,
                                                  "status": pay.status if pay else None})
    return req, pay


def reject(session: Session, approval_id: str) -> tuple[ApprovalRequest, Payment | None]:
    req = _pending(session, approval_id)
    _claim(session, req, "rejected")
    pay = session.get(Payment, req.ref_id) if req.kind == "payment" and req.ref_id else None
    if pay is not None:
        pay.status = "rejected"
        session.add(pay)
        session.commit()
        session.refresh(pay)
    audit.log_event(session, trace_id=req.trace_id, actor="owner", event_type=f"{req.kind}_rejected" if req.kind != "payment" else "payment_rejected",
                    decision="rejected", payload={"approval_id": req.id, "kind": req.kind, "payment_id": req.ref_id})
    return req, pay
