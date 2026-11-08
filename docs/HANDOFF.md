# HANDOFF: continue SoukSaaS with OpenCode

For the team taking over. Read this once, then work from `PROGRESS.md` (live status) and `AGENTS.md`
(rules OpenCode loads automatically). State as of 2026-11-08: everything below is on `main`.

## 1. Start in 5 minutes

```
git clone https://github.com/Faouzi-Blibech/SoukSaaS.git && cd SoukSaaS
uv sync
cp .env.example .env          # set OWNER_TOKEN to a long random string
                              # optional: LLM_MODE=live + LLM_API_KEY=sk-... (OpenAI, gpt-4o-mini)
uv run python tasks.py test   # expect ~390 passed
uv run python tasks.py demo   # full scenario in the terminal, ends with "audit chain ok"
uv run python tasks.py run        # terminal 1: API + product page  http://127.0.0.1:8000
uv run python tasks.py dashboard  # terminal 2: owner console        http://localhost:8501
```
In the dashboard, open **Simulator** and click "Run demo scenario" to fill every page with activity.
No `make` on Windows: `tasks.py` does everything. Keep `LLM_MODE=mock` when there is no network.

## 2. Working with OpenCode on a small token budget

1. OpenCode reads `AGENTS.md` by itself. Do not paste the spec into the chat.
2. Start every session with: "Read PROGRESS.md section 0 and the files for <task>. Do only <task>."
3. Point it at exact files (see the map in section 4). Never ask it to "look around the repo".
4. One task per session, one branch per task, one PR per branch, always targeting `main`.
5. Before each PR: `uv run pytest -q` and `uv run ruff check .`. Paste only the last line of output.
6. Shapes shared between backend and dashboard live in `docs/API_CONTRACT.md`: change it first, then code.
7. After each task, update the step table and next actions in `PROGRESS.md` in the same PR.
8. Use a cheap model for docs and small edits; a stronger one only for security or payment logic.

## 3. What was built (22 PRs)

| Area | What exists | Main files |
|---|---|---|
| Scaffolding | uv project, SQLModel tables, Pydantic schemas, deterministic synthetic seed, `tasks.py` | `app/models.py`, `app/schemas.py`, `scripts/seed.py` |
| LLM client | One interface, offline mock mode + OpenAI live mode via LiteLLM, JSON validation, fail-fast without key | `app/llm.py` |
| Sales Agent | Q&A first (no LLM on known questions), product-only LLM fallback, human handoff, interest + objection detection | `app/agents/sales.py`, `app/services/qa.py` |
| Q&A base | Generated from `product.json`, owner approves, TF-IDF char+word matcher with ambiguity guard, EN/FR | `app/services/qa.py` |
| Payment Agent | Test tokens only, amount always from `product.json`, fraud score, hold for approval, idempotent (no double charge) | `app/agents/payment.py`, `app/services/payments_sandbox.py` |
| Insights Agent | Needs/objections/recommendations with mandatory evidence; consented-lead outreach drafts with opt-out, never sent | `app/agents/insights.py`, `app/services/insights.py` |
| Analytics | Every cahier section 4 KPI, deterministic (no LLM), revenue forecast (linear trend) | `app/services/analytics.py` |
| Orchestrator | Typed messages, permission check, timeout, retries only for idempotent handlers, own DB session per attempt | `app/orchestrator.py` |
| Security | Layered gateway (filters EN/FR/AR, base64, zero-width, Luhn; LLM classifier only when unsure), least-privilege matrix, fraud rules, hash-chained audit log | `app/gateway/`, `app/permissions.py`, `app/audit.py` |
| API | Public: product page, chat, pay, rating. Owner (token): KPIs, audit, approvals, Q&A, insights, drafts, simulate, evals | `app/routers/` |
| Product page | Jinja page + chat widget, test pay buttons, no card field anywhere, mic via Web Speech API | `app/templates/` |
| Dashboard | 7 pages, three.js agent network, HUD theme, light/dark toggle, motion, live feed every 5 s | `dashboard/` (`DESIGN.md` explains the design) |
| Evals | 23 Q&A questions + 15 attacks, honest results written to `docs/SECURITY.md` | `scripts/run_evals.py`, `tests/evals/` |
| Docs | Architecture diagrams, security model and limits, decisions, pitch guide | `docs/` |

Verified numbers (mock mode): tests 390 passed; attacks 15/15 blocked; Q&A refusal 100%, top-1 100%
(optimistic, see SECURITY.md); demo conversion 58.8%, audit chain ok. A final Opus code review found 12
defects (double charge, card data in ids, delimiter breakout, wrong conversion KPI...); all fixed with tests.

