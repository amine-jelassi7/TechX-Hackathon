# PROGRESS / HANDOFF

> **RESUME HERE (2026-11-08):** read section 0 below first.

Single source of truth for where the project stands. Updated after every step.
Anyone continuing (Claude Code, OpenCode, a human): read `AGENTS.md`, then this file, then only the
spec sections you need. Last updated: 2026-11-08 (step W done).

## 0. Resume here: review fixes in progress (branch `wip/review-fixes`)

A final code review (Opus) found 12 defects. A worker started fixing them and stopped mid-way (token limit).
Its partial work is on branch `wip/review-fixes` (draft PR). State when it stopped: `uv run pytest -q` -> 390 passed.
Open PRs to merge first if still open: #17 (pitch guide), #18 (dashboard motion + light/dark).

Status per finding (verify each by reading the code + running its test; "likely done" = code present, not re-verified):

| # | Sev | Finding | Where | Status |
|---|---|---|---|---|
| 1 | HIGH | Retried payment charges 2-3 times when the payment agent is slower than the orchestrator timeout; timed-out threads share the request DB session | `app/orchestrator.py`, `app/agents/payment.py` | **NOT DONE / partial** (worker stopped at "base agent and orchestrator"). Fix: never retry non-idempotent message types (payment_request, insights runs); idempotency key (AgentMessage id / trace_id stored on Payment, checked before insert); own Session per attempt. Add regression test: slow payment agent -> exactly 1 Payment row |
| 2 | HIGH | Card number stored raw in `Payment.test_token` | `app/schemas.py`, `payment.py` | likely done (`test_token` pattern `^tok_[a-z0-9_]{1,32}$`) |
| 3 | MED | Card number stored raw in `Interaction.session_id` | `app/schemas.py` | likely done (`session_id` pattern) |
| 4 | MED | User can close the `<untrusted>` delimiter in chat | `sales.py`, `gateway/filters.py` | likely done (tag stripped + injection pattern) |
| 5 | MED | Conversion = paid/interested counted payers who never showed interest (demo showed 88%) | `services/analytics.py`, `scripts/seed.py` | analytics likely done (`converted = paid & interested`); CHECK seed: payers mostly from interested prospects, ApprovalRequest rows for seeded held payments, no seeded `approved` status |
| 6 | MED | Live mode: LLM 20 s x 2 attempts vs 10 s orchestrator timeout -> timeouts + duplicate runs | `app/llm.py`, `app/config.py` | **NOT VERIFIED**. Fix: agent timeout ~45 s, LLM timeout ~15 s, 1 retry |
| 7 | MED | Owner API accepts default token `change-me` | `app/config.py`, `routers/owner.py` | **partial** (config only). Fix: if token empty/`change-me` and APP_ENV != dev -> owner API 401 + startup warning; dev keeps working with a warning |
| 8 | LOW | Approve/reject race: two concurrent approves both charge | `payment.py` approve/reject | **NOT DONE**. Fix: atomic `UPDATE ... WHERE id=? AND status='pending'`, check rowcount, 409 otherwise |
| 9 | LOW | Classifier `extra="forbid"` breaks on extra keys in live mode; missing API key only reported after retries | `gateway/classifier.py`, `app/llm.py` | classifier likely done; CHECK fail-fast on missing key (no retries on auth errors, never log key) |
| 10 | LOW | `LLMError` from `qa.generate_qa` -> 500 | `routers/owner.py` | likely done (503) |
| 11 | LOW | LLM-written outreach `reason` not passed through `gateway.check_output` | `services/insights.py` | CHECK |
| 12 | LOW | No audit events for qa reject/update and payment-hold failure path | `routers/owner.py`, `payment.py` | qa events likely done; CHECK payment-hold failure path |

After fixing: run `uv run pytest -q`, `uv run ruff check .`, `uv run python tasks.py demo` (must end "audit chain ok", believable conversion),
`uv run python tasks.py eval` (attacks must stay 15/15, refusal 100%). Then update this table, `docs/DECISIONS.md`
(no retry for non-idempotent handlers, token policy) and open the PR against main.

