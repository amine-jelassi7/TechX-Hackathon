"""Anonymous visitors: one synthetic Prospect per chat session (no PII is collected)."""
import hashlib

from sqlmodel import Session

from app.models import Prospect


def prospect_for_session(session: Session, session_id: str) -> Prospect:
    pid = "pr_" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    prospect = session.get(Prospect, pid)
    if prospect is None:
        prospect = Prospect(id=pid, name="Visitor", email=f"{pid}@visitor.test")
        session.add(prospect)
        session.commit()
        session.refresh(prospect)
    return prospect
