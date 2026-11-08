# SoukSaaS

SoukSaaS is a prototype team of cooperating AI agents that runs a sales and payment journey end to end
for a product (demo: an AI training course), with security built in from the start.
A prospect chats, asks questions, and pays (simulated); the owner validates answers, approves sensitive
payments, and reads a KPI dashboard. Everything is synthetic and runs offline in mock mode.

Built for the TechX ENIT x ENICarthage 14h hackathon, "The Autonomous FinTech Team".

## Features (mapped to the 6 cahier agents)

| Cahier agent | Where | What it does |
|---|---|---|
| 1. Sales Agent | `app/agents/sales.py` | Q&A base first, LLM fallback limited to `product.json`; voice is simulated (text chat) |
| 2. Customer Intelligence + 5. Business Strategy | `app/agents/insights.py` | Needs, objections, recommendations; ranks consented leads and writes outreach drafts (manual sending only) |
| 3. Payment Agent | `app/agents/payment.py`, `app/services/payments_sandbox.py` | Sandbox payments with test tokens, fraud score, human approval gate |
| 4. Financial Data Analyst | `app/services/analytics.py` | Deterministic KPIs (plain code), optional LLM narrative, revenue forecast (linear trend, bonus) |
| 6. Security and Fraud Detection | `app/gateway/` | Layered gateway, permission matrix, explainable fraud rules, hash-chained audit log |

Also: Streamlit owner console (7 pages), product page and chat widget, attack evaluation set.

## Quickstart

```
uv sync
copy .env.example .env     # Windows; use cp on macOS/Linux
```

Keep `LLM_MODE=mock` for a fully offline run, or set `LLM_MODE=live` and `LLM_API_KEY` for a live model.
Never commit `.env`.

```
uv run python tasks.py seed        # synthetic data
uv run python tasks.py run         # API + product page
uv run python tasks.py dashboard   # owner console
uv run python tasks.py test        # unit and integration tests
uv run python tasks.py eval        # attack evals (writes data/eval_results.json)
uv run python tasks.py demo        # reseed + scripted demo (spec section 13) in-process, prints a transcript
```

- Product page: http://127.0.0.1:8000/
- API docs: http://127.0.0.1:8000/docs
- Dashboard: http://localhost:8501

## Demo scenario

1. The owner loads `product.json`; the product page renders; Q&A is generated and approved.
2. A prospect asks 3 questions: 2 answered from the Q&A, 1 through the fallback.
3. The prospect sends a prompt injection ("ignore previous instructions...") and it is blocked; an alert appears.
4. The prospect pays with `tok_success` (Standard plan) and the payment succeeds.
5. A second prospect makes repeated attempts and a large amount; the payment is held and the owner approves or rejects.
6. KPIs update; the Insights agent produces evidence-backed recommendations.
7. Insights mode B ranks consented leads and writes outreach drafts; the owner copies one to send manually.
8. The Security page shows audit-chain integrity OK and the eval results.

## Security summary

- Gateway layers, cheapest first: deterministic filters (length, normalization, injection patterns EN/FR/AR,
  Luhn card detection with redaction), LLM classifier (untrusted output, validated), permission matrix,
  output and tool-argument screening, prompt hygiene, human in the loop.
- Least privilege per agent, enforced by the orchestrator independent of the LLM.
- Deterministic, explainable fraud scoring; risky payments are held for owner approval.
- Hash-chained audit log with integrity check and dashboard badge.
- No real payment, card handling, or message sending exists anywhere in the code.

Threat model, limitations, and human-supervised steps: [docs/SECURITY.md](docs/SECURITY.md).

## Repository layout

```
app/          FastAPI app, agents, gateway, services, audit, permissions, LLM client
dashboard/    Streamlit owner console
scripts/      seed.py, run_evals.py
data/         product.json, synthetic seed, eval results
docs/         ARCHITECTURE.md (Mermaid), SECURITY.md, API_CONTRACT.md, DECISIONS.md, PITCH.md
tests/        unit and integration tests
tasks.py      cross-platform task runner
```

## Known limitations

- Prototype-grade owner auth (shared token header); no real user accounts.
- Payments are simulated; synthetic data only; no INPDP compliance review.
- The LLM classifier can be bypassed; deterministic layers are the real control.
- Voice is simulated; the forecast is a simple linear trend, not a production model.

## For contributors

Read [AGENTS.md](AGENTS.md) (rules and workflow) and [PROGRESS.md](PROGRESS.md) (state and next steps).
Design choices are in [docs/DECISIONS.md](docs/DECISIONS.md); the HTTP contract is in [docs/API_CONTRACT.md](docs/API_CONTRACT.md).