Last step after that: run the platform and check it: `uv run python tasks.py seed`, then `uv run python tasks.py run`
(product page http://127.0.0.1:8000) and `uv run python tasks.py dashboard` (http://localhost:8501), click the
Simulator page "Run demo scenario", check Overview, Live feed, Approvals, Security in both themes.

## 1. What we are building
SoukSaaS: a prototype "Autonomous FinTech Team" for the TechX ENIT x ENICarthage 14h hackathon
(brief: `Cahier_des_Charges_The_Autonomous_FinTech_Team_TechX.pdf`, local only, not in the repo).
Cooperating AI agents sell an AI training course end to end: product page + chat (Sales Agent),
simulated payment with human approval (Payment Agent), KPIs (Analytics), evidence-backed
recommendations and outreach drafts (Insights), all behind a security gateway with a hash-chained
audit log. Full technical spec: `IMPLEMENTATION_SPEC.md`. Internal contracts: `docs/API_CONTRACT.md`.

## 2. How to run (Windows, macOS, Linux)
```
uv sync                         # install
cp .env.example .env            # then set LLM_API_KEY (OpenAI) and OWNER_TOKEN; LLM_MODE=mock needs no key
uv run python tasks.py seed     # synthetic data into data/app.db
uv run python tasks.py test     # pytest
uv run python tasks.py run      # FastAPI on :8000 (available after step S4)
uv run python tasks.py dashboard  # Streamlit owner console
```
`make <target>` works too where make exists (it calls tasks.py).

## 3. Step status (each step = one stacked PR, merge in order)
| Step | Content | Status | PR |
|---|---|---|---|
| S0 | Scaffolding, models, schemas, seed, tasks.py | done | #1 |
| SD | ARCHITECTURE.md, SECURITY.md, attacks.jsonl, Jinja templates | done | #2 |
| S3 | Streamlit owner console (7 pages) vs owner API contract | done | #3 |
| SH | AGENTS.md + this file | done | #4, #7 |
| S2 | `app/llm.py`, `services/qa.py`, `payments_sandbox.py`, `analytics.py`; seed Q&A ids qa_price, qa_features, qa_schedule, qa_refund, qa_prerequisites, qa_certificate, qa_audience, qa_plans, qa_contact | done | #6, #7 |
| S1 | `app/audit.py`, `permissions.py`, `gateway/` (filters, classifier, fraud) | done | #8 |
| S7a | README, `docs/PITCH.md` (FR), `analytics.forecast_revenue` | done | #9 |
| S6a | `scripts/run_evals.py`, `tests/evals/qa_questions.jsonl`, eval results in SECURITY.md | done | #10 |
| S5a | `app/services/insights.py`, `outreach.py` (logic only) | done | #11 |
| S3b | Futuristic dashboard (three.js agent network, HUD theme, `tests/fixtures/mock_owner_api.py`) | done | see PR list |
| S4 | Orchestrator, agents (base, sales, payment), public + owner routers, integration tests | done | see PR list |
| S2b | Q&A matcher fix (ambiguity guard, word+char scoring, richer variants) | done | see PR list |
| W | Wiring: `app/agents/insights.py`, owner endpoints insights/run, recommendations, drafts, mark_sent (drafts deduped per lead), `/owner/simulate` (demo + attack, `app/services/simulator.py`), `python tasks.py demo`, dashboard actor mapping vs real backend actors | done | see PR list |
| F | Final: end-to-end browser check (product page chat + pay, dashboard on real backend), review pass, PITCH numbers | next | - |

## 4. Next actions (in order)
1. F (final checks): browser check of product page + dashboard on the real backend (`uv run python tasks.py demo`, then `run` and `dashboard`), review pass, PITCH numbers.
2. Optional: held-out Q&A question set (current 100% is optimistic, see docs/SECURITY.md).

## Dev tips
- Dashboard without backend: `uv run uvicorn tests.fixtures.mock_owner_api:app --port 8001`, then
  `API_URL=http://127.0.0.1:8001 uv run streamlit run dashboard/app.py`.
- Delete `data/app.db` and reseed after model changes.

## 5. Cahier (brief) coverage and gaps
Covered by the spec: 6 agent roles (Customer Intelligence + Business Strategy merged into the Insights
agent with 2 modes; Financial Analyst = deterministic analytics service; Security = gateway), all
section 4 dashboard KPIs, section 5 data rules (synthetic only, no card data, simulated payments,
documented limits), most section 6 bonuses (prompt-injection demo, fraud rules, human-in-the-loop,
live event feed, browser voice simulation).
Gaps added to the plan:
- Section 7 deliverable "Presentation du projet" (problem, solution, technical choices, value,
  security measures, future work): not in the spec -> S7 `docs/PITCH.md` outline.
- Section 6 bonus "ML forecast of sales/revenue": not in the spec -> S7, small linear trend on
  `analytics.revenue_over_time`, shown on the dashboard.
- Section 8 is missing from the brief itself (numbering jumps 7 -> 9). Ask the organizers.
Judging (sections 9-10): communication, originality, feasible architecture, AI/FinTech relevance,
security and trust, prototype quality, impact. The 3D agent-network dashboard targets originality.

## 6. Decisions
See `docs/DECISIONS.md`. Plus: PRs are stacked (`step/<n>-<name>`, each based on the previous step
branch) and never merged by the agent; the repo owner merges in order.

## 7. Known issues
- Q&A TF-IDF threshold 0.37 (`app/services/qa.py`), narrow margin: worst off-topic 0.34 vs weakest paraphrase 0.39. Embeddings path untested (optional extra).
- Dashboard `eval_results.json` shape fixed in API_CONTRACT.md section 5; S6 must write exactly that.
- Browser JS of the chat widget not yet exercised (done in S7 end-to-end check).
- Fixed in W: `new_id()` plain uuid hex contained 13+ digit runs ~0.2% of the time, which the gateway Luhn filter read as a card number and rejected the payment (flaky tests, random demo rejections). Ids now use `_` every 8 hex chars.
- Demo/simulate burst step: the 3rd attempt after two declines is held by the `declined_then_new_attempt` rule (expected), so each run adds 2 pending approvals.
