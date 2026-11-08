import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_runner_output_matches_contract(tmp_path):
    spec = importlib.util.spec_from_file_location("run_evals", ROOT / "scripts" / "run_evals.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "eval_results.json"
    mod.run(out)
    r = json.loads(out.read_text("utf-8"))
    assert {"run_at", "llm_mode", "qa", "attacks"} <= r.keys()
    assert r["llm_mode"] == "mock"
    assert {"total", "accuracy", "refusal_rate", "details"} == r["qa"].keys()
    assert {"total", "blocked", "details"} == r["attacks"].keys()
    n_qa = len(mod.load_jsonl(ROOT / "tests/evals/qa_questions.jsonl"))
    n_atk = len(mod.load_jsonl(ROOT / "tests/evals/attacks.jsonl"))
    assert r["qa"]["total"] == n_qa == len(r["qa"]["details"])
    assert r["attacks"]["total"] == n_atk == len(r["attacks"]["details"])
    assert {"id", "channel", "expected", "got", "layer", "ok"} <= r["attacks"]["details"][0].keys()
    assert {"id", "question", "expected", "got", "score", "ok"} <= r["qa"]["details"][0].keys()
    assert n_qa >= 16 and sum(d["expected"] == "none" for d in r["qa"]["details"]) >= 5
