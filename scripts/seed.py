"""Deterministic synthetic seed data. Idempotent: drops and recreates all tables."""
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from app import db
from app.models import (
    ApprovalRequest,
    Interaction,
    Lead,
    Payment,
    Prospect,
    QAEntry,
    SecurityAlert,
)
from app.schemas import Product

PRODUCT_PATH = Path(__file__).resolve().parent.parent / "data" / "product.json"
BASE_TS = datetime(2026, 1, 15, 9, 0, 0)

FIRST = ["Amine", "Sarra", "Yassine", "Ines", "Mehdi", "Nour", "Khaled", "Lina", "Omar", "Rania",
         "Slim", "Mariem", "Walid", "Hana", "Aymen", "Salma", "Fares", "Dorra", "Anis", "Emna"]
LAST = ["Ben Ali", "Trabelsi", "Mansouri", "Gharbi", "Jlassi", "Bouzid", "Hammami", "Saidi"]
DISPOSABLE = ["mailinator.test", "tempmail.test"]

# (text, interest_level, objection)
SNIPPETS = [
    ("C'est trop cher pour moi, vous avez une reduction ?", "medium", "price"),
    ("The price is too high for a student budget.", "medium", "price"),
    ("السعر مرتفع شوية", "low", "price"),
    ("Les horaires 18h-20h ne me conviennent pas.", "low", "schedule"),
    ("I work until 19:00, is the schedule flexible?", "medium", "schedule"),
    ("المواعيد ما تناسبنيش", "low", "schedule"),
    ("Je n'ai jamais fait de Python, est-ce que je peux suivre ?", "medium", "prerequisites"),
    ("Do I need prior experience in machine learning?", "low", "prerequisites"),
    ("Est-ce que cette formation est serieuse ? Je ne vous connais pas.", "low", "trust"),
    ("How do I know this bootcamp is legit?", "low", "trust"),
    ("Le certificat est-il reconnu par les employeurs ?", "medium", "certificate"),
    ("Does the certificate have any real value?", "medium", "certificate"),
    ("Je veux m'inscrire, comment payer ?", "high", None),
    ("I want to sign up for the Standard plan.", "high", None),
    ("بغيت نسجل في الدورة", "high", None),
    ("Quel est le prix du plan Pro ?", "medium", None),
    ("Tell me more about the bootcamp.", "low", None),
    ("Bonjour", "none", None),
]
AGENT_REPLY = "Merci pour votre message. Voici ce que je peux vous dire sur le bootcamp."


def seed(engine: Engine | None = None) -> dict[str, int]:
    eng = engine or db.engine
    rng = random.Random(42)
    random.seed(42)
    product = Product.model_validate(json.loads(PRODUCT_PATH.read_text(encoding="utf-8")))

    SQLModel.metadata.drop_all(eng)
    db.init_db(eng)

    with Session(eng) as s:
        prospects = []
        for i in range(40):
            domain = rng.choice(DISPOSABLE) if i % 8 == 7 else "example.test"
            prospects.append(Prospect(id=f"p{i:03d}", name=f"{FIRST[i % len(FIRST)]} {rng.choice(LAST)}",
                                      email=f"user{i:03d}@{domain}",
                                      created_at=BASE_TS + timedelta(hours=i)))
        s.add_all(prospects)
        _seed_interactions(s, prospects, rng)
        _seed_payments(s, prospects, _interested_prospect_ids(s))
        _seed_leads(s, rng)
        _seed_alerts(s)
        _seed_qa(s, product)
        s.commit()

    return counts(eng)


def _seed_interactions(s: Session, prospects: list[Prospect], rng: random.Random) -> None:
    # 40 sessions x 2 interactions (prospect + agent) = 80
    for i, p in enumerate(prospects):
        text, level, objection = rng.choice(SNIPPETS)
        ts = BASE_TS + timedelta(hours=i, minutes=5)
        sid = f"sess{i:03d}"
        s.add(Interaction(id=f"i{i:03d}a", session_id=sid, prospect_id=p.id, role="prospect",
                          text=text, source="qa", interest_level=level, objection=objection,
                          trace_id=f"tr{i:03d}", ts=ts))
        s.add(Interaction(id=f"i{i:03d}b", session_id=sid, prospect_id=p.id, role="agent",
                          text=AGENT_REPLY, source="qa", interest_level=level,
                          rating=rng.choice([None, 3, 4, 4, 5, 2]), trace_id=f"tr{i:03d}",
                          ts=ts + timedelta(seconds=3)))


def _interested_prospect_ids(s: Session) -> set[str]:
    """Prospects with a medium/high interest message (same rule as analytics.INTERESTED_LEVELS)."""
    s.flush()
    rows = s.exec(select(Interaction).where(Interaction.role == "prospect")).all()
    return {i.prospect_id for i in rows if i.interest_level in ("medium", "high") and i.prospect_id}


