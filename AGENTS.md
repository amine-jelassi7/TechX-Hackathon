# AGENTS.md

Instructions for any coding agent (Claude Code, OpenCode, Codex) working on this repo.
Keep token use low: read `PROGRESS.md` first, then only the spec sections your task needs.

## Where things are
- `docs/HANDOFF.md`: what was built, how to continue with OpenCode, feature ideas, how to win. Read once.
- `PROGRESS.md`: current state, step table, next actions, known issues. Start here.
- `IMPLEMENTATION_SPEC.md`: full spec (section 12 = build phases, section 0 = rules).
- `docs/API_CONTRACT.md`: LLM client, owner/public HTTP API, eval-results JSON. Code must match it.
- `docs/DECISIONS.md`: choices made where the spec was ambiguous. Add to it, don't silently diverge.

## Rules (from the spec, non-negotiable)
1. No real payments, card handling, email/SMS sending or payment-provider calls. Test tokens only.
2. No secrets in code or logs. `.env` only (`.env.example` is the template).
3. Synthetic data only (`scripts/seed.py`).
4. Everything must run offline with `LLM_MODE=mock`; tests always use mock mode and in-memory SQLite.
5. Every agent action, gateway decision and payment event goes through `app/audit.py`.
6. Never cut: gateway deterministic filters, permission matrix, audit log, approval queue.
7. No emojis in code, UI or docs.

## Workflow
- One step = one branch `step/<n>-<name>`, one PR, always targeting `main` (not stacked: stacked PRs
  merged out of order left commits stranded on step branches, see PR #7).
- Parallel work: each worker owns a disjoint set of files; shared shapes live in docs/API_CONTRACT.md.
- Before opening a PR: `uv run pytest -q` and `uv run ruff check .` must pass; put the commands and
  results in the PR body.
- After each step, update the step table and next actions in `PROGRESS.md` in the same PR.
- Tests: cover behavior that can break (security filters, fraud rules, KPI math, flows); skip trivia.
