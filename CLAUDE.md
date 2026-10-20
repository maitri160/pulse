# Pulse — news event intelligence feed

Portfolio project. **Hard deadline: deployed by 2026-11-14.** Keep scope lean.

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
  the alternative rejected, and the trade-off.
- New concept → 3–5 line explanation + one link to read later. No long tutorials.
- User must be able to explain every line in an interview. Prefer simple code; comment anything clever.
- Tests written alongside features, not at the end.
- Git commit after each milestone with clear messages. **Never push** until the user gives
  their GitHub username and says yes.
- Ask before installing anything or running sudo.
- End of each week: quiz the user with 5 interview-style questions on that week's design
  decisions, then grade their answers honestly.

## Machine
Apple Silicon Mac (arm64). Homebrew, git 2.50, Python 3.12.15 (`/opt/homebrew/bin/python3.12`;
the default `python3` is 3.14, so don't use it). Docker Desktop 29.9 works. Images
`pgvector/pgvector:pg16` and `redis:7-alpine` run natively on arm64.

## Plan
- **Week 1 (Oct 20–24):** skeleton, Docker Compose, DB schema (sources, articles, events) +
  Alembic, Celery Beat fetching feeds, exact dedupe (URL + content hash), MinHash near-dup,
  retries/backoff for failing feeds, basic tests.
- **Week 2 (Oct 25–31):** embeddings, pgvector, incremental clustering into events, events API.
- **Week 3 (Nov 1–7):** LLM event summaries (regenerate only on meaningful new articles),
  ranked feed (recency + #sources + velocity), HTMX UI, Redis caching.
- **Week 4 (Nov 8–14):** clustering eval on small hand-labelled set (precision/recall for
  README), Locust load test, Prometheus metrics, README with architecture diagram + "Design
  decisions", deploy, resume bullet.

## Progress
<!-- Update after every milestone: date, what was done, commit hash. -->
- 2026-10-20: Machine checks passed. Docker OK (the Rosetta error resolved itself; Rosetta
  is installed and running). Verified Postgres 16.15 + pgvector 0.8.7 and Redis 7 reachable
  via containers. CLAUDE.md created. Week 1 folder structure + schema approved; using **uv**.
- 2026-10-20 — **W1 M1 done:** uv project, FastAPI `/health` (checks DB + Redis, 503 if
  either is down), Dockerfile, Compose (db, redis, api). 2 tests, ruff clean.
  Next: M2 schema + Alembic.

## Commands
- `docker compose up -d --build` — start everything; `curl localhost:8000/health`
- `uv run pytest` — tests (need `docker compose up -d db redis` running)
- `uv run ruff check . && uv run ruff format .`