## 4. Known gaps (good first tasks)

1. Paid KPI card says "94% of interested": it counts all payers; conversion counts payers who were also
   interested. Show `converted_prospects` on the card. Files: `dashboard/page_overview.py`.
2. Security Gateway node in the 3D network does not turn red when events are flagged.
   Files: `dashboard/three_scene.py` (`canonical_actor`), `dashboard/static/agent_network.js`.
3. Owner approval of a held payment does not notify insights (only direct success does). `app/agents/payment.py`.
4. Q&A accuracy is optimistic: write 15 NEW questions in `tests/evals/qa_questions_holdout.jsonl` and report.
5. Live mode was only smoke-tested lightly: run `tasks.py demo` with `LLM_MODE=live` once and fix what breaks.
6. Product page is a plain light theme; align it with the dashboard look. `app/templates/`.

## 5. Features you could add (ranked by points per hour)

Cahier section 6 bonuses and section 10 judging criteria in brackets.

| # | Feature | Effort | Why it scores |
|---|---|---|---|
| 1 | **Live attack button on the product page demo**: a "try to hack the agent" panel that sends a preset injection and shows the block + the alert appearing in the dashboard feed | 1-2 h | Prompt-injection demo [bonus], security [crit 5], memorable demo moment |
| 2 | **Real-time voice**: browser speech-to-text already fills the input; add text-to-speech replies (Web Speech `speechSynthesis`) and auto-send | 1-2 h | "Voice Sales Agent" is agent 1 of the cahier; voice [bonus] |
| 3 | **Sentiment + intent per turn**: small rules or an LLM call storing sentiment, shown as a trend chart | 2 h | Advanced sentiment [bonus], customer intelligence [crit 6] |
| 4 | **Stripe test mode adapter** behind the same sandbox interface (test keys only, still no card field: use Stripe Checkout test) | 3 h | Sandbox payment API [bonus], FinTech realism [crit 4] |
| 5 | **IsolationForest anomaly score** on seeded payments, shown next to the rule-based fraud score (never the only control) | 2 h | ML fraud detection [bonus] |
| 6 | **Better forecast**: weekly seasonality or Prophet-style model with an uncertainty band on the Overview | 1-2 h | ML revenue forecast [bonus] |
| 7 | **Agent conversation replay**: pick a trace_id, see every agent message and gateway decision as a timeline | 2-3 h | Transparency/traceability [cahier 1], shows collaboration [crit 3] |
| 8 | **Arabic / Tunisian darja support** in the Q&A base and filters | 2 h | Local impact [crit 6], originality |
| 9 | **WhatsApp-style channel mock** (same agents, second UI) | 3 h | Shows the agents are channel-independent |
| 10 | **Multi-product**: load any `product.json` from the dashboard | 2 h | Impact / real-world use [crit 6] |

## 6. How to win

What the jury scores (cahier section 10): presentation, team cohesion, prototype quality, originality,
security and reliability, impact. Plan for each:

1. **Lead with the demo, not slides.** 3 minutes, scripted in `docs/PITCH.md` section 5: chat (Q&A answer),
   injection blocked live, card number refused, test payment, a big payment held, owner approves in the
   dashboard, insights recommendations with evidence, audit chain OK. Rehearse it 5 times.
2. **Make security visible.** Most teams will claim security; you can show it: live feed turning red,
   attacks 15/15 in Security & Evals, tamper-evident audit chain, least-privilege matrix. Say the limits
   out loud (classifier can be bypassed, synthetic data only): honesty reads as maturity.
3. **Show agents collaborating, not a chatbot.** Point at the 3D network and the live feed
   `agent_message` events: Sales asks Payment, Payment notifies Insights, Security sits in front of all.
4. **Numbers beat adjectives.** Quote the real eval and demo numbers (`tasks.py eval`, `tasks.py demo`).
5. **Impact story.** Tunisian training providers (the 9antra example in cahier section 11) lose
   prospects between chat, payment and follow-up; SoukSaaS closes the loop with a human in control.
6. **Team cohesion.** Each member presents the part they own (agents, security, data, dashboard).
7. **Have a fallback.** If Wi-Fi or OpenAI fails: `LLM_MODE=mock` gives the same demo offline.
   Record a 2-minute screen video of the demo the night before as a last resort.
8. **Ask the organizers** what the missing cahier section 8 contains before the event.

Full slide-by-slide guide, screenshot checklist and likely jury questions: `docs/PITCH.md`.
