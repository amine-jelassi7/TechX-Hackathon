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
