"""Cross-platform task runner: python tasks.py setup|seed|run|dashboard|test|eval|demo"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

TASKS = {
    "setup": [["uv", "sync"]],
    "seed": [[sys.executable, "scripts/seed.py"]],
    "run": [[sys.executable, "-m", "uvicorn", "app.main:app", "--reload"]],
    "dashboard": [[sys.executable, "-m", "streamlit", "run", "dashboard/app.py"]],
    "test": [[sys.executable, "-m", "pytest", "-q"]],
    "eval": [[sys.executable, "scripts/run_evals.py"]],
}
SPECIAL = {"demo"}


def _line(value, width: int = 110) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= width else text[: width - 3] + "..."


def demo() -> int:
    """Reseed, then run spec section 13 in-process (offline, mock LLM). Starts no server."""
    os.environ["LLM_MODE"] = "mock"  # the demo must run offline and without keys
    code = subprocess.call([sys.executable, "scripts/seed.py"])
    if code:
        return code
    sys.path.insert(0, str(ROOT))
    from sqlmodel import Session

    from app import audit, db
    from app.services import analytics, qa, simulator

    qa.invalidate()
    print("\n=== SoukSaaS demo: spec section 13, steps 2-8 (mock LLM, in-process) ===\n")
    with Session(db.engine) as session:
        for item in simulator.run_demo(session)["steps"]:
            print(f"[{item['step']}]")
            for key, value in item["result"].items():
                print(f"    {key}: {_line(value)}")
        kpis = analytics.get_kpis(session)
        chain = audit.verify_chain(session)
    print("\n=== Final KPIs ===")
    print(f"    interactions: {kpis.interactions_count}   interested: {kpis.interested_prospects}   "
          f"paid: {kpis.paid_prospects}   conversion: {kpis.conversion_rate:.1%}")
    print(f"    simulated revenue: {kpis.simulated_revenue:g}   payments: {kpis.payments_by_status}")
    print(f"    security alerts: {kpis.security_alerts_count} {kpis.security_alerts_by_type}")
    print(f"    audit chain: {'ok' if chain['ok'] else 'BROKEN'} ({chain['checked']} events checked)")
    print("\nStart the app to look at it (two terminals, nothing was started for you):")
    print("    uv run python tasks.py run         # API on http://127.0.0.1:8000 (product page at /)")
    print("    uv run python tasks.py dashboard   # owner console, uses OWNER_TOKEN from .env")
    return 0 if chain["ok"] else 1


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in TASKS | dict.fromkeys(SPECIAL):
        print(f"usage: python tasks.py {'|'.join([*TASKS, *sorted(SPECIAL)])}")
        return 2
    name = sys.argv[1]
    if name == "demo":
        return demo()
    for cmd in TASKS[name]:
        code = subprocess.call(cmd)
        if code:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
