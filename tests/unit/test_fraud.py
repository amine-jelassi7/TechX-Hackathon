from datetime import timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.gateway.fraud import level_for, score_payment
from app.models import Payment, Prospect, utcnow
from app.schemas import Plan, Policies, Product

PRODUCT = Product(
    id="p", name="n", tagline="t", description="d", audience=[], price=250.0, currency="TND",
    plans=[Plan(id="std", name="Standard", price=250.0), Plan(id="pro", name="Pro", price=450.0)],
    features=[], faq_seed=[], policies=Policies(), contact="contact@example.test", tone="x",
)
NOW = utcnow()


@pytest.fixture
def session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def add_prospect(s, pid="p1", email="a@example.test"):
    s.add(Prospect(id=pid, name="A", email=email))
    s.commit()


def add_payment(s, pid="p1", status="declined", minutes_ago=1.0, plan="std", amount=250.0):
    s.add(Payment(prospect_id=pid, plan_id=plan, amount=amount, status=status,
                  created_at=NOW - timedelta(minutes=minutes_ago)))
    s.commit()


def score(s, plan="std", amount=250.0, **kw):
    return score_payment(s, prospect_id="p1", plan_id=plan, amount=amount, product=PRODUCT, now=NOW, **kw)


def test_clean_request_is_low(session):
    add_prospect(session)
    r = score(session)
    assert (r.score, r.level, r.reasons, r.mismatch) == (0.0, "low", [], False)


def test_amount_over_threshold_alone_is_medium(session):
    add_prospect(session)
    r = score(session, plan="pro", amount=450.0)
    assert r.reasons == ["amount_over_threshold"]
    assert r.score == settings.fraud_w_amount and r.level == "medium"


@pytest.mark.parametrize("plan,amount,currency", [("std", 1.0, None), ("nope", 250.0, None),
                                                   ("pro", 250.0, None), ("std", 250.0, "USD")])
def test_mismatch_is_blocking_high(session, plan, amount, currency):
    add_prospect(session)
    r = score(session, plan=plan, amount=amount, currency=currency)
    assert r.mismatch and "plan_amount_mismatch" in r.reasons
    assert r.score >= settings.fraud_w_mismatch and r.level == "high"


def test_burst_needs_more_than_three_attempts_in_window(session):
    add_prospect(session)
    for _ in range(2):
        add_payment(session, status="succeeded")
    assert "burst_attempts" not in score(session).reasons  # 2 previous + this = 3, not more than 3
    add_payment(session, status="succeeded")
    r = score(session)
    assert "burst_attempts" in r.reasons and r.score == settings.fraud_w_burst


def test_burst_ignores_old_attempts(session):
    add_prospect(session)
    for _ in range(5):
        add_payment(session, status="succeeded", minutes_ago=settings.fraud_burst_window_minutes + 5)
    assert score(session).reasons == []


def test_exclude_current_payment_row(session):
    add_prospect(session)
    for _ in range(2):
        add_payment(session, status="succeeded")
    add_payment(session, status="created", minutes_ago=0.1)
    current = session.exec(select(Payment).where(Payment.status == "created")).one()
    assert "burst_attempts" not in score(session, exclude_payment_id=current.id).reasons


def test_declined_streak_then_new(session):
    add_prospect(session)
    add_payment(session, status="declined", minutes_ago=60)
    assert "declined_then_new_attempt" not in score(session).reasons
    add_payment(session, status="declined", minutes_ago=50)
    r = score(session)
    assert "declined_then_new_attempt" in r.reasons and r.score == settings.fraud_w_declined_then_new
    assert r.level == "medium"


def test_declined_streak_broken_by_success(session):
    add_prospect(session)
    add_payment(session, status="declined", minutes_ago=60)
    add_payment(session, status="declined", minutes_ago=50)
    add_payment(session, status="succeeded", minutes_ago=40)
    assert "declined_then_new_attempt" not in score(session).reasons


def test_disposable_email_domain(session):
    add_prospect(session, email="x@mailinator.test")
    r = score(session)
    assert r.reasons == ["disposable_email_domain"] and r.score == settings.fraud_w_disposable_email
    assert r.level == "low"  # 0.2 alone is below the medium threshold


def test_same_email_different_prospect(session):
    add_prospect(session, "p1", "dup@example.test")
    add_prospect(session, "p2", "DUP@example.test")
    r = score(session)
    assert r.reasons == ["same_email_multiple_prospects"] and r.level == "medium"


def test_additive_and_capped_at_one(session):
    add_prospect(session, "p1", "dup@mailinator.test")
    add_prospect(session, "p2", "dup@mailinator.test")
    for _ in range(3):
        add_payment(session, status="declined")
    r = score(session, plan="pro", amount=450.0)
    assert len(r.reasons) == 5 and r.score == 1.0 and r.level == "high"


@pytest.mark.parametrize("value,level", [(0.0, "low"), (0.29, "low"), (0.3, "medium"), (0.59, "medium"),
                                          (0.6, "high"), (1.0, "high")])
def test_level_thresholds(value, level):
    assert level_for(value) == level
