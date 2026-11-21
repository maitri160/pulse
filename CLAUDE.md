# Pulse — news event intelligence feed

Portfolio project. **Deadline: ship the core pipeline ASAP; job applications are live.**
Keep scope lean.

## What it does
Pulls articles from ~30–100 RSS feeds → removes exact and near-duplicates (same wire story
republished by many outlets) → clusters articles about the same real-world event into
"events" → one LLM summary per event → ranked feed in a simple web UI.

## About the user
Software engineer, ~2 yrs (security integrations: Splunk, ServiceNow, REST APIs, CI/CD,
a multi-agent AI project). Moving into AI/backend engineering; Pulse is for job applications.
Humour welcome.

## Stack (don't change without telling the user first and giving a strong reason)
- Python 3.12, FastAPI, Pydantic, SQLAlchemy 2.0 + Alembic
- PostgreSQL 16 + pgvector
- Redis + Celery + Celery Beat
- feedparser + trafilatura
- datasketch (MinHash near-dup detection)
- sentence-transformers `all-MiniLM-L6-v2` (CPU, 384-dim)
- Free-tier LLM API behind a small swappable provider interface
- Jinja2 + HTMX (no React)
- Docker Compose, pytest, ruff, GitHub Actions
- **Out of scope:** Kubernetes, Kafka, microservices, user accounts, training models

## Working rules
- Small milestones. After each: short plain-words explanation of what was built, *why*,
  the alternative rejected, and the trade-off. Use `/milestone` to finish one.
- New concept → 3–5 line explanation + one link to read later. No long tutorials.
- User must be able to explain every line in an interview. Prefer simple code; comment anything clever.
- Tests written alongside features, not at the end.
- Git commit after each milestone with clear messages. **Never push** until the user gives
  their GitHub username and says yes (a hook blocks `git push`).
- Ask before installing anything or running sudo.
- End of each phase: `/quiz` — 5 interview-style questions on that phase's design decisions,
  graded honestly.

## Machine
Apple Silicon Mac (arm64). Homebrew, git 2.50, Python 3.12.15 (`/opt/homebrew/bin/python3.12`;
the default `python3` is 3.14, so don't use it). Docker Desktop 29.9 works. Images
`pgvector/pgvector:pg16` and `redis:7-alpine` run natively on arm64.

## Plan (in order, no fixed dates)
1. **Ingest + dedupe:** skeleton, Compose, schema + Alembic, Celery Beat fetching, exact
   dedupe (URL + content hash), MinHash near-dup, backoff for failing feeds, tests.
2. **Events:** embeddings, pgvector, incremental clustering into events, events API.
3. **Feed:** LLM event summaries (regenerate only on meaningful new articles), ranked feed
   (recency + #sources + velocity), HTMX UI, Redis caching.
4. **Ship:** clustering eval on a small hand-labelled set (precision/recall for README),
   `/metrics` endpoint, README with architecture diagram + "Design decisions", deploy,
   resume bullet. Stretch: Locust load test.

## Progress
Milestone log: [docs/progress.md](docs/progress.md). Design decisions:
[docs/architecture.md](docs/architecture.md#design-decisions).

## Commands
- `docker compose up -d --build` — start everything; `curl localhost:8000/health`
- `uv run alembic upgrade head` — apply migrations to dev DB
- `uv run alembic revision --autogenerate -m "..."` — new migration after changing models
- `uv run pytest` — tests (need `docker compose up -d db redis`; uses separate `pulse_test` DB)
- `uv run ruff check . && uv run ruff format .`
- `uv run python -m scripts.fetch_once <feed_url> [name]` — fetch one real feed into dev DB
- `uv run python -m scripts.seed_sources` — load/update feeds from `sources.toml`
- `docker compose logs -f worker beat` — watch scheduled fetching;
  `docker compose stop worker beat` to pause it
