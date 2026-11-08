# Decisions

- **Repo root is the project root.** The spec's `autonomous-fintech-team/` folder is not created.
- **TF-IDF is the default Q&A matcher; embeddings are an optional extra.** `sentence-transformers`
  pulls PyTorch (large install, slow first download) and would break the "fresh clone to demo in
  10 minutes" goal. Install with `uv sync --extra embeddings` and set `EMBEDDING_MODE=local`.
  `.env.example` therefore defaults `EMBEDDING_MODE=tfidf`.
- **Audit writes are serialized with a process-wide lock.** Each event hashes the previous one
  (hash chain), so concurrent writers would fork the chain. A single lock around read-last-hash +
  insert keeps it linear. Limit: single process only (fine for the prototype).
- **Streamlit talks to the FastAPI owner API, not SQLite directly.** Permissions, redaction and
  audit logging live in one place (the API); the dashboard is a thin client using `X-Owner-Token`.
- **`tasks.py` instead of relying on `make`.** `make` is not available on Windows. `tasks.py` is
  the real runner; the `Makefile` is a thin wrapper calling `uv run python tasks.py <target>`.
- **LLM: OpenAI `gpt-4o-mini` via LiteLLM by default.** Key in `LLM_API_KEY` (`OPENAI_API_KEY`
  also accepted). `LLM_MODE=mock` keeps everything offline and deterministic.
- **Seed ids are deterministic strings** (`p000`, `pay003`, ...) so tests and demos are stable.
- **No orchestrator retries for non-idempotent handlers.** `BaseAgent.idempotent_types` lists the message
  types that may run twice; the default is empty, so Sales, Payment and Insights (which all write rows) run
  exactly once. A timed-out worker thread cannot be killed, so a retry would run in parallel with it (this
  produced 3 charges for one `/api/pay`). The Payment Agent is additionally idempotent: one Payment per
  (trace_id, prospect, plan), checked before insert, so a repeated message returns the stored outcome.
  Each attempt runs on its own DB Session (`Orchestrator(isolate_sessions=True)`, used by `build_orchestrator`).
- **Timeout budget.** LLM call 15 s x 2 attempts = 30 s worst case; agent handler timeout 45 s. Auth errors and
  a missing key in live mode fail at once with `LLMError` (no retry); startup logs an error for the latter.
- **Token policy.** `test_token` must match `^tok_[a-z0-9_]{1,32}$` (422 otherwise) and the Payment Agent
  re-checks it before writing any row; `session_id` must match `^[A-Za-z0-9_-]{1,64}$` and must not be a
  Luhn-valid card number. Nothing that could be a card number is stored.
- **Owner token.** Empty or `change-me` with `APP_ENV != dev`: every owner call returns 401 and startup logs an
  error; in dev it works and logs a warning. `.env.example` keeps `APP_ENV=dev` for the demo.
- **Product discovery is a deterministic weighted quiz, not an LLM call.** `data/products.json` lists the
  catalog; each product carries a `match` profile for six signals (goal, level, budget, format, audience,
  pace). `app/services/recommender.py` sums fixed weights (0.28/0.16/0.14/0.18/0.12/0.12) and returns 0..100
  fit scores with human-readable reasons, so the public `/discover` page is offline, testable and never
  invents a match. The flagship `data/product.json` remains the single product the Sales and Payment agents
  sell; the catalog is used only for discovery.
- **Conversion KPI** is `converted_prospects / interested_prospects` where converted = paid AND interested;
  `paid_prospects` still counts every payer. An owner-approved payment ends `succeeded` (the `approved` status
  belongs to the ApprovalRequest, never to a Payment).
- **SaaS owner flow plan (2026-11-08).** The existing discovery quiz is customer-facing and is replaced
  by a redirect to published pages (or a minimal published-product list). The requested flow needs a
  persisted multi-product catalog, owner-token APIs, dashboard wizard, deterministic public page,
  feedback scoring, and evidence-backed suggestions. Implementation order: data/contracts and parser;
  owner CRUD/publish and public routing; feedback and aggregates; suggestions/inbox/page changes;
  dashboard and docs. Product rows retain structured Product fields as JSON to preserve the existing
  Sales/Payment contract. Existing one-product seed remains the default published product. PDF intake
  uses text extraction only, never stores the upload, and leaves every extracted value for owner review.
  Delivery is dashboard inbox plus a local Markdown outbox; no email is sent. Scanned PDFs/OCR are out
  of scope. `app/db.py` adds `product_id` to existing SQLite interaction, payment, Q&A and approval tables
  before `create_all` creates the new product-flow tables.
  PDF support uses pure-Python `pypdf` for text extraction and `python-multipart` for FastAPI uploads;
  neither executes PDF content or adds OCR. The dashboard stores separate draft and published product
  snapshots so owner edits and approved suggestions remain private until the owner publishes again.
- **Buyer research is owner-triggered and evidence-first.** Search queries are built from the selected
  product profile. Hunter discovers candidate companies and enriches business-domain decision-maker
  emails first; Tavily searches one-year news results for funding, acquisition and hiring signals. A
  signal is included only when a dated result names the candidate and its public page is crawlable under
  that page's `robots.txt`. If enrichment finds no contact, only the company's `/contact`, `/about` and
  `/team` pages are checked. Personal webmail domains and LinkedIn are excluded; phone numbers are taken
  only from `/contact` pages and labeled as the company main line. Results without recent signal evidence
  are omitted. No messages are sent. Search and enrichment keys are supplied through `.env`, and the
  dashboard warns that providers may consume credits before a run.
