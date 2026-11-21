---
name: migration
description: Checklist for writing an Alembic migration by hand in Pulse - safe against existing data, reversible, and tested on pulse_test. Use when adding or changing tables, columns, constraints or indexes.
argument-hint: "[what the migration does]"
---

Write a hand-checked Alembic migration for: $ARGUMENTS

Work through this checklist and tell the user the result of each step.

1. **Change the model first** (`app/models.py`), so the model stays the single description
   of the schema.

2. **Create the file.** `uv run alembic revision -m "<what it does>"` (no `--autogenerate`
   for constraints or indexes; autogenerate is fine to draft plain new columns/tables, but
   read every line it writes).

3. **Write it by hand when autogenerate can't be trusted:**
   - Partial indexes (`postgresql_where=sa.text("...")`): autogenerate misses or mangles
     the `WHERE`.
   - Unique constraints and indexes: name them explicitly (`uq_<table>_<what>`), so the
     downgrade can drop them by name.
   - Extensions (`CREATE EXTENSION`), data backfills, renames: always by hand.

4. **Check existing data won't violate it.** Before adding a UNIQUE / NOT NULL / CHECK /
   foreign key, run a query on the dev DB that finds rows that would break it, e.g.
   `SELECT col, count(*) FROM t GROUP BY col HAVING count(*) > 1`. If any rows come back,
   stop and ask the user how to clean them up. Don't delete data on your own.

5. **Write `downgrade()` too.** It must undo exactly what `upgrade()` did, in reverse order.

6. **Round trip on the test DB** (never on dev data):
   `DATABASE_URL=postgresql+psycopg://pulse:pulse@localhost:5432/pulse_test` with
   `uv run alembic upgrade head`, then `uv run alembic downgrade -1`, then
   `uv run alembic upgrade head`. All three must succeed.

7. **Model and DB agree:** `uv run alembic check` on the dev DB after upgrading should say
   "No new upgrade operations detected."

8. **Tests:** add a test that proves the new rule works (e.g. the second violating insert
   raises `IntegrityError`, and allowed cases still pass). Run `uv run pytest`; conftest
   rebuilds `pulse_test` from all migrations, so this also tests the migration chain.
