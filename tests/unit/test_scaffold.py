import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.models import Interaction, Lead, Payment, Prospect, QAEntry
from app.schemas import PayRequest, Product
from scripts.seed import seed

ROOT = Path(__file__).resolve().parents[2]


def test_product_json_validates():
    product = Product.model_validate(json.loads((ROOT / "data" / "product.json").read_text("utf-8")))
    assert {p.id for p in product.plans} == {"std", "pro"}
    assert product.currency == "TND"


def test_seed_counts_and_no_card_like_numbers():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    c = seed(eng)
    assert c["Prospect"] == 40 and c["Interaction"] == 80
    assert c["Payment"] == 30 and c["Lead"] == 25 and c["QAEntry"] >= 3
    with Session(eng) as s:
        texts = []
        for m in (Prospect, Interaction, Payment, Lead, QAEntry):
            texts += [json.dumps(r.model_dump(), default=str) for r in s.exec(select(m)).all()]
        assert not re.search(r"\d{13,19}", " ".join(texts).replace(" ", "").replace("-", ""))
        assert {p.status for p in s.exec(select(Payment)).all()} >= {
            "succeeded", "declined", "pending", "held_for_approval", "rejected"}
        # `approved` is an approval-request status; the code never writes it on a Payment.
        assert "approved" not in {p.status for p in s.exec(select(Payment)).all()}
        consent = [x.consent for x in s.exec(select(Lead)).all()]
        assert 0.6 <= sum(consent) / len(consent) <= 0.8


def test_request_schema_rejects_extra_field():
    with pytest.raises(ValidationError):
        PayRequest(session_id="s", plan_id="std", test_token="tok_success", card_number="x")


def test_seed_conversion_is_believable_and_held_payments_have_approvals():
    from app.models import ApprovalRequest
    from app.services import analytics
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    seed(eng)
    with Session(eng) as s:
        k = analytics.get_kpis(s)
        assert k.converted_prospects <= k.interested_prospects
        assert 0.45 <= k.conversion_rate <= 0.65
        assert k.converted_prospects >= 0.6 * k.paid_prospects  # payers come mostly from interested prospects
        held = {p.id for p in s.exec(select(Payment)).all() if p.status == "held_for_approval"}
        reqs = [a for a in s.exec(select(ApprovalRequest)).all() if a.kind == "payment"]
        assert held and held <= {a.ref_id for a in reqs if a.status == "pending"}
        assert {a.status for a in reqs} == {"pending", "approved", "rejected"}
