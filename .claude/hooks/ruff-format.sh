#!/bin/bash
# PostToolUse hook (Edit|Write): run `ruff format` on any Python file Claude just changed.
#
# Claude Code sends JSON on stdin; for Edit and Write, tool_input.file_path is always
# an absolute path. The edit has already happened, so this hook can't block anything:
# it only tidies the file. Exit 0 = fine. Exit 2 = show our stderr to Claude
# (used when ruff can't format, which usually means a syntax error).

file=$(jq -r '.tool_input.file_path // empty')

# Only Python files, and only inside this repo (not scratch files elsewhere on disk).
# If CLAUDE_PROJECT_DIR were empty, the "inside this repo" check below would match every
# absolute path, so bail out instead.
[[ -n "$CLAUDE_PROJECT_DIR" ]] || exit 0
[[ "$file" == *.py ]] || exit 0
[[ "$file" == "$CLAUDE_PROJECT_DIR"/* ]] || exit 0
[[ -f "$file" ]] || exit 0

# --project makes uv use this repo's ruff version and its config in pyproject.toml.
if ! output=$(uv run --project "$CLAUDE_PROJECT_DIR" ruff format "$file" 2>&1); then
  echo "ruff format failed on $file:" >&2
  echo "$output" >&2
  exit 2
fi

exit 0
