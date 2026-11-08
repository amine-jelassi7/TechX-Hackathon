"""Base class for agents: identity, tool allow-list, and the gateway-gated tool helper."""

import copy
from typing import Any, ClassVar

from sqlmodel import Session

from app.gateway import SecurityGateway
from app.schemas import AgentMessage, Ctx, Decision


class ToolRefused(Exception):
    """The gateway (or the agent's own allow-list) refused a tool call."""

    def __init__(self, tool: str, decision: Decision):
        super().__init__(f"{tool}: {decision.action} {decision.reasons}")
        self.tool = tool
        self.decision = decision


class BaseAgent:
    name: str = "agent"
    # Returned (as payload) by the orchestrator if handle() times out or raises on every attempt.
    fallback_payload: ClassVar[dict[str, Any]] = {
        "reply": "Sorry, something went wrong. Please retry."
    }
    # Message types whose handler can safely run twice (no rows written, no money moved). The orchestrator
    # retries ONLY these; everything else runs exactly once, even after a timeout.
    idempotent_types: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        name: str,
        allowed_tools: frozenset[str] | set[str] | tuple[str, ...],
        session: Session,
        gateway: SecurityGateway | None = None,
        orchestrator: Any = None,
    ):
        self.name = name
        self.allowed_tools = frozenset(allowed_tools)
        self.session = session
        self.gateway = gateway or SecurityGateway(session)
        self.orchestrator = orchestrator

    def bind(self, session: Session) -> "BaseAgent":
        """A copy of this agent working on `session` (one DB session per attempt, never shared across threads)."""
        clone = copy.copy(self)
        clone.session = session
        clone.gateway = SecurityGateway(session, self.gateway.product)
        return clone

    def ctx(self, msg: AgentMessage, **extra: Any) -> Ctx:
        """Gateway context with this agent as the actor (the permission matrix keys on it)."""
        return Ctx(trace_id=msg.trace_id, actor=self.name, **extra)

    def handle(self, msg: AgentMessage) -> AgentMessage | dict:
        raise NotImplementedError

    def reply(
        self, msg: AgentMessage, type_: str, payload: dict, recipient: str | None = None
    ) -> AgentMessage:
        return AgentMessage(
            trace_id=msg.trace_id,
            sender=self.name,
            recipient=recipient or msg.sender,
            type=type_,
            payload=payload,
        )

    def use_tool(
        self, tool: str, args: dict, ctx: Ctx, *, allow_escalate: bool = False
    ) -> Decision:
        """Run a tool call through the gateway. Raises ToolRefused unless the decision is allow
        (or escalate when the caller can handle it, e.g. the payment agent holding a payment)."""
        if ctx.actor != self.name:
            ctx = ctx.model_copy(update={"actor": self.name})
        if tool not in self.allowed_tools:
            # Still routed through the gateway so the denial is audited and alerted.
            decision = self.gateway.check_tool_call(tool, args, ctx)
            if (
                decision.action == "allow"
            ):  # matrix says yes but the agent never declared it: refuse
                decision = Decision(action="block", reasons=[f"undeclared_tool:{tool}"], risk=1.0)
            raise ToolRefused(tool, decision)
        decision = self.gateway.check_tool_call(tool, args, ctx)
        if decision.action == "allow" or (allow_escalate and decision.action == "escalate"):
            return decision
        raise ToolRefused(tool, decision)
