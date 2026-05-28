---
name: prompt-chat
description: Interactive chat simulator for a prompt file. Loads the prompt as the simulated system prompt, lets you converse with its persona via text, and lets you save interesting turns as test anchors. Use when the user runs /prompt-chat, asks to "chat with this prompt", "test the prompt by talking to it", or wants to iterate on a voice-agent prompt without calling Vapi. Produces chat.jsonl + optional anchors written to frontmatter.anchors[]. Never modifies the prompt body — only the frontmatter, and only on explicit /commit.
---

# prompt-chat

You give a prompt file `$1` and a conversation. The skill loads the prompt as the simulated system prompt, spawns a `chat-simulator` subagent per turn to produce the persona's reply, accumulates the conversation, and lets you save turns as test anchors that `/prompt-test` can later replay.

`/prompt-chat` is an exploratory tool. It is intentionally separate from `/prompt-check` (audit) and `/prompt-test` (regression) — different mental model, different state, different artefacts. The bridge between them is `frontmatter.anchors[]` (single source of truth for test scenarios).

## Inputs you have

- `$1` — relative or absolute path to the prompt file under chat.
- `agents/chat-simulator.md` — the subagent that produces each assistant turn (you read its definition once; dispatching it for every turn is the chat loop).

## Phase 0 — Bootstrap

Read `$1`, parse its frontmatter, write a run directory under `.promptcheck/<basename>/chat-NNN/`.

```bash
ABS_PROMPT=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
BASENAME=$(basename "$1" | sed 's/\.[^.]*$//')
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PROMPT_DIR="$REPO_ROOT/.promptcheck/$BASENAME"
mkdir -p "$PROMPT_DIR"

# Atomic chat-NNN allocation. mkdir without -p fails if the directory exists,
# so a concurrent run claiming the same number loses cleanly and we retry.
ATTEMPT=1
while [ "$ATTEMPT" -le 100 ]; do
  N=$(ls -1 "$PROMPT_DIR" 2>/dev/null | grep -c '^chat-')
  NEXT_NUM=$((N + ATTEMPT))
  RUN_NAME=$(printf 'chat-%03d' "$NEXT_NUM")
  RUN_DIR="$PROMPT_DIR/$RUN_NAME"
  if mkdir "$RUN_DIR" 2>/dev/null; then break; fi
  ATTEMPT=$((ATTEMPT + 1))
done
if [ "$ATTEMPT" -gt 100 ]; then
  echo "error: could not allocate a free chat-NNN slot in $PROMPT_DIR"
  exit 1
fi

echo "RUN_DIR=$RUN_DIR"
echo "BASENAME=$BASENAME"
```

