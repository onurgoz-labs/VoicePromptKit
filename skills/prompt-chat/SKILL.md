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
    "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
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
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()
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
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()
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

All five commands are implemented: `/save` dispatches to Phase 5, `/commit` to Phase 7, `/quit` to Phase 8 (with optional commit prompt for any staged anchors). `/history` and `/reset` execute inline in this phase as they are simple state operations.

## Phase 5 — Anchor save sub-flow

User typed `/save`. Capture the most recent user → assistant exchange as a test anchor staged in `saved_anchors.json`. The user fills in expected behaviour via four AskUserQuestion prompts; we never write to the prompt file in this phase (frontmatter changes happen only in Phase 7 / `/commit`).

### Step 5.1 — Locate the last turn

```bash
python3 - "$RUN_DIR" <<'PY'
import sys, json, os
run_dir = sys.argv[1]
entries = []
with open(os.path.join(run_dir, 'chat.jsonl'), encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

# Find the last user→assistant pair. Walk back to the most recent assistant
# entry whose immediate predecessor is a user entry.
last_assistant = None
last_user = None
for i in range(len(entries) - 1, -1, -1):
    if entries[i].get('role') == 'assistant' and i > 0 and entries[i-1].get('role') == 'user':
        last_assistant = entries[i]
        last_user = entries[i-1]
        prior_context = entries[:i-1]  # everything before the user turn we're saving
        break

if last_user is None:
    print("ERROR: no completed user→assistant turn yet — converse at least once before /save", file=sys.stderr)
    sys.exit(1)

print(json.dumps({
    "user_content": last_user.get('content', ''),
    "assistant_content": last_assistant.get('content', ''),
    "prior_context_count": len(prior_context),
    "prior_context": prior_context,
}, ensure_ascii=False))
PY
```

If the script exits non-zero (no user→assistant exchange yet), surface to user:

- TR: `Henüz tam bir tur yok. Önce bir mesaj yaz, bot cevap versin, sonra /save kullan.`
- EN: `No complete user→assistant turn yet. Send a message and wait for the bot's reply before /save.`

…and return to the chat loop.

### Step 5.2 — Ask: expect_contains

`AskUserQuestion` (free-form):

```
question (TR): "Yanıtta hangi sözcükler / ifadeler bulunmalı? (virgülle ayır, boş bırakılabilir)"
question (EN): "What words / phrases SHOULD the reply contain? (comma-separated, may be empty)"
header:        "expect_contains"
```

Parse the user's free-form answer: split on `,`, trim whitespace, drop empty entries. Result is a string list. Store as `expect_contains`.

### Step 5.3 — Ask: expect_not_contains

```
question (TR): "Yanıtta hangi sözcükler / ifadeler bulunmamalı? (virgülle ayır, boş bırakılabilir)"
question (EN): "What words / phrases SHOULD NOT the reply contain? (comma-separated, may be empty)"
header:        "expect_not_contains"
```

Same parsing as 5.2. Store as `expect_not_contains`.

### Step 5.4 — Ask: rubric

```
question (TR): "LLM judge'a verilecek davranış kuralı (opsiyonel, ≤ 200 karakter)"
question (EN): "Rubric for the LLM judge (optional, ≤ 200 chars)"
header:        "rubric"
```

Single string, may be empty. Store as `rubric`.

### Step 5.5 — Ask: context inclusion (single-turn vs prior-context)

This is the codex-recommended escape hatch from the single-turn-only limitation.

```
question (TR): "Bu anchor'ı nasıl kaydedeyim?"
question (EN): "How should I save this anchor?"
header:        "context"
multiSelect:   false
options:
  - label: "Sadece bu turu" | "Just this turn"  
    description: "Stateless test — drift-runner sadece bu input'u gönderecek. (önerilen) (Recommended)"
  - label: "Önceki turları bağlam olarak ekle" | "Include prior turns as context"
    description: "State-aware test — drift-runner önceki <N> turu conversation history olarak verir, sonra input'u son user turn olarak ekler."
```

