#!/bin/bash
# PreToolUse hook used ONLY by the `reviewer` subagent (see .claude/agents/reviewer.md).
#
# A subagent's `tools` list can allow or deny Bash as a whole, but not specific commands.
# The reviewer needs `git diff` to see changes, so it gets Bash, and this hook narrows it
# to read-only git commands. Anything else is blocked with exit 2.

command=$(jq -r '.tool_input.command // empty')

# Refuse anything that chains, pipes or redirects (; & | > < ` $( ), so an allowed
# command can't smuggle a second one in, e.g. `git diff; rm -rf x` or `git diff > file`.
if grep -Eq '[;&|<>`]|\$\(' <<<"$command"; then
  echo "Reviewer is read-only: one plain git command at a time, no pipes or redirects." >&2
  exit 2
fi

# `git diff/log/show --output=FILE` writes a file, and `--ext-diff` can run an external
# program. Refuse both, even on otherwise read-only subcommands.
if grep -Eq -- '--output|--ext-diff' <<<"$command"; then
  echo "Reviewer is read-only: --output and --ext-diff are not allowed." >&2
  exit 2
fi

# Allow only these git subcommands, which never change the repo.
if grep -Eq '^[[:space:]]*git[[:space:]]+(diff|status|log|show|blame)([[:space:]]|$)' <<<"$command"; then
  exit 0
fi

echo "Reviewer is read-only: only git diff/status/log/show/blame are allowed." >&2
exit 2
