---
name: milestone
description: Finish a Pulse milestone - run tests and lint, review the diff, log progress and design decisions, explain the work, and make one commit. Use when a milestone's code is done.
disable-model-invocation: true
argument-hint: "[short milestone name]"
---

Finish the milestone: $ARGUMENTS

Do these steps in order. If any check fails, stop and report the failure with its output.
Do not try to "fix forward" silently.

1. **Checks**
   - `uv run pytest`
   - `uv run ruff check . && uv run ruff format .`
   If either fails: stop, show the failing output, and ask the user how to proceed.

2. **Review.** Run the `reviewer` subagent on the current diff. Show its findings. If it
   reports anything critical (races, broken idempotency, data loss), stop and ask the user
   before committing.

3. **Progress log.** Append one entry to the END of `docs/progress.md`, in the same style as
   the existing entries: `- YYYY-MM-DD — **<milestone>:** what was built, key numbers
   (tests, live-run results), anything known to be weak. Next: <next milestone>.`
   End the new entry with `(commit: pending)`: a commit can't contain its own hash, because
   the hash is computed from the content (amending to add it would change it again).
   Before adding the new entry, replace any `(commit: pending)` left by the PREVIOUS
   milestone with its real short hash (`git log -1 --format=%h -- docs/progress.md` gives
   the last commit that touched the log, which is that milestone).

4. **Design decisions.** If the milestone made a real design choice, add a row to the table
   under `## Design decisions` in `docs/architecture.md`:
   `| Decision | Alternative rejected | Why |`. Skip this if nothing new was decided.

5. **Explain** to the user in plain words, briefly:
   - What was built (files, one line each)
   - Why it was built that way
   - The alternative that was rejected
   - The trade-off
   - Any new concept: 3–5 lines plus one link

6. **Commit.** Stage everything and make ONE commit with a clear summary line and a short
   body. Tell the user the short hash.
   - No `Co-Authored-By` or other attribution lines.
   - **Never push.** Pushing is manual (a hook blocks `git push` anyway).