If `prior_context_count == 0`, skip this question (no prior context exists) — default to "Just this turn".

If user picks "Include prior", construct the `context` array from `prior_context`: each entry becomes `{role: "user" | "assistant", content: "..."}` (drop `ts`).

If "Just this turn", set `context = []` (or omit the field entirely — drift-runner treats missing/empty as single-turn).

### Step 5.6 — Preview and confirm

Render the staged anchor:

```
Önizleme (Anchor #<N>):
  input: "<user_content>"
  context: <"yok" | "<K> prior turn">
  expect_contains: [<items>] | (none)
  expect_not_contains: [<items>] | (none)
  rubric: "<rubric>" | (none)
```

```
question (TR): "Kaydedeyim mi?"
question (EN): "Save?"
header:        "Confirm"
multiSelect:   false
options:
  - label: "Evet, kaydet" | "Yes, save"
    description: "Stage this anchor in saved_anchors.json. /commit will write it to frontmatter later."
  - label: "Düzenle" | "Edit"
    description: "Re-run the 4 questions to fix the fields."
  - label: "İptal" | "Cancel"
    description: "Discard this attempt; nothing is staged."
```

- **Yes** → Step 5.7
- **Edit** → loop back to Step 5.2
- **Cancel** → no-op, return to chat loop

### Step 5.7 — Atomic append to saved_anchors.json

```bash
python3 - "$RUN_DIR" "$USER_CONTENT" "$EXPECT_CONTAINS_JSON" "$EXPECT_NOT_CONTAINS_JSON" "$RUBRIC" "$CONTEXT_JSON" <<'PY'
import sys, json, os
run_dir = sys.argv[1]
user_content = sys.argv[2]
expect_contains = json.loads(sys.argv[3])         # list[str]
expect_not_contains = json.loads(sys.argv[4])     # list[str]
rubric = sys.argv[5]
context = json.loads(sys.argv[6])                 # list[{role,content}] or []

staged_path = os.path.join(run_dir, 'saved_anchors.json')
staged = json.load(open(staged_path, encoding='utf-8'))

anchor = {"input": user_content}
if context:
    anchor["context"] = context
if expect_contains:
    anchor["expect_contains"] = expect_contains
if expect_not_contains:
    anchor["expect_not_contains"] = expect_not_contains
if rubric:
    anchor["rubric"] = rubric

staged.append(anchor)

tmp = staged_path + '.tmp.' + str(os.getpid())
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(staged, f, indent=2, ensure_ascii=False)
os.rename(tmp, staged_path)

# Update session.json staged count.
session_path = os.path.join(run_dir, 'session.json')
session = json.load(open(session_path, encoding='utf-8'))
session['saved_anchors'] = len(staged)
tmp = session_path + '.tmp.' + str(os.getpid())
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(session, f, indent=2, ensure_ascii=False)
os.rename(tmp, session_path)

print(len(staged))
PY
```

Print to user (in `report_language`):

- TR: `Anchor #<N> kaydedildi (staging). Toplam: <N> staged. /commit ile frontmatter'a yazılır.`
- EN: `Anchor #<N> staged. Total: <N>. Use /commit to write them to frontmatter.`

Return to chat loop.

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

User typed `/commit`. Atomic-write the staged anchors from `saved_anchors.json` into the prompt file's `frontmatter.anchors[]`. The body of the prompt is NEVER touched — only the YAML header.

### Step 7.1 — Load staged anchors

```bash
STAGED_COUNT=$(python3 -c "import json; print(len(json.load(open('$RUN_DIR/saved_anchors.json'))))")
```

If `STAGED_COUNT == 0`:

- TR: `Staged anchor yok. Önce /save kullan.`
- EN: `No staged anchors. Use /save first.`

…and return to chat loop.

### Step 7.2 — Atomic frontmatter write

The write is in three stages: build the new file content, write to a temp file, validate the temp by parsing it back, then atomically rename. Rollback on any failure.