Then parse frontmatter + body. Reuse the exact Python heredoc pattern from `skills/prompt-check/SKILL.md` Phase 2 (don't re-derive it — same parsing, same atomic write):

```bash
python3 - "$ABS_PROMPT" "$RUN_DIR" <<'PY'
import sys, re, json, os, hashlib
prompt_path, run_dir = sys.argv[1], sys.argv[2]
text = open(prompt_path, encoding='utf-8').read()

prompt_sha256 = hashlib.sha256(text.encode('utf-8')).hexdigest()

m = re.match(r'^---\r?\n(.*?)\r?\n---\r?\n?(.*)$', text, re.DOTALL)
if m:
    raw_fm = m.group(1)
    body = m.group(2)
    pre_body = text[:m.start(2)]
    body_line_offset = pre_body.count('\n') + 1
else:
    raw_fm = ''
    body = text
    body_line_offset = 1

fm = {}
try:
    import yaml
    fm = yaml.safe_load(raw_fm) or {} if raw_fm else {}
except Exception:
    for line in (raw_fm or '').splitlines():
        if ':' in line and not line.lstrip().startswith('-'):
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip()

# Resolve defaults (target_model, report_language) the same way /prompt-check does.
# We keep this minimal — the chat skill only needs target_model + report_language.
resolved = {
    'target_model':     fm.get('target_model') or 'claude-opus-4-7',
    'report_language':  (fm.get('report_language') or 'tr').lower(),
    'body_char_count':  len(body),
    'body_line_offset': body_line_offset,
    'prompt_sha256':    prompt_sha256,
    'existing_anchors_count': len(fm.get('anchors') or []),
}
if resolved['report_language'] not in ('tr', 'en'):
    resolved['report_language'] = 'tr'

with open(os.path.join(run_dir, 'frontmatter.json'), 'w', encoding='utf-8') as f:
    json.dump(resolved, f, indent=2, ensure_ascii=False)
with open(os.path.join(run_dir, 'body.txt'), 'w', encoding='utf-8') as f:
    f.write(body)
PY
```

Bootstrap empty state files (atomic, idempotent):

```bash
# chat.jsonl — append-only conversation log. Each line: {role, content, ts}.
: > "$RUN_DIR/chat.jsonl"

# saved_anchors.json — staging area for anchors captured via /save.
# Empty array on bootstrap. Phase 7 (/commit) drains this into the prompt
# file's frontmatter.anchors[]; Phase 8 (/quit) offers to commit if non-empty.
printf '[]' > "$RUN_DIR/saved_anchors.json"

# session.json — durable record of the chat session.
python3 - "$RUN_DIR" "$ABS_PROMPT" <<'PY'
import sys, json, os, datetime
run_dir, prompt_path = sys.argv[1], sys.argv[2]
fm = json.load(open(os.path.join(run_dir, 'frontmatter.json'), encoding='utf-8'))
session = {
    "schema_version": 1,
    "run_id": os.path.basename(run_dir),
    "started_at": datetime.datetime.utcnow().isoformat() + "Z",
    "prompt_path": prompt_path,
    "prompt_sha256_at_chat": fm['prompt_sha256'],
    "target_model": fm['target_model'],
    "report_language": fm['report_language'],
    "turns": 0,
    "saved_anchors": 0,
    "isolation_mode": None,  # filled by Phase 1
}
with open(os.path.join(run_dir, 'session.json'), 'w', encoding='utf-8') as f:
    json.dump(session, f, indent=2, ensure_ascii=False)
PY
```

## Phase 1 — Run mode selection (isolation)

You are running in the user's main Claude Code session. The chat loop in Phase 3 will accumulate user/assistant turns. **Without isolation, those turns pollute the main session's context** — the main-session assistant persona gets confused, and the user has trouble switching back to "normal work" after the chat ends.

Three isolation strategies, in preference order:

1. **New window** (preferred when platform supports it) — spawn a new `claude` CLI subprocess in a separate Terminal / tmux window. The new process runs `/prompt-chat-session <run-dir>` (see below). Full process + memory + context isolation. Main session exits this skill immediately.
2. **In-session** (fallback) — the skill enters its own loop and stays in the main session. The user only talks to the bot until `/quit`. Mental isolation; process is shared.
3. **Setup-only** (manual fallback) — the skill prepares `$RUN_DIR` and prints the manual command to run; user opens a new window themselves.

### Pre-flight check

```bash
PLATFORM=$(uname)
CLAUDE_CLI=$(command -v claude || true)
NEW_WINDOW_OK=false

if [ -n "$CLAUDE_CLI" ]; then
  case "$PLATFORM" in
    Darwin)
      # osascript is built-in on macOS; Terminal.app ships with macOS.
      command -v osascript >/dev/null && NEW_WINDOW_OK=true
      ;;
    Linux)
      if command -v tmux >/dev/null || command -v gnome-terminal >/dev/null; then
        NEW_WINDOW_OK=true
      fi
      ;;
  esac
fi
echo "PLATFORM=$PLATFORM CLAUDE_CLI=$CLAUDE_CLI NEW_WINDOW_OK=$NEW_WINDOW_OK"
```

### Ask the user

Emit AskUserQuestion (mandatory — do not silently default):

```
question (TR):  "Chat oturumunu nasıl başlatayım?"
question (EN):  "How should the chat session be started?"
header:         "Isolation"
multiSelect:    false
options:
  - label: "Yeni pencere aç" | "Open new window"
    description: "Ayrı Terminal/tmux penceresinde yeni Claude session'ı. Tam izolasyon. (önerilen)"
    enabled: NEW_WINDOW_OK
  - label: "Burada (in-session)" | "Here (in-session)"
    description: "Ana oturumda devam et. Skill kendi loop'unda kalır, /quit'e kadar sadece bot ile konuşursun."
  - label: "Setup'ı yap, ben elle açarım" | "Set up only, I'll open manually"
    description: "Skill run-dir'i hazırlayıp çıkar; kullanıcı manuel `claude '/prompt-chat-session <run-dir>'` yazar."
```

Wording follows `frontmatter.report_language` from Phase 0. If `NEW_WINDOW_OK == false`, surface the first option as disabled with a footnote (`platform desteği yok — tmux veya macOS Terminal kur`).

### Dispatch per mode

**Mode = "new window":**

```bash
case "$PLATFORM" in
  Darwin)
    osascript -e "tell application \"Terminal\" to do script \"$CLAUDE_CLI '/prompt-chat-session $RUN_DIR'\"" >/dev/null
    ;;
  Linux)
    if command -v tmux >/dev/null; then
      tmux new-window -n "chat-$BASENAME" "$CLAUDE_CLI '/prompt-chat-session $RUN_DIR'"
    else
      gnome-terminal -- "$CLAUDE_CLI" "/prompt-chat-session $RUN_DIR"
    fi
    ;;
esac
```

Record `session.json.isolation_mode = "new_window"`. Print to the main session:

```
Chat oturumu yeni pencerede başlatıldı.
Run dir: <relative path to $RUN_DIR>
Yeni pencere kapandıktan sonra `cat <run-dir>/chat.jsonl` ile konuşmayı inceleyebilirsin.
```

**Then exit this skill.** Main session is freed; user converses in the new window.

**Mode = "in-session":**

Record `session.json.isolation_mode = "in_session"`. Continue to Phase 2 in the current session. Main asistan persona's is on pause until `/quit`.

**Mode = "setup-only":**

Record `session.json.isolation_mode = "setup_only"`. Print:

```
Setup tamamlandı. Yeni bir terminal aç ve şu komutu çalıştır:

  $CLAUDE_CLI '/prompt-chat-session $RUN_DIR'

Veya tamamen interaktif bir Claude Code penceresi açıp Claude'a şu mesajı yaz:

  /prompt-chat-session $RUN_DIR
```

Exit this skill.

### `/prompt-chat-session` subagent skill

A sibling skill at `skills/prompt-chat-session/SKILL.md` is invoked by the "new window" and "setup-only" modes. It takes `$1 = <run-dir>` (an existing chat-NNN directory created by `/prompt-chat` Phase 0), skips Phase 0-1, and enters Phase 2 directly. The two skills share Phase 2-8 logic — extract to a helper or document the duplication.

For the MVP, the `prompt-chat-session` skill is a thin wrapper that re-enters this same file's Phase 2 onwards using the supplied `$RUN_DIR`. The `BASENAME` is derived from the parent directory name. Implementation can defer to a follow-up commit if Phase 2-8 below is generic enough.

## Phase 2 — Welcome screen

Render in `report_language`:

**TR:**
```
/prompt-chat başlatıldı.
Prompt: <basename> (<line count> satır, model: <target_model>)
İzolasyon: <yeni pencere | in-session | setup-only>

Yaz, ben prompt'a göre cevap vereyim.

Komutlar:
  /save           — son turu anchor olarak kaydet (staging)
  /history        — bu oturumdaki turları göster
  /reset          — geçmişi sil, baştan başla
  /commit         — staged anchor'ları frontmatter'a yaz
  /quit           — çıkış + final summary
```

**EN:** same content, English labels.

After printing, **stop**. Wait for the user's first message (Phase 3 starts on their next turn).

**Crucial:** do not call any tool after printing the welcome. Skill emits the welcome text as its assistant turn, then the user types their first message, which becomes the next user turn. The skill resumes in Phase 3.

## Phase 3 — Chat loop (per user turn)

Each time the user sends a message, this is your one assistant turn to process it:

### Step 3.1 — Slash command check

If `user_message.strip().startswith("/")`:
- Parse the command + optional args
- Dispatch to Phase 4
- (Slash commands do NOT go to chat.jsonl; they are skill-level control)
- Return to chat loop after Phase 4 completes

### Step 3.2 — Normal message handling

Append to `chat.jsonl` (atomic per-line append):

```bash
python3 - "$RUN_DIR" "$USER_MESSAGE" <<'PY'
import sys, json, os, datetime
run_dir, user_message = sys.argv[1], sys.argv[2]
entry = {
    "role": "user",
    "content": user_message,
    "ts": datetime.datetime.utcnow().isoformat() + "Z"
}
with open(os.path.join(run_dir, 'chat.jsonl'), 'a', encoding='utf-8') as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
PY
```

Then invoke the `chat-simulator` subagent (Phase 6 for the dispatch shape). Wait for it to return.

Read `<RUN_DIR>/next_turn.txt`, strip leading/trailing whitespace, append as assistant entry:

```bash
python3 - "$RUN_DIR" <<'PY'
import sys, json, os, datetime
run_dir = sys.argv[1]
output_path = os.path.join(run_dir, 'next_turn.txt')
text = open(output_path, encoding='utf-8').read().strip()
entry = {
    "role": "assistant",
    "content": text,
    "ts": datetime.datetime.utcnow().isoformat() + "Z"
}
with open(os.path.join(run_dir, 'chat.jsonl'), 'a', encoding='utf-8') as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")

# Update session.json turn count.
session_path = os.path.join(run_dir, 'session.json')
session = json.load(open(session_path, encoding='utf-8'))
session['turns'] = session.get('turns', 0) + 1
tmp = session_path + '.tmp'
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(session, f, indent=2, ensure_ascii=False)
os.rename(tmp, session_path)

# Print just the assistant text to the user (no JSON wrap).
print(text)
PY
```

Render the assistant's reply to the user, followed by a compact footer:

```
<assistant text>

― [turn N · /save | /history | /commit | /quit]
```

The footer's separator (`―`) and label set follow `report_language`. Then stop, wait for the user's next turn.

**Loop invariant:** every turn = one user message → one chat-simulator dispatch → one assistant reply → wait. No batching. No look-ahead. The skill returns control to the user after every assistant emission.

## Phase 4 — Slash command dispatch

User typed something starting with `/`. Parse the command (lowercase, strip leading slash):

| Command | Action |
|---|---|
| `save` | Phase 5 — anchor save sub-flow (filled in Phase C of the implementation) |
| `history` | Pretty-print `chat.jsonl` (turn N · role · first 80 chars), then return to chat loop |
| `reset` | Move `chat.jsonl` to `chat-N-discarded.jsonl`, create fresh empty `chat.jsonl`, update `session.json.turns = 0`. Inform user. saved_anchors.json is NOT touched. |
| `commit` | Phase 7 — frontmatter write (filled in Phase C) |
| `quit` | Phase 8 — final summary + optional commit prompt (filled in Phase C) |
| anything else | Print "Bilinmeyen komut: `/<x>`. Mevcut: /save /history /reset /commit /quit" and return |

For MVP (this commit), the following commands are stubs that print a placeholder until Phase C lands:

- `save` → "Phase 5 henüz implementasyon bekliyor. Bu çağrı no-op."
- `commit` → "Phase 7 henüz implementasyon bekliyor. Bu çağrı no-op."
- `quit` → directly print Phase 8 summary (turns count, run dir path) and exit the skill. saved_anchors handling skipped.

`history` and `reset` are fully implemented in this MVP — they don't depend on save/commit logic.

## Phase 5 — Anchor save sub-flow

**[Implementation deferred to Phase C of the rollout. See plan: /Users/onur/.claude/plans/immutable-riding-moth.md]**

Outline for forward reference:

1. Read last user + assistant turns from chat.jsonl
2. AskUserQuestion (4 steps): expect_contains, expect_not_contains, rubric, preview-and-confirm
3. Option to include prior context (single-turn vs prior-context anchor)
4. Atomic append to saved_anchors.json
5. Inform user: "Anchor #N kaydedildi (staging). /commit ile frontmatter'a yazılır."

## Phase 6 — `chat-simulator` agent invocation

Per user message in Phase 3, dispatch the `chat-simulator` subagent. One dispatch per user turn.

```javascript
Agent({
  subagent_type: "chat-simulator",
  prompt: JSON.stringify({
    inputs: {
      body:                 "<absolute path to $RUN_DIR/body.txt>",
      conversation_history: "<absolute path to $RUN_DIR/chat.jsonl>",
      target_model:         "<string from frontmatter.target_model>",
      report_language:      "<string from frontmatter.report_language>"
    },
    output_path: "<absolute path to $RUN_DIR/next_turn.txt>"
  }),
  description: "chat turn " + N + " for " + BASENAME,
  isolation: "worktree"
})
```

`N` is the upcoming assistant turn number (= `session.json.turns + 1`).

The subagent reads `body.txt` as the simulated system prompt, reads `chat.jsonl` as conversation history (the last `role: user` entry is the turn to answer), produces the next assistant turn, and writes it to `next_turn.txt` as plain text.

Wait for the subagent to return. Then continue Step 3.2 (read `next_turn.txt`, append to chat.jsonl).

If the subagent reports an error (its status line starts with `chat-simulator error:`), surface the error to the user and skip the chat.jsonl assistant append:

```
[Bot şu an cevap üretemedi: <error reason>. Devam etmek için tekrar yaz veya /quit yaz.]
```

## Phase 7 — Frontmatter commit (`/commit`)

**[Implementation deferred to Phase C.]**

Outline:

1. Load `saved_anchors.json`
2. If empty, print "Staged anchor yok. Önce /save kullan." and return
3. Atomic write to prompt file's frontmatter (yaml.safe_dump + temp+rename)
4. Validate post-write parse (rollback on fail)
5. Rename `saved_anchors.json` → `committed-<timestamp>.json`
6. Print "N anchor frontmatter'a yazıldı. /prompt-test ile çalıştırabilirsin."

## Phase 8 — `/quit` final

**[Full implementation deferred to Phase C; MVP stub below.]**

MVP stub:

```
/prompt-chat oturumu kapatıldı — chat-NNN
- <N> turn konuşma (chat.jsonl)
- Run dir: .promptcheck/<basename>/chat-NNN/
```

Phase C adds: if `saved_anchors.json` non-empty, ask "Frontmatter'a yazayım mı?" before exit.

After printing the summary, the skill exits. If isolation_mode was `in_session`, the main session's normal asistan resumes.

## Invariants

- **Never read `next_turn.txt`** outside Step 3.2 (after the subagent writes it). Reading it earlier burns tool calls on a non-existent file.
- **Never modify `body.txt`** in any phase. Body is read-only after Phase 0.
- **Never modify the original prompt file outside Phase 7.** All chat-time state is in `chat-NNN/`.
- **Slash commands are case-insensitive.** `/SAVE` works the same as `/save`.
- **Every persisted state update is atomic** (temp + rename) — never partial writes.
- **`chat.jsonl` is append-only.** Never overwrite, never delete mid-session. `/reset` moves the file aside; it does not delete it.
