#!/bin/bash
# PreToolUse hook (Bash): stop Claude from running `git push`.
#
# Claude Code sends the tool call as JSON on stdin, e.g.
#   {"tool_name": "Bash", "tool_input": {"command": "git push origin main"}, ...}
# Exit 2 = block the call; whatever we print to stderr is shown to Claude as the reason.
# Exit 0 = no opinion; the normal permission flow carries on.

command=$(jq -r '.tool_input.command // empty')

# Match `git` at the start of a command segment (start of line, or after ; & | ( or a quote),
# optionally followed by git's own options (-C <path>, -c <key=value>, --no-pager, ...),
# and then the `push` subcommand. That catches `git push`, `cd x && git push`,
# `git -C repo push`, `bash -c "git push"`, but not `git commit -m "fix push bug"`.
# It's a seatbelt, not a sandbox: a git alias (`git p`) or `env git push` would get past it.
# The CLAUDE.md rule covers those cases; this catches the normal ways of pushing.
segment_start='(^|[;&|(`"'"'"'])[[:space:]]*'
git_binary='([^[:space:];&|]*/)?git'  # `git`, or a full path like /usr/bin/git
git_options='([[:space:]]+(-C|-c)[[:space:]]+[^[:space:]]+|[[:space:]]+-[^[:space:]]+)*'
# `push` must end the word (followed by a space, a quote, ; etc., or end of line).
pattern="${segment_start}${git_binary}${git_options}[[:space:]]+push([^[:alnum:]_-]|$)"

if grep -Eq "$pattern" <<<"$command"; then
  echo "Pushing is manual: run it yourself." >&2
  exit 2
fi

exit 0