```bash
python3 - "$ABS_PROMPT" "$RUN_DIR/saved_anchors.json" <<'PY'
import sys, os, re, json, yaml, datetime, shutil

prompt_path, staged_path = sys.argv[1], sys.argv[2]
staged = json.load(open(staged_path, encoding='utf-8'))
if not staged:
    print("WARN: nothing to commit", file=sys.stderr)
    sys.exit(0)

text = open(prompt_path, encoding='utf-8').read()

# Parse existing frontmatter (or none).
m = re.match(r'^---\r?\n(.*?)\r?\n---\r?\n?(.*)$', text, re.DOTALL)
if m:
    fm_raw, body = m.group(1), m.group(2)
    try:
        fm = yaml.safe_load(fm_raw) or {}
    except yaml.YAMLError as e:
        print(f"ERROR: existing frontmatter unparseable — aborting commit. {e}", file=sys.stderr)
        sys.exit(2)
else:
    fm, body = {}, text  # no frontmatter; we'll create one

existing_anchors = fm.get('anchors') or []
fm['anchors'] = existing_anchors + staged

# Emit with allow_unicode=True (TR characters), sort_keys=False (preserve author's
# key order — anchors appended to the end of whatever keys exist), default_flow_style=False
# (block style for readability).
new_fm = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=False)
new_text = f"---\n{new_fm}---\n{body}"

# Write temp, validate, rename.
tmp = prompt_path + '.chat-commit.tmp.' + str(os.getpid())
with open(tmp, 'w', encoding='utf-8') as f:
    f.write(new_text)

# Validation: re-parse temp file's frontmatter. If we can't, do not commit.
try:
    check_text = open(tmp, encoding='utf-8').read()
    check_m = re.match(r'^---\r?\n(.*?)\r?\n---\r?\n?(.*)$', check_text, re.DOTALL)
    if not check_m:
        raise ValueError("temp file has no frontmatter delimiter")
    check_fm = yaml.safe_load(check_m.group(1)) or {}
    if not isinstance(check_fm.get('anchors'), list):
        raise ValueError("temp file's anchors field is not a list")
    if len(check_fm['anchors']) != len(existing_anchors) + len(staged):
        raise ValueError(f"anchor count mismatch: got {len(check_fm['anchors'])}, expected {len(existing_anchors)+len(staged)}")
except Exception as e:
    os.remove(tmp)
    print(f"ERROR: post-write validation failed — rollback complete. {e}", file=sys.stderr)
    sys.exit(3)

# Validation passed — atomic rename.
os.rename(tmp, prompt_path)

# Archive saved_anchors.json with a committed timestamp prefix.
ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
committed_path = os.path.join(os.path.dirname(staged_path), f'committed-{ts}.json')
shutil.move(staged_path, committed_path)

# Re-create empty saved_anchors.json so subsequent /save calls in the same chat
# session can stage fresh anchors without confusion.
with open(staged_path, 'w', encoding='utf-8') as f:
    json.dump([], f, indent=2)

# Update session.json (saved_anchors back to 0; record the commit).
run_dir = os.path.dirname(staged_path)
session_path = os.path.join(run_dir, 'session.json')
session = json.load(open(session_path, encoding='utf-8'))
session['saved_anchors'] = 0
session.setdefault('commits', []).append({
    "ts": ts,
    "count": len(staged),
    "archived_to": os.path.basename(committed_path),
})
stmp = session_path + '.tmp.' + str(os.getpid())
with open(stmp, 'w', encoding='utf-8') as f:
    json.dump(session, f, indent=2, ensure_ascii=False)
os.rename(stmp, session_path)

print(f"OK: {len(staged)} anchor(s) committed; archived to {os.path.basename(committed_path)}")
PY
```

Exit codes:
- `0` — success
- `2` — existing frontmatter unparseable (do not touch the prompt file; surface to user)
- `3` — post-write validation failed (temp removed; prompt file is unchanged)