def _seed_payments(s: Session, prospects: list[Prospect], interested: set[str]) -> None:
    """30 payments. Payers come mostly from interested prospects (conversion ~55%); statuses are the ones
    the code produces (an owner-approved payment ends `succeeded`, with an `approved` ApprovalRequest)."""
    n = 0

    def add(pid: str, plan: str, amount: float, status: str, token: str, risk: float,
            reasons: list[str], ts: datetime) -> str:
        nonlocal n
        pay_id = f"pay{n:03d}"
        s.add(Payment(id=pay_id, prospect_id=pid, plan_id=plan, amount=amount,
                      currency="TND", status=status, test_token=token, risk_score=risk,
                      reasons=reasons, trace_id=f"trp{n:03d}", created_at=ts))
        n += 1
        return pay_id

    def approval(pay_id: str, status: str, reasons: list[str], ts: datetime) -> None:
        s.add(ApprovalRequest(id=f"apr{pay_id[3:]}", kind="payment", ref_id=pay_id, reasons=reasons,
                              status=status, trace_id=f"trp{pay_id[3:]}", created_at=ts,
                              decided_at=None if status == "pending" else ts + timedelta(hours=1)))

    rejected_payer = prospects[39]  # disposable e-mail domain (i % 8 == 7)
    pool = [p for p in prospects if p.id != rejected_payer.id]
    hot = [p.id for p in pool if p.id in interested]
    cold = [p.id for p in pool if p.id not in interested]
    # 14 succeeded (9 interested + 5 not), 5 declined, 3 pending, then the burst and the large amounts
    normal = ([(pid, "succeeded") for pid in hot[:9] + cold[:5]]
              + [(pid, "declined") for pid in hot[9:12] + cold[5:7]]
              + [(pid, "pending") for pid in hot[12:13] + cold[7:9]])
    tokens = {"succeeded": "tok_success", "declined": "tok_declined", "pending": "tok_pending"}
    for i, (pid, status) in enumerate(normal):
        add(pid, "std", 250.0, status, tokens[status], 0.0, [], BASE_TS + timedelta(days=1, hours=i))
    burst_payer, big_held, big_ok = cold[9], cold[10], cold[11]
    for k in range(5):  # burst: one prospect, 5 attempts within 5 minutes
        ts = BASE_TS + timedelta(days=2, minutes=k)
        held = k >= 3
        pay_id = add(burst_payer, "std", 250.0, "held_for_approval" if held else "declined", "tok_declined",
                     0.7 if held else 0.0, ["burst_attempts"] if held else [], ts)
        if held:
            approval(pay_id, "pending", ["burst_attempts"], ts)
    ts = BASE_TS + timedelta(days=3)  # large amounts (>= threshold): held, owner-approved, rejected
    approval(add(big_held, "pro", 450.0, "held_for_approval", "tok_success", 0.4, ["amount_over_threshold"], ts),
             "pending", ["amount_over_threshold"], ts)
    ts += timedelta(hours=1)
    approval(add(big_ok, "pro", 450.0, "succeeded", "tok_success", 0.4, ["amount_over_threshold"], ts),
             "approved", ["amount_over_threshold"], ts)
    ts += timedelta(hours=1)
    rej = ["amount_over_threshold", "disposable_email"]
    approval(add(rejected_payer.id, "pro", 450.0, "rejected", "tok_success", 0.6, rej, ts), "rejected", rej, ts)


def _seed_leads(s: Session, rng: random.Random) -> None:
    interests = ["python", "machine learning", "chatbots", "data analysis", "career change",
                 "AI for business"]
    sources = ["newsletter", "linkedin", "event", "referral", "cold_list"]
    for i in range(25):
        s.add(Lead(id=f"lead{i:03d}", first_name=FIRST[(i * 3) % len(FIRST)],
                   email=f"lead{i:03d}@example.test", interest=rng.choice(interests),
                   source=rng.choice(sources), consent=(i % 10) < 7))


def _seed_alerts(s: Session) -> None:
    s.add(SecurityAlert(id="alert000", ts=BASE_TS, alert_type="fraud", severity="high",
                        detail="Burst of payment attempts (seeded)"))
    s.add(SecurityAlert(id="alert001", ts=BASE_TS, alert_type="injection", severity="medium",
                        detail="Prompt injection attempt (seeded)"))


QA_IDS = ["qa_price", "qa_features", "qa_schedule", "qa_refund", "qa_prerequisites",
          "qa_certificate", "qa_audience", "qa_plans", "qa_contact"]


def _seed_qa(s: Session, product: Product) -> None:
    """Approved Q&A built by the mock generator (deterministic ids, stable across runs)."""
    from app.config import settings
    from app.services import qa

    mode, settings.llm_mode = settings.llm_mode, "mock"
    try:
        drafts = qa.generate_qa(product)
    finally:
        settings.llm_mode = mode
    for i, e in enumerate(drafts):
        e.id = QA_IDS[i] if i < len(QA_IDS) else f"qa_extra{i}"
        e.status = "approved"
        s.add(e)
    qa.invalidate()


def counts(eng: Engine) -> dict[str, int]:
    out = {}
    with Session(eng) as s:
        for m in (Prospect, Interaction, Payment, Lead, QAEntry, SecurityAlert):
            out[m.__name__] = len(s.exec(select(m)).all())
    return out


if __name__ == "__main__":
    print(seed())
