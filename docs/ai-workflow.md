# AI-assisted workflow

Pulse is built with Claude Code as a pair programmer. The repo's `.claude/` folder holds
the rules and automation that keep that collaboration consistent. It is committed on
purpose, so it is part of the project and anyone cloning it gets the same setup.

| Piece | File | What it does |
|---|---|---|
| Project brief | `CLAUDE.md` | Goal, stack, working rules and commands, loaded at the start of every session |
| `/milestone` skill | `.claude/skills/milestone/SKILL.md` | Runs tests and lint, runs the reviewer, logs the milestone in `docs/progress.md`, adds design decisions, explains the work, makes one commit |
| `/migration` skill | `.claude/skills/migration/SKILL.md` | Checklist for hand-written Alembic migrations: check existing data, write the downgrade, round-trip on the test DB |
| `/quiz` skill | `.claude/skills/quiz/SKILL.md` | 5 interview-style questions on the current phase's design decisions, graded honestly |
| Push guard hook | `.claude/hooks/block-git-push.sh` | Blocks any `git push` Claude tries to run. Pushing is done by hand |
| Format hook | `.claude/hooks/ruff-format.sh` | Runs `ruff format` on every Python file Claude edits; reports syntax errors back |
| `reviewer` subagent | `.claude/agents/reviewer.md` | Read-only review of the diff for races, idempotency, transactions and locks, constraints, error paths and missing tests |
| Reviewer guard hook | `.claude/hooks/reviewer-readonly-bash.sh` | Limits the reviewer's shell to `git diff/status/log/show/blame` |

Hooks are registered in `.claude/settings.json`.

## Hook or CLAUDE.md rule?

A rule in `CLAUDE.md` is an instruction. The model reads it and almost always follows it,
but "almost always" is not a guarantee: a long session, a summarised context or an
ambiguous request can let it slip. A hook is code that Claude Code runs on every matching
tool call, outside the model, so it can't be forgotten or talked around.

The rule of thumb used here:

- **Hook** when breaking the rule is costly or hard to undo, or when the check is fully
  mechanical. Pushing publishes code to GitHub, so it is blocked by a hook, not just asked
  for. Formatting is mechanical, so a hook does it every time instead of relying on memory.
- **CLAUDE.md rule** when the behaviour needs judgement: how long an explanation should be,
  when to ask before installing something, what counts as a milestone.
- **Both** for the push rule: `CLAUDE.md` explains *why* (pushing is the user's call), and
  the hook makes sure it holds.

## Why the reviewer is a subagent

It runs in its own context with a narrow brief and read-only tools, so it reviews the diff
with fresh eyes, can't "fix" things on its own, and doesn't fill the main conversation with
file dumps. A subagent's `tools` list can only allow or deny Bash as a whole, so a hook
scoped to the reviewer narrows Bash to read-only git commands.
