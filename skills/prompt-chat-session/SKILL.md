---
name: prompt-chat-session
description: Resume an in-progress /prompt-chat session at the given run directory. This skill is launched by /prompt-chat's "Open new window" isolation mode (`claude '/prompt-chat-session <run-dir>'`) in a fresh Claude Code instance — it skips the bootstrap and run-mode-selection phases (already done by the parent /prompt-chat invocation) and enters the chat loop directly. Not normally invoked manually; the parent skill spawns it.
---

# prompt-chat-session

You are entering an **in-progress** `/prompt-chat` session. The run directory at `$1` was set up by `/prompt-chat`'s Phase 0–1 in a separate Claude Code instance (the "parent" session). Every state file is already on disk:

- `body.txt` — the prompt body (simulated system prompt)
- `frontmatter.json` — parsed prompt metadata (target_model, judge_model, report_language, prompt_sha256, etc.)
- `chat.jsonl` — append-only conversation log (may be empty on first turn, or carry prior turns from a resumed session)
- `saved_anchors.json` — staging area for `/save` (may be empty)
- `session.json` — session metadata (turns count, isolation_mode, started_at)

Your job is to **continue from Phase 2 (Welcome) onwards** of the parent `/prompt-chat` skill's lifecycle: render the welcome screen, then run the chat loop + slash commands + chat-simulator dispatch + save / commit / quit sub-flows, all against `$1`.

## Phase 0 — Argument parse and state-file validation

Parse `$1`. Expected shape: absolute path to a directory like `<repo>/.promptcheck/<basename>/chat-NNN/`. Derive:

```bash
RUN_DIR="$1"
BASENAME=$(basename "$(dirname "$RUN_DIR")")     # the prompt basename (parent dir name)
RUN_NAME=$(basename "$RUN_DIR")                  # chat-NNN
REPO_ROOT=$(cd "$RUN_DIR/../../.." && pwd)       # strip .promptcheck/<basename>/chat-NNN to get repo root

# Sanity checks — fail fast with clear error if anything is missing.
for f in body.txt frontmatter.json chat.jsonl saved_anchors.json session.json; do
  if [ ! -f "$RUN_DIR/$f" ]; then
    echo "ERROR: $RUN_DIR/$f missing — this run dir was not bootstrapped by /prompt-chat."
    echo "Did you mean to run /prompt-chat <prompt-path> instead?"
    exit 1
  fi
done

# ABS_PROMPT comes from session.json (the parent skill stored it).
ABS_PROMPT=$(python3 -c "import json; print(json.load(open('$RUN_DIR/session.json'))['prompt_path'])")
if [ ! -f "$ABS_PROMPT" ]; then
  echo "ERROR: prompt file referenced in session.json no longer exists: $ABS_PROMPT"
  exit 1
fi

echo "RUN_DIR=$RUN_DIR"
echo "BASENAME=$BASENAME"
echo "RUN_NAME=$RUN_NAME"
echo "ABS_PROMPT=$ABS_PROMPT"
echo "REPO_ROOT=$REPO_ROOT"
```

If any check fails, surface the error to the user and exit. Do not proceed into the chat loop with a partially-bootstrapped directory.

Update `session.json` to record that the session was resumed in this new window:

```bash
python3 - "$RUN_DIR" <<'PY'
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
```

## Phase 1 — Execute Phase 2 through Phase 8 of `/prompt-chat`

Read `skills/prompt-chat/SKILL.md` once at the start of this skill's run. Then execute its **Phase 2 (Welcome screen) through Phase 8 (`/quit` final)** verbatim, with these context substitutions (the values come from Phase 0 above, not from a fresh bootstrap):

| Token in /prompt-chat | This skill's value |
|---|---|
| `$RUN_DIR` | `$1` (parsed in Phase 0) |
| `$ABS_PROMPT` | from `session.json.prompt_path` |
| `$BASENAME` | derived from the run dir's parent directory name |
| `$REPO_ROOT` | derived by stripping `.promptcheck/<basename>/chat-NNN` from `$RUN_DIR` |
| `frontmatter.report_language` | read from `$RUN_DIR/frontmatter.json` |
| `frontmatter.target_model` | read from `$RUN_DIR/frontmatter.json` |
| `session.turns` / `session.saved_anchors` | already maintained in session.json by `/prompt-chat`'s Phase 3 / 5 / 7 logic |

**Critical:** the chat loop (Phase 3 of /prompt-chat) reads existing `chat.jsonl` entries before generating the next assistant turn. If `chat.jsonl` is empty, the chat-simulator subagent (dispatched in Phase 6) produces the persona's opening greeting per its "first turn" contract. The first user message you receive in THIS skill is the start of (or continuation of) the conversation; treat it as the next `user_input` turn and follow Phase 3.2's append + dispatch + render flow.

**Skip Phase 1 (Run mode selection)** — it already ran in the parent session and chose "Open new window". Re-running it here would create a recursive spawn loop.

**Phase 2 (Welcome screen) render:** show `isolation: yeni pencere` (TR) / `isolation: new window` (EN) so the user knows they're in the spawned window, not the original.

**Phase 4 (Slash command dispatch)** through **Phase 8 (`/quit` final)** execute identically — they are state-file operations that work the same regardless of which Claude Code instance is hosting them.

## What changes vs. running /prompt-chat directly

Nothing functional. This skill is purely an entry point that picks up an existing run directory instead of creating one. The user experience inside the chat loop is identical:

- Conversation accumulates in `chat.jsonl` exactly the same way.
- `/save`, `/history`, `/reset`, `/commit`, `/quit` behave exactly the same.
- The `chat-simulator` subagent is dispatched per user turn identically.
- `/commit` writes to `<prompt>.anchors.yaml` (sidecar) identically.

When the user runs `/quit`, the skill exits and this Claude Code window can be closed. The original `/prompt-chat` instance in the parent window already exited at the end of its Phase 1 (after spawning this one).

## Failure modes

- **`$1` is not a valid run directory** → Phase 0's sanity check catches it; print the error message and exit.
- **`session.json.prompt_path` points at a deleted prompt file** → Phase 0's check catches it; tell the user the prompt was moved/deleted and exit.
- **State files exist but are corrupted (parse errors)** → fall through to the chat loop and rely on Phase 3's atomic-append + JSON parsing to surface errors per turn.
- **User invokes this skill without an argument** → bash sanity check on `$1`; print `usage: /prompt-chat-session <run-dir>` and exit.

## When to use this skill manually

Normally you should NOT invoke this skill directly. Use `/prompt-chat <prompt-path>` instead — it bootstraps the run dir AND launches the chat session. This sub-skill exists for two cases:

1. **"Open new window" mode of /prompt-chat** (the primary case): the parent skill spawns `claude '/prompt-chat-session <run-dir>'` in a fresh Terminal/tmux window. The user does not type this command themselves.
2. **Resuming a session** (future enhancement): if you exited a chat session early (closed the window without `/quit`), you can manually re-enter via `claude '/prompt-chat-session <existing-run-dir>'`. The session state is preserved across runs.
