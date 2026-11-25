# Pulse progress log

One entry per milestone: date, what was done, commit hash. Newest at the bottom.

- 2026-10-20: Machine checks passed. Docker OK (the Rosetta error resolved itself; Rosetta
  is installed and running). Verified Postgres 16.15 + pgvector 0.8.7 and Redis 7 reachable
  via containers. CLAUDE.md created. Week 1 folder structure + schema approved; using **uv**.
- 2026-10-20 — **W1 M1 done:** uv project, FastAPI `/health` (checks DB + Redis, 503 if
  either is down), Dockerfile, Compose (db, redis, api). 2 tests, ruff clean.
  Next: M2 schema + Alembic.
- 2026-10-29 — **Schedule compressed** (5 days behind). New dates: rest of W1 by Nov 1,
  W2 Nov 2–5, W3 Nov 6–9, eval/README/deploy Nov 10–12, buffer Nov 13–14. Locust is now
  a stretch goal; Prometheus = a `/metrics` endpoint via library only. Explanations stay
  at 3–5 lines unless the user asks for detail.
- 2026-10-29 — **W1 M2 done:** models (`sources` incl. `category`, `articles`, `events`),
  first Alembic migration (also enables `vector`), `pulse_test` DB created by conftest,
  migrations run at test start, per-test rollback. 6 tests. Next: M3 fetch one feed.
- 2026-10-29 — **W1 M3+M4 done (merged):** `app/ingest/` — normalize (canonical URL,
  content hash), fetcher (httpx + feedparser, conditional GET), extract (trafilatura),
  ingest (skip known URLs, hash-match → `duplicate_of_id`, `ON CONFLICT DO NOTHING`).
  Added `httpx` (feedparser has no timeout control). Tests use `tests/fakes.py`
  (httpx.MockTransport), no network. 24 tests. Real BBC World run: 27/27 with body;
  rerun skips all 27. Next: M5 Celery + Beat + backoff.