### Step 7.3 — Surface result

On success, print to user (in `report_language`):

- TR: `<N> anchor frontmatter'a yazıldı. Prompt: <basename>. /prompt-test ile çalıştırabilirsin.`
- EN: `<N> anchors written to frontmatter. Prompt: <basename>. Use /prompt-test to run them.`

On error (exit 2 or 3), print the stderr message verbatim and return to chat loop. The user can investigate the prompt file's existing frontmatter (likely a manual YAML syntax issue) and try again.

### Why staged-then-commit (not instant-write)

Staged saves let the user iterate fast and discard mistakes. Instant writes would mutate the prompt file on every `/save`, invalidating any cached `/prompt-check` audit (the SHA256 stale-audit guard) — and a typo'd anchor would require a frontmatter edit to remove. The two-step flow (`/save` × N → `/commit`) is friction worth one extra command for the safety it buys.

## Phase 8 — `/quit` final

User typed `/quit`. Offer to commit any uncommitted staged anchors, then print the session summary and exit.

### Step 8.1 — Check staged anchors

```bash
STAGED_COUNT=$(python3 -c "import json; print(len(json.load(open('$RUN_DIR/saved_anchors.json'))))")
```

If `STAGED_COUNT > 0`, ask the user:

```
question (TR): "<N> staged anchor var ama henüz commit edilmedi. Frontmatter'a yazayım mı?"
question (EN): "<N> staged anchors are uncommitted. Write them to frontmatter now?"
header:        "Commit on exit"
multiSelect:   false
options:
  - label: "Evet, yaz" | "Yes, commit"
    description: "Run Phase 7 commit before exit. Anchors written to frontmatter.anchors[]."
  - label: "Hayır, sonra commit ederim" | "No, I'll commit later"
    description: "saved_anchors.json kept. Next /prompt-chat run on this prompt can pick it up via --resume (future)."
  - label: "Hepsini sil" | "Discard all"
    description: "Drop staged anchors. saved_anchors.json reset to []."
```

Dispatch:
- **Yes** → execute Phase 7 inline, then proceed to Step 8.2.
- **No** → leave `saved_anchors.json` untouched. Proceed to Step 8.2.
- **Discard** → reset `saved_anchors.json` to `[]` (atomic write). Proceed to Step 8.2.

If `STAGED_COUNT == 0`, skip directly to Step 8.2.

### Step 8.2 — Final summary

Read `session.json` for stats. Compute totals and print (in `report_language`):

**TR:**
```
/prompt-chat oturumu kapatıldı — <chat-NNN>

- Konuşma turu: <N>
- Kaydedilen anchor: <M staged + K committed = total>
- İzolasyon: <yeni pencere | in-session | setup-only>
- Run dir: .promptcheck/<basename>/<chat-NNN>/

Sonraki adım: <one of>
  - /prompt-test <prompt> ile committed anchor'ları çalıştır (M > 0 ise)
  - /prompt-check <prompt> ile audit yap
  - frontmatter'ı manuel düzenle
```

**EN:** same structure, English labels.

If isolation_mode was `in_session`, the main asistan resumes after the skill exits. If `new_window` or `setup_only`, this is the final message in that window (window can be closed manually).

### Step 8.3 — Exit

Skill returns. No further user input is consumed by `/prompt-chat`. Main session control returns to the user.

## Invariants

- **Never read `next_turn.txt`** outside Step 3.2 (after the subagent writes it). Reading it earlier burns tool calls on a non-existent file.
- **Never modify `body.txt`** in any phase. Body is read-only after Phase 0.
- **Never modify the original prompt file outside Phase 7.** All chat-time state is in `chat-NNN/`.
- **Slash commands are case-insensitive.** `/SAVE` works the same as `/save`.
- **Every persisted state update is atomic** (temp + rename) — never partial writes.
- **`chat.jsonl` is append-only.** Never overwrite, never delete mid-session. `/reset` moves the file aside; it does not delete it.
