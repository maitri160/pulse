---
name: quiz
description: Quiz the user with 5 interview-style questions on the current phase's Pulse design decisions, one at a time, and grade each answer honestly.
disable-model-invocation: true
argument-hint: "[phase or topic, optional]"
---

Run an interview-practice quiz on: $ARGUMENTS (if empty, use the current phase in
CLAUDE.md's Plan and the latest entries in `docs/progress.md`).

1. Read `docs/progress.md` and the `## Design decisions` table in `docs/architecture.md`.
   Pick 5 decisions from this phase that an interviewer would probe.

2. Ask **one question at a time**, phrased the way an interviewer would ("Why did you...",
   "What happens if two workers...", "What would you change at 100x scale?"). Mix:
   at least one "why this design", one "what breaks if...", and one "what's the trade-off".
   Then **stop and wait** for the user's answer. Don't hint at the answer in the question.

3. Grade each answer before asking the next question:
   - **Score:** Strong / Partial / Missing
   - **What was right** (one line)
   - **The gap**: exactly what was missing or wrong, and why it matters
   - **A model answer** in 2–4 sentences, as the user could say it in an interview
   Be honest: a vague answer is Partial, not Strong. Don't soften it.

4. After question 5, give a short summary: the scores, the one topic to revise, and the
   file to reread for it.
