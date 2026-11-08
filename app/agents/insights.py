"""Insights Agent (spec 6.5): thin agent wrapper over `services.insights`, reachable through the orchestrator.

Message types: `insights_analyze` (mode A, recommendations) and `insights_opportunities` (mode B, lead
ranking + outreach drafts). Every tool use goes through the gateway under the `insights` actor. Nothing
is sent anywhere: drafts are stored for the owner.
"""
from typing import Any, ClassVar

from sqlmodel import Session

from app.agents.base import BaseAgent, ToolRefused
from app.gateway import SecurityGateway
from app.schemas import AgentMessage
from app.services import insights as service

TOOLS = ("interaction.read_anonymized", "kpi.read", "lead.read_consented", "draft.write", "recommendation.write")

EMPTY: dict[str, Any] = {"needs": [], "objections": [], "recommendations": [], "items": []}


class InsightsAgent(BaseAgent):
    fallback_payload: ClassVar[dict[str, Any]] = EMPTY

    def __init__(self, session: Session, gateway: SecurityGateway | None = None, orchestrator=None):
        super().__init__("insights", TOOLS, session, gateway, orchestrator)

    def handle(self, msg: AgentMessage) -> AgentMessage:
        ctx = self.ctx(msg)
        try:
            if msg.type == "insights_analyze":
                for tool in ("interaction.read_anonymized", "kpi.read", "recommendation.write"):
                    self.use_tool(tool, {}, ctx)
                n = int(msg.payload.get("n", 50))
                out = service.analyze(self.session, n).model_dump(mode="json")
                return self.reply(msg, "insights_analyze_result", out)
            if msg.type == "insights_opportunities":
                for tool in ("lead.read_consented", "draft.write"):
                    self.use_tool(tool, {}, ctx)
                items = service.opportunities(self.session, top_k=int(msg.payload.get("top_k", 5)))
                return self.reply(msg, "insights_opportunities_result", {"items": items})
        except ToolRefused as exc:
            return self.reply(msg, "error", {**EMPTY, "error": f"refused:{exc.tool}"})
        return self.reply(msg, "error", {**EMPTY, "error": "unsupported message"})


def run(session: Session, mode: str) -> tuple[dict, bool]:
    """Owner-triggered run through the orchestrator (so it shows in the live feed as agent_message events).

    Returns (payload, ok). On timeout/error the orchestrator's fallback payload (empty lists) comes back
    with ok=False and an `agent_error` event is in the audit log."""
    import uuid

    from app.orchestrator import build_orchestrator

    msg_type = {"analyze": "insights_analyze", "opportunities": "insights_opportunities"}[mode]
    reply = build_orchestrator(session).dispatch(AgentMessage(
        trace_id=uuid.uuid4().hex, sender="orchestrator", recipient="insights", type=msg_type, payload={}))
    return reply.payload, reply.type not in ("agent_error", "error")
