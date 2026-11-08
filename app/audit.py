"""Hash-chained, tamper-evident audit log (spec 7.4).

hash = sha256(prev_hash + canonical_json(fields)), where the fields include the redacted payload.
Chain order is the application-assigned `seq` (not the timestamp), so it is deterministic.
"""
import hashlib
import json
import threading
from typing import Any

from sqlmodel import Session, select

from app.gateway.filters import redact_payload
from app.models import AuditEvent, utcnow

_lock = threading.Lock()
GENESIS = ""


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fields(ev: AuditEvent) -> dict[str, Any]:
    return {
        "id": ev.id,
        "seq": ev.seq,
        "ts": ev.ts.isoformat(),
        "trace_id": ev.trace_id,
        "actor": ev.actor,
        "event_type": ev.event_type,
        "decision": ev.decision,
        "reasons": list(ev.reasons_json or []),
        "payload": ev.payload or {},
    }


def compute_hash(prev_hash: str, ev: AuditEvent) -> str:
    return _sha(prev_hash + canonical_json(_fields(ev)))


def log_event(
    session: Session,
    *,
    trace_id: str | None,
    actor: str,
    event_type: str,
    decision: str | None = None,
    reasons: list[str] | None = None,
    payload: dict | None = None,
) -> AuditEvent:
    """Append one event. The payload is redacted (no card numbers/secrets, strings cut to 300 chars)."""
    clean = redact_payload(payload or {})
    clean_reasons = [str(r)[:200] for r in (reasons or [])]
    with _lock:
        last = session.exec(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1)).first()
        next_seq = (last.seq if last else 0) + 1
        ev = AuditEvent(
            ts=utcnow(), trace_id=trace_id, actor=actor, event_type=event_type, decision=decision,
            reasons_json=clean_reasons, payload=clean, seq=next_seq,
        )
        ev.prev_hash = last.hash if last else GENESIS
        ev.payload_hash = _sha(canonical_json(clean))
        ev.hash = compute_hash(ev.prev_hash, ev)
        session.add(ev)
        session.commit()
        session.refresh(ev)
    return ev


def verify_chain(session: Session) -> dict[str, Any]:
    """Recompute every hash in `seq` order. Returns {"ok", "checked", "first_bad_id"}."""
    prev = GENESIS
    checked = 0
    rows = session.exec(select(AuditEvent).order_by(AuditEvent.seq)).all()
    for ev in rows:
        good = (
            ev.prev_hash == prev
            and ev.payload_hash == _sha(canonical_json(ev.payload or {}))
            and ev.hash == compute_hash(prev, ev)
        )
        if not good:
            return {"ok": False, "checked": checked, "first_bad_id": ev.id}
        prev = ev.hash
        checked += 1
    return {"ok": True, "checked": checked, "first_bad_id": None}

