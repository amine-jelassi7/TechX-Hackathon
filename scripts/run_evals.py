"""Offline evaluation harness (spec 10.3): Q&A matching and gateway attack set -> data/eval_results.json.

Runs in LLM_MODE=mock against a fresh in-memory SQLite DB seeded with the seed helpers (data/app.db is
never touched). Always exits 0: it is a report, misses are printed.
"""
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["LLM_MODE"] = "mock"
os.environ["EMBEDDING_MODE"] = "tfidf"

from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from app.config import settings
from app.gateway import SecurityGateway
from app.schemas import Ctx, PayRequest
from app.services import qa

EVALS = ROOT / "tests" / "evals"
OUT = ROOT / "data" / "eval_results.json"
STRICT = {"allow": 0, "sanitize": 1, "escalate": 2, "block": 3}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]


def run_qa(session: Session, questions: list[dict]) -> dict:
    details = []
    for q in questions:
        entry, score = qa.match(session, q["question"])
        got = entry.id if entry else "none"
        details.append({"id": q["id"], "question": q["question"], "lang": q.get("lang"),
                        "expected": q["expected"], "got": got, "score": round(score, 3),
                        "ok": got == q["expected"]})
    pos = [d for d in details if d["expected"] != "none"]
    neg = [d for d in details if d["expected"] == "none"]
    rate = lambda ds: round(sum(d["ok"] for d in ds) / len(ds), 3) if ds else 1.0
    return {"total": len(details), "accuracy": rate(pos), "refusal_rate": rate(neg),
            "details": details}


def _layer(reasons: list[str], default: str = "filters") -> str:
    if any(r == "classifier_injection" or r.startswith("classifier") for r in reasons):
        return "classifier"
    if any(r.startswith("permission_denied") for r in reasons):
        return "permissions"
    return default


def _attack_chat(gw: SecurityGateway, ctx: Ctx, payload: str) -> tuple[str, str]:
    d = gw.check_input(payload, ctx)
    return d.action, ("-" if d.action == "allow" else _layer(d.reasons))


def _attack_lead(gw: SecurityGateway, ctx: Ctx, payload: str) -> tuple[str, str]:
    """Lead fields are untrusted data: screen on intake, then make sure they cannot reach a draft raw."""
    d = gw.check_input(payload, ctx)
    if d.action in ("block", "escalate"):
        return d.action, _layer(d.reasons)
    safe = d.sanitized if d.sanitized is not None else payload
    out = gw.check_output(f"Bonjour {safe}, voici notre offre. Pour vous desinscrire repondez STOP.", ctx)
    if out.action == "block":
        return "block", "output"
    return d.action, ("filters" if d.action == "sanitize" else "-")


def _attack_pay(gw: SecurityGateway, ctx: Ctx, payload: dict) -> tuple[str, str]:
    try:
        PayRequest.model_validate(payload)
    except ValidationError:
        return "block", "schema"
    args = {"prospect_id": payload["session_id"], "plan_id": payload["plan_id"],
            "amount": payload.get("amount", 1), "test_token": payload["test_token"]}
    d = gw.check_tool_call("payment.create", args, ctx)
    if d.action == "allow":
        return "allow", "-"
    fraud = any("amount" in r or "fraud" in r or "mismatch" in r for r in d.reasons)
    return d.action, _layer(d.reasons, "fraud" if fraud else "gateway")


def run_attacks(session: Session, attacks: list[dict]) -> dict:
    gw = SecurityGateway(session)
    details = []
    for a in attacks:
        ctx = Ctx(trace_id=f"eval_{a['id']}", actor="payment" if a["channel"] == "pay_api" else "sales",
                  session_id=f"sess_{a['id']}")
        fn = {"chat": _attack_chat, "lead_field": _attack_lead, "pay_api": _attack_pay}[a["channel"]]
        got, layer = fn(gw, ctx, a["payload"])
        details.append({"id": a["id"], "channel": a["channel"], "category": a.get("category"),
                        "expected": a["expected"], "got": got, "layer": layer,
                        "ok": STRICT[got] >= STRICT[a["expected"]]})
    return {"total": len(details), "blocked": sum(d["got"] != "allow" for d in details),
            "details": details}


def run(out: Path | None = OUT) -> dict:
    from seed import seed  # scripts/seed.py

    settings.llm_mode = "mock"
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    seed(eng)
    qa.invalidate()
    with Session(eng) as s:
        qa_res = run_qa(s, load_jsonl(EVALS / "qa_questions.jsonl"))
        atk_res = run_attacks(s, load_jsonl(EVALS / "attacks.jsonl"))
    result = {"run_at": datetime.now(UTC).isoformat(timespec="seconds"), "llm_mode": settings.llm_mode,
              "qa": qa_res, "attacks": atk_res}
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def report(r: dict) -> None:
    q, a = r["qa"], r["attacks"]
    print(f"QA: {q['total']} questions, top-1 accuracy {q['accuracy']:.0%}, refusal rate {q['refusal_rate']:.0%}")
    print(f"Attacks: {a['blocked']}/{a['total']} blocked or neutralized")
    print(f"{'id':8}{'channel':12}{'expected':10}{'got':10}{'layer':12}ok")
    for d in a["details"]:
        print(f"{d['id']:8}{d['channel']:12}{d['expected']:10}{d['got']:10}{d['layer']:12}{'yes' if d['ok'] else 'MISS'}")
    for d in q["details"]:
        if not d["ok"]:
            print(f"QA MISS {d['id']}: expected {d['expected']} got {d['got']} (score {d['score']}) - {d['question']}")
    for d in a["details"]:
        if not d["ok"]:
            print(f"ATTACK MISS {d['id']} ({d['category']}): expected {d['expected']} got {d['got']}")
    print(f"written to {OUT}")


if __name__ == "__main__":
    report(run())