- 2026-11-21 — **Dedupe race fixed:** partial unique index
  `uq_articles_original_content_hash` (one original per hash, `WHERE duplicate_of_id IS
  NULL`), hand-written migration `fa13b34449bc`. Insert uses `ON CONFLICT DO NOTHING` with
  no target; a hash clash re-looks-up the original and retries once as a duplicate.
  `ingest_source` now commits per article and saves etag/last_modified only after the
  loop (it commits itself; callers don't). Verified with a real two-connection race
  (loser waits, then skips) and a mutation check. Design decisions table added to
  `docs/architecture.md`. 26 tests. Next: M5 Celery + Beat + backoff.
- 2026-11-21 — **W1 M5 done:** Celery app (`app/worker/`), Beat every 60 s runs
  `dispatch_due_feeds` → `claim_due_sources` (one UPDATE…RETURNING, 15-min lease) →
  `fetch_source(id)` per feed. `app/ingest/scheduling.py`: backoff 5 min × 2^failures,
  cap 6 h, ±10% jitter; `run_source` records success/failure (broad except → backoff).
  `acks_late`, 10-min `task_time_limit`. 23 verified feeds in `sources.toml` +
  `scripts/seed_sources.py` (upsert, stdlib tomllib). Compose: `migrate` (one-off),
  `worker`, `beat`. Live run: 817 articles from 23 feeds, 11 exact dupes, 79% with
  body; broken demo feed backed off to 10 min; 2nd round skipped known URLs. 41 tests.
  Known: the dispatcher shares the queue with fetches, so a tick can run late when all
  workers are busy (fine at this scale). Next: M6 MinHash near-dup.
- 2026-11-21 — **Claude Code tooling:** committed `.claude/` folder. Hooks in
  `.claude/settings.json`: `block-git-push.sh` (PreToolUse Bash, exit 2 on any `git push`)
  and `ruff-format.sh` (PostToolUse Edit|Write on `*.py`). Skills: `/milestone`,
  `/migration`, `/quiz`. Subagent `reviewer` (Read/Grep/Glob + Bash narrowed by its own hook
  to read-only git; `--output`/`--ext-diff` refused). Both hooks tested live (dry-run push
  blocked; messy file reformatted; syntax error reported back) plus 13 + 8 scripted cases.
  CLAUDE.md trimmed: progress moved here, deadline replaced, plan kept in order with no
  dates. `docs/ai-workflow.md` explains each piece and hook vs. rule. Known: push guard
  misses git aliases and `env git push` (the CLAUDE.md rule covers those), and blocks shell
  commands whose quoted text mentions pushing (write such text with the editor instead);
  the reviewer agent loads from the next session. Next: M6 MinHash near-dup.
  (commit: 96ad95c)
- 2026-11-22 — **W1 M6 done: MinHash near-dup.** `app/ingest/neardup.py`: 5-word shingles,
  128-number MinHash (datasketch 2.0, `scheme="affine32"`, fixed seed), only for bodies of
  50+ words. In-memory LSH of the last 3 days of originals, loaded on first need per fetch;
  LSH tuned to 0.5 to find candidates, each verified at 0.8 (tuned to 0.8, LSH finds a
  0.8 pair only about half the time). Exact hash is checked first, near match only if that
  misses; near-dups use the same `duplicate_of_id`, always pointing at an original. New
  stat `near_duplicates`. Dropped redundant index `ix_articles_content_hash` (EXPLAIN:
  the lookup uses the partial unique index), migration `96726efea6cc`, round trip OK.
  `scripts/backfill_minhash.py` filled 825 old articles. Survey of 812 real originals:
  true copies at 0.82–0.99 (AP story on ABC + CBS 0.88, DW republish 0.99), same event
  written separately 0.64 (left for clustering). 52 tests. Reviewer: no criticals. Known: two workers saving
  near-identical originals at the same moment can both stay originals (no DB rule can
  say "similar"; clustering groups them); BBC video pages extract as shared navigation
  text and match each other (no boilerplate filter yet). Next: phase 1 quiz, then phase 2.
  (commit: 87846c9)
- 2026-11-22 — **Phase 1 quiz:** 1 Strong, 3 Partial, 1 Missing. Pattern: knows outcomes,
  not mechanisms (ON CONFLICT is a quiet skip, not an error; an OOM kill runs no code, so
  the lease recovers the feed). Found and fixed an over-claiming `acks_late` comment
  (commit 62bb29b).
- 2026-11-22 — **P2 M7 done: embeddings.** `articles.embedding vector(384)` (migration
  `79d439d82c20`, hand-fixed: autogenerate forgot the pgvector import). No vector index
  yet: nothing searches articles by vector (M8 indexes event centres). `app/events/embed.py`:
  all-MiniLM-L6-v2, title + first 200 words, unit-length vectors; originals only
  (duplicates follow their original). `embed_pending` takes batches of 64 with
  `FOR NO KEY UPDATE SKIP LOCKED`, commits per batch. Celery task `embed_articles` on
  its own `embed` queue, served by a new `embedder` container with 1 process (model loads
  once: embedder 879 MB, fetch worker 435 MB); Beat every 60 s. PyTorch from the CPU-only
  index in Docker. Live: 1,091 originals embedded in one 97 s run (incl. model download),
  18 duplicates skipped. Lock check: NO KEY UPDATE lets ingest insert a duplicate pointing
  at a locked row; plain FOR UPDATE blocked it. Real pairs: same event rewritten 0.97
  (MinHash 0.64), unrelated 0.06, but different events on one topic 0.62, so M8's
  threshold must sit well above that. Reviewer (no criticals) fixes: a failing batch is
  retried one article at a time (else the oldest bad batch blocked everything forever);
  stale Beat ticks expire; whitespace body falls back to summary; a two-connection lock
  test (fails if `key_share` is removed). 62 tests. Known/deferred: no partial index for
  "pending" rows (fine below ~100k articles); torch uses all CPU cores; the model
  downloads on first run, not at build. Next: M8 clustering into events.
  (commit d088b75)
- 2026-11-23 — **M7 follow-up: embed one article per transaction.** Measured batching on
  CPU: 64 texts at once 0.52 s vs one at a time 0.58 s, only ~10%. Batching had made one
  bad article roll back 63 good ones and needed a fallback path (`_embed_one_by_one`).
  Now `embed_pending` locks, encodes and commits one article at a time (up to 1,280 per
  run); a failure rolls back only that article, which is skipped for the rest of the run
  and retried next run. ~30 fewer lines. Test checks the bad article is tried once per run
  (fails if the `failed` filter is removed). 62 tests.
- 2026-11-23 — Embed Beat tick `expires` raised from 60 s to 300 s (clock-drift margin
  between Beat and worker machines); test pins it. 63 tests.
- 2026-11-25 — **P2 M8 done: clustering into events.** Migration `af4ecdd684ce` (hand-fixed:
  pgvector import, unnamed FK): `events.centroid`, `events.last_article_at`,
  `articles.event_id`. `app/events/cluster.py`: single-pass clustering, threshold 0.75,
  3-day window either side, advisory lock, centroid and counts recomputed in SQL,
  duplicates follow their original. Task renamed `embed_and_cluster`. First live run: 1,229
  originals -> 946 events in ~9 s, same count as an in-memory simulation. Two days later,
  live: 2,719 articles in 1,828 events; all 23 feeds had recovered by backoff from an
  overnight DNS outage. Reviewer: no criticals. Fixed: future/old feed dates (seen in real
  data), one bad article blocking the rest, missing tests. Dry run with the fixes (rolled
  back): 1,845 events, biggest 106 -> 100 articles, now 21-25 Nov instead of 9-25 Nov. 79
  tests; each fix mutation-checked. Known/deferred: a long story stays one event (the
  100-article one); events made before the fix were not re-clustered; no cross-connection
  late-duplicate test; no empty-table guard in the migration. Also fixed an M7 test whose
  bad article was last, so it didn't prove newer ones get embedded. Next: M9 events API.
  (commit bcd6fb7)
- 2026-11-25 — **P2 M9 done: events API.** `app/api/events.py`: `GET /events` (most recently
  active first, keyset cursor, `limit`, `min_sources`) and `GET /events/{id}` (event + newest
  100 articles, one JOIN). Explicit response models, so no centroid or full text leaks. No
  migration: the `last_article_at` index serves the list (0.06 ms). Live: 1,835 events over
  19 pages, each exactly once; median 1.5 ms list, 2.6 ms for a 106-article event. Reviewer:
  no criticals. Fixed: tests never had time order differ from id order (sorting by id
  passed), and future-dated articles stayed on top of an event. 109 tests; 10 mutations
  checked. Known/deferred: an event that jumps to the top mid-paging can be missed;
  `min_sources` isn't indexed; links must be limited to http/https before the UI renders
  them (0 bad ones stored today). LLM engineering requirements added to CLAUDE.md. Phase 2
  code complete. Next: phase 2 quiz, then phase 3.
  (commit: pending)
