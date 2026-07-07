---
name: prompt-chat-session
description: Resume an in-progress /prompt-chat session at the given run directory. Launched by /prompt-chat in a fresh window (`claude '/prompt-chat-session <run-dir>'`) — validates the run dir, then execs bin/prompt-chat-runner.py. Not normally invoked manually.
---

# prompt-chat-session

You are entering an **in-progress** `/prompt-chat` session that was bootstrapped by `/prompt-chat`'s Phase 0–1 in a separate Claude Code instance (the "parent" session). Your job is **purely structural**:

1. Validate the run directory has the expected state files.
2. Record that the session is being resumed in this new window.
3. Hand off to `bin/prompt-chat-runner.py` — the Python orchestrator that drives the actual chat loop via a single long-lived `claude` subprocess.

**You do NOT host the chat loop yourself.** The whole point of v0.5.6 was to escape the per-turn skill / subagent dispatch cost. The Python script runs the chat as a stream-json subprocess that bypasses skill instructions, CLAUDE.md auto-discovery, tool definitions, and other Claude Code overhead — taking per-turn cost from ~32k tokens / ~50s (v0.5.4) down to ~3-5k tokens / ~3-5s (v0.5.6).

## Phase 0 — Argument parse and state-file validation

Parse `$1`. Expected shape: absolute path to a directory like `<repo>/.voicepromptkit/<basename>/chat-NNN/`.

```bash
RUN_DIR="$1"
if [ -z "$RUN_DIR" ] || [ ! -d "$RUN_DIR" ]; then
  echo "usage: /prompt-chat-session <run-dir>"
  echo "error: run-dir must be a valid directory created by /prompt-chat Phase 0"
  exit 1
fi

BASENAME=$(basename "$(dirname "$RUN_DIR")")     # the prompt basename (parent dir name)
RUN_NAME=$(basename "$RUN_DIR")                  # chat-NNN
REPO_ROOT=$(cd "$RUN_DIR/../../.." && pwd)       # strip .voicepromptkit/<basename>/chat-NNN to get repo root

# Sanity checks — bootstrap state files.
for f in body.txt frontmatter.json chat.jsonl saved_anchors.json session.json; do
  if [ ! -f "$RUN_DIR/$f" ]; then
    echo "ERROR: $RUN_DIR/$f missing — this run dir was not bootstrapped by /prompt-chat."
    echo "Did you mean to run /prompt-chat <prompt-path> instead?"
    exit 1
  fi
done

# Resolve the runner script path (lives at repo root: bin/prompt-chat-runner.py).
RUNNER="$REPO_ROOT/bin/prompt-chat-runner.py"
if [ ! -f "$RUNNER" ]; then
  # Fall back to the plugin's installed location if the dev repo isn't here.
  # The marketplace install lands under the versioned cache path; the glob
  # matches any installed version (first match wins — good enough for a fallback).
  for guess in \
    "$HOME/.claude/plugins/cache/onurgoz-labs/VoicePromptKit"/*/bin/prompt-chat-runner.py \
    "$HOME/.claude/plugins/VoicePromptKit/bin/prompt-chat-runner.py" \
    "/usr/local/share/claude/plugins/VoicePromptKit/bin/prompt-chat-runner.py"; do
    if [ -f "$guess" ]; then RUNNER="$guess"; break; fi
  done
fi
if [ ! -f "$RUNNER" ]; then
  echo "ERROR: bin/prompt-chat-runner.py not found near $REPO_ROOT or in standard plugin paths."
  echo "Reinstall VoicePromptKit (the runner script is part of the plugin distribution)."
  exit 1
fi

# Update session.json: record that we entered through the new-window path.
PYTHON_CLI=$(command -v python3 || command -v python || command -v py || echo python3)
"$PYTHON_CLI" - "$RUN_DIR" <<'PY'
import sys, json, os, datetime
run_dir = sys.argv[1]
session_path = os.path.join(run_dir, 'session.json')
session = json.load(open(session_path, encoding='utf-8'))
session['isolation_mode'] = 'new_window'
session['resumed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
tmp = session_path + '.tmp'
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(session, f, indent=2, ensure_ascii=False)
os.rename(tmp, session_path)
PY

echo "RUN_DIR=$RUN_DIR"
echo "RUNNER=$RUNNER"
```

## Phase 1 — Hand off to the Python orchestrator

Replace this Claude Code process with the Python script via `exec`. The Python runner owns the terminal from here onward: it prints the welcome screen, reads user stdin, manages slash commands (/save /history /reset /commit /quit), and shells out to a SINGLE long-lived `claude --input-format stream-json` subprocess for each non-slash user turn.

```bash
PYTHON_CLI=$(command -v python3 || command -v python || command -v py || echo python3)
exec "$PYTHON_CLI" "$RUNNER" "$RUN_DIR"
```

After `exec`, this Claude Code session no longer drives the terminal — Python does. When the user runs `/quit` inside the Python REPL, the Python process exits with status 0 and the Terminal window can be closed.

## Why this skill is intentionally thin

The Python script is the single source of truth for the chat loop. The skill exists only to:
- Be addressable as a slash command from the spawning Terminal (`claude '/prompt-chat-session <run-dir>'`).
- Validate the run directory before handing off (fail-fast UX).
- Update session.json with the new-window isolation marker.

Any feature changes to the chat loop — slash commands, anchor schema, model selection, streaming behaviour — live in `bin/prompt-chat-runner.py`, NOT here. This file should never grow beyond a few dozen lines of bash + the exec call.

## Failure modes

- **`$1` is missing or not a directory** → bash sanity check; exit with usage hint.
- **State files missing** → validation loop catches it; exit with "did you mean /prompt-chat?" hint.
- **Python runner script not found** → search standard paths; if all fail, ask the user to reinstall VoicePromptKit.
- **`exec` fails** (very rare, e.g. corrupted Python install) → the Claude Code process continues briefly with an error message before exiting on its own.

## When to use this skill manually

Normally you should NOT invoke this skill directly. Use `/prompt-chat <prompt-path>` instead — it bootstraps the run dir AND launches the chat session via this skill. This sub-skill exists for two cases:

1. **"Open new window" mode of /prompt-chat** (the primary case): the parent skill spawns `claude '/prompt-chat-session <run-dir>'` in a fresh Terminal / tmux window. The user does not type this command themselves.
2. **Resuming a session** (interrupted earlier without /quit): you can re-enter via `claude '/prompt-chat-session <existing-run-dir>'`. session.json's chat_session_uuid is preserved across runs, so the Claude conversation context is restored as long as it hasn't been GC'd by Claude Code's session storage.
