import threading

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.audit import log_event, verify_chain
from app.models import AuditEvent


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def seed(session, n=5):
    return [log_event(session, trace_id="t1", actor="sales", event_type="chat_turn", decision="allow",
                      reasons=["r"], payload={"i": n_}) for n_ in range(n)]


def test_empty_chain_is_ok(session):
    assert verify_chain(session) == {"ok": True, "checked": 0, "first_bad_id": None}


def test_chain_links_and_verifies(session):
    evs = seed(session)
    assert [e.seq for e in evs] == [1, 2, 3, 4, 5]
    assert evs[0].prev_hash == "" and all(evs[i].prev_hash == evs[i - 1].hash for i in range(1, 5))
    assert verify_chain(session) == {"ok": True, "checked": 5, "first_bad_id": None}


@pytest.mark.parametrize("field,value", [
    ("decision", "allow_tampered"),
    ("actor", "evil"),
    ("reasons_json", ["changed"]),
    ("payload", {"i": 999}),
    ("trace_id", "other"),
])
def test_tampering_any_field_is_detected_with_row_id(session, field, value):
    evs = seed(session)
    target = evs[2]
    row = session.get(AuditEvent, target.id)
    setattr(row, field, value)
    session.add(row)
    session.commit()
    assert verify_chain(session) == {"ok": False, "checked": 2, "first_bad_id": target.id}


def test_deleting_a_middle_row_is_detected(session):
    evs = seed(session)
    session.delete(session.get(AuditEvent, evs[2].id))
    session.commit()
    res = verify_chain(session)
    assert not res["ok"] and res["first_bad_id"] == evs[3].id


def test_rewriting_hash_of_tampered_row_still_breaks_next_link(session):
    from app.audit import compute_hash
    evs = seed(session)
    row = session.get(AuditEvent, evs[1].id)
    row.decision = "block"
    row.hash = compute_hash(row.prev_hash, row)  # attacker recomputes this row's hash
    session.add(row)
    session.commit()
    assert verify_chain(session)["first_bad_id"] == evs[2].id


def test_payload_is_redacted_and_truncated_before_storage(session):
    ev = log_event(session, trace_id=None, actor="gateway", event_type="gateway_block", payload={
        "text": "my card is 4242 4242 4242 4242 and password is hunter2",
        "long": "x" * 1000, "card_number": "4242424242424242"})
    stored = session.get(AuditEvent, ev.id).payload
    assert "4242" not in str(stored) and "hunter2" not in str(stored)
    assert len(stored["long"]) <= 303
    assert verify_chain(session)["ok"]


def test_concurrent_writers_keep_a_valid_chain(engine):
    def worker(k):
        with Session(engine) as s:
            for i in range(10):
                log_event(s, trace_id=f"t{k}", actor="sales", event_type="chat_turn", payload={"i": i})

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with Session(engine) as s:
        seqs = [e.seq for e in s.exec(select(AuditEvent).order_by(AuditEvent.seq)).all()]
        assert seqs == list(range(1, 41))
        assert verify_chain(s) == {"ok": True, "checked": 40, "first_bad_id": None}
