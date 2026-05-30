---
name: prompt-chat
description: Interactive chat simulator for a prompt file. Loads the prompt as the simulated system prompt, lets you converse with its persona via text, and lets you save interesting turns as test anchors. Use when the user runs /prompt-chat, asks to "chat with this prompt", "test the prompt by talking to it", or wants to iterate on a voice-agent prompt without calling Vapi. Produces chat.jsonl + optional anchors written to <prompt>.anchors.yaml (a sidecar file alongside the prompt). The prompt file itself is never touched.
---

# prompt-chat

You give a prompt file `$1`. The skill bootstraps state, binds any prompt variables, then spawns `bin/prompt-chat-runner.py` in a new terminal window. The runner loads the prompt as the simulated system prompt of a long-lived bare-Claude subprocess and owns the turn-by-turn conversation, letting you save turns as test anchors that `/prompt-test` can later replay.

`/prompt-chat` is an exploratory tool. It is intentionally separate from `/prompt-check` (audit) and `/prompt-test` (regression) — different mental model, different state, different artefacts. The bridge between them is `<prompt>.anchors.yaml` (the sidecar file — single source of truth for test scenarios; v0.5.1 moved this out of the prompt's frontmatter).

## Inputs you have

- `$1` — relative or absolute path to the prompt file under chat.
- `bin/prompt-chat-runner.py` — the Python orchestrator that owns the chat loop (spawned in a new window by Phase 1). v0.5.6 replaced the per-turn `chat-simulator` subagent with a single long-lived bare-Claude subprocess.

## Phase 0+1 — Bootstrap, variable binding & dispatch (v0.6.0: two bash blocks)

**IMPORTANT: execute each fenced bash block below as ONE Bash tool call.** v0.5.18 consolidated bootstrap+dispatch into a single block (one permission prompt). v0.6.0 splits it into **two** blocks because variable binding needs an `AskUserQuestion` in the middle (a main-session tool call cannot run inside a Bash subshell): **Phase 0** (bootstrap + variable detection) → `AskUserQuestion` for any unbound variables → **Phase 1** (persist bindings + pre-flight + dispatch). Two permission prompts, not one — the extra prompt is the cost of pre-chat variable binding. Each block is still self-contained; do not split a block further.

The flow:

1. **Phase 0** — read `$1`, parse frontmatter, allocate `.voicepromptkit/<basename>/chat-NNN/`, write `frontmatter.json` / `body.txt` / empty `chat.jsonl` / empty `saved_anchors.json` / initial `session.json`; then **detect variables** (`{{...}}` + `[BÜYÜK_HARF]`), merge known values from the `<prompt>.vars.yaml` sidecar + `chat_variables` frontmatter seed, and emit the `UNBOUND_JSON` set.
2. **Phase 0.5** — for each unbound variable, `AskUserQuestion` for a value (or leave random). Assemble the final `{name: value}` map.
3. **Phase 1** — persist the bindings (`variables.json` in the run dir + `<prompt>.vars.yaml` sidecar), detect platform + window-spawn capability + Python interpreter, resolve the runner script (dev-repo `bin/` first, plugin cache fallback), spawn the orchestrator in a fresh Terminal / tmux / Windows Terminal session, then exit this skill. The chat session always opens in a new window (v0.5.11 simplification — no in-session / setup-only alternatives).

v0.5.7 — spawn the Python orchestrator DIRECTLY in the new Terminal/tmux window. Do NOT route through `claude '/prompt-chat-session ...'` because Claude Code's skill runtime wraps Python's REPL in a Bash tool call whose subshell has no interactive TTY — Python `input()` immediately hits EOF and the chat exits with 0 turns. Going directly to the Python script gives the runner the real terminal it needs.

```bash
# v0.5.18 consolidated bootstrap + dispatch. Variables defined early stay in
# scope through the rest of the block; abort early via `exit 1` on any fatal
# precondition (missing Python, no window-spawn helper, missing runner).

# ---- 0.1: paths + atomic chat-NNN allocation -------------------------------
ABS_PROMPT=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
BASENAME=$(basename "$1" | sed 's/\.[^.]*$//')
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PROMPT_DIR="$REPO_ROOT/.voicepromptkit/$BASENAME"
mkdir -p "$PROMPT_DIR"

# mkdir without -p fails if the dir exists, so a concurrent run claiming the
# same number loses cleanly and we retry up to 100 times.
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

# ---- 0.2: frontmatter + body parse (identical to /prompt-check Phase 2) ---
python3 - "$ABS_PROMPT" "$RUN_DIR" "$REPO_ROOT" <<'PY'
import sys, re, json, os, hashlib, subprocess
prompt_path, run_dir, repo_root = sys.argv[1], sys.argv[2], sys.argv[3]
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

# v0.5.1: count sidecar+frontmatter anchors so session metadata reflects what
# the rest of the pipeline uses as the single source of truth.
existing_anchors_count = 0
anchors_source = 'none'
try:
    _r = subprocess.run(
        [sys.executable, os.path.join(repo_root, 'bin', 'read-anchors.py'), prompt_path],
        capture_output=True, text=True, timeout=30,
    )
    if _r.returncode == 0:
        _p = json.loads(_r.stdout)
        existing_anchors_count = len(_p.get('anchors', []))
        anchors_source = _p.get('source', 'none')
except Exception:
    pass

def _model_alias(full_or_alias, default):
    if not full_or_alias:
        return default
    s = str(full_or_alias).strip().lower()
    if s in ('sonnet', 'opus', 'haiku'):
        return s
    if 'haiku' in s: return 'haiku'
    if 'sonnet' in s: return 'sonnet'
    if 'opus' in s: return 'opus'
    return default

# v0.5.4: chat_model defaults to Haiku — persona exploration during /prompt-chat
# is rapid-iteration; cost & latency beat frontier-model quality per turn.
# Override in frontmatter for tricky persona testing where fidelity matters.
resolved = {
    'target_model':     fm.get('target_model') or 'claude-opus-4-7',
    'chat_model':       fm.get('chat_model') or 'claude-haiku-4-5-20251001',
    'report_language':  (fm.get('report_language') or 'tr').lower(),
    'body_char_count':  len(body),
    'body_line_offset': body_line_offset,
    'prompt_sha256':    prompt_sha256,
    'existing_anchors_count': existing_anchors_count,
    'anchors_source':   anchors_source,
}
resolved['target_model_alias'] = _model_alias(resolved['target_model'], 'opus')
resolved['chat_model_alias']   = _model_alias(resolved['chat_model'], 'haiku')
if resolved['report_language'] not in ('tr', 'en'):
    resolved['report_language'] = 'tr'

with open(os.path.join(run_dir, 'frontmatter.json'), 'w', encoding='utf-8') as f:
    json.dump(resolved, f, indent=2, ensure_ascii=False)
with open(os.path.join(run_dir, 'body.txt'), 'w', encoding='utf-8') as f:
    f.write(body)
PY

# ---- 0.3: empty state files ----------------------------------------------
: > "$RUN_DIR/chat.jsonl"
printf '[]' > "$RUN_DIR/saved_anchors.json"

# v0.5.18: bake isolation_mode="new_window" into session.json at creation
# time. The pre-v0.5.18 skill wrote None here and then patched the field
# again after dispatch — separate bash block, extra permission prompt.
# Since dispatch is now unconditional, the value is known up front.
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
    "chat_model": fm.get('chat_model'),
    "report_language": fm['report_language'],
    "turns": 0,
    "saved_anchors": 0,
    "isolation_mode": "new_window",       # always: dispatch is unconditional
    "chat_session_uuid": None,            # v0.5.6: filled by runner on first turn
}
with open(os.path.join(run_dir, 'session.json'), 'w', encoding='utf-8') as f:
    json.dump(session, f, indent=2, ensure_ascii=False)
PY

# ---- 0.4: variable detection (v0.6.0) ------------------------------------
# Scan body for Vapi {{...}} + bracketed [BÜYÜK_HARF] tokens, merge any
# already-known values (sidecar <prompt>.vars.yaml wins over a frontmatter
# `chat_variables` seed), and emit the UNBOUND set so the skill can ask the
# user for values BEFORE the window opens. Writes detected.json (full
# detected list + known values) and prints two stdout lines the skill
# parses: DETECTED_JSON=... and UNBOUND_JSON=...
python3 - "$ABS_PROMPT" "$RUN_DIR" <<'PY'
import sys, re, json, os
prompt_path, run_dir = sys.argv[1], sys.argv[2]
body = open(os.path.join(run_dir, 'body.txt'), encoding='utf-8').read()

MUSTACHE = re.compile(r'\{\{\s*([\w.]+)\s*\}\}')
BRACKET  = re.compile(r'\[([A-ZÇĞİÖŞÜ_][A-ZÇĞİÖŞÜ0-9_]*)\]')
DENY = {'SYSTEM', 'PERSONA_NAME', 'END_CALL'}

hits = []
for m in MUSTACHE.finditer(body):
    hits.append((m.start(), m.group(0), m.group(1).strip()))
for m in BRACKET.finditer(body):
    name = m.group(1).strip()
    if name not in DENY:
        hits.append((m.start(), m.group(0), name))
hits.sort(key=lambda h: h[0])
seen, detected = set(), []
for _p, token, name in hits:
    if token not in seen:
        seen.add(token)
        detected.append({'name': name, 'token': token})

# Known values: frontmatter chat_variables seed (read-only) < sidecar.
known = {}
try:
    import yaml
    text = open(prompt_path, encoding='utf-8').read()
    fmm = re.match(r'^---\r?\n(.*?)\r?\n---\r?\n?', text, re.DOTALL)
    if fmm:
        cv = (yaml.safe_load(fmm.group(1)) or {}).get('chat_variables') or {}
        if isinstance(cv, dict):
            known.update({str(k): str(v) for k, v in cv.items()})
    sidecar = prompt_path + '.vars.yaml'
    if os.path.exists(sidecar):
        sc = yaml.safe_load(open(sidecar, encoding='utf-8')) or {}
        if sc.get('schema_version') == 1 and isinstance(sc.get('variables'), dict):
            known.update({str(k): str(v) for k, v in sc['variables'].items()})
except Exception:
    pass

names = [d['name'] for d in detected]
values = {n: known[n] for n in names if known.get(n)}
unbound = [n for n in names if n not in values]

with open(os.path.join(run_dir, 'detected.json'), 'w', encoding='utf-8') as f:
    json.dump({'detected': detected, 'values': values}, f, ensure_ascii=False, indent=2)
print('DETECTED_JSON=' + json.dumps(detected, ensure_ascii=False))
print('UNBOUND_JSON=' + json.dumps(unbound, ensure_ascii=False))
PY
```

### Phase 0.5 — Pre-chat variable binding (v0.6.0)

After the Phase 0 block returns, read its `UNBOUND_JSON` line.

- **If `UNBOUND_JSON` is `[]`** (no variables, or all already bound in the sidecar / frontmatter seed): skip the questions — go straight to the Phase 1 block with `BOUND_VALUES_JSON='{}'`.
- **If it has entries:** for each unbound variable, call `AskUserQuestion` (batch up to 4 variables per call) to collect a value. Per variable:
  - `header`: the variable name (≤12 chars; truncate if longer).
  - `question` (TR): `"<name> için ne atayayım?"` · (EN): `"What value for <name>?"`.
  - `options` (2–4): infer 1–2 **plausible sample values from the name** (a name token → a Turkish full name; a date token → a near-future date; a phone token → a TR mobile number; an account/id token → a plausible id), each as a concrete-value option; plus a final `"(rastgele / model uydursun)"` · `"(random / let the model invent)"` option. The user can always type a custom value via the auto-provided **Other**.
  - Map the answer: the **random** option → leave the name OUT of the final values (the persona invents it per framing rule 10); any concrete value (sample or Other) → record `name: value`.

Assemble the final `{name: value}` object = (values already known from sidecar/frontmatter) merged with the newly answered ones. Substitute it for `BOUND_VALUES_JSON` in the Phase 1 block below. Prefer double-quoted JSON values.

```bash
# v0.6.0 Phase 1 — persist bindings, then pre-flight + dispatch.
# Replace the placeholder with the final {name: value} JSON you assembled
# (e.g. {"randevu_tarihi":"12 Haziran","MÜŞTERİ_ADI":"Zeynep"}); use {} when
# there were no variables or the user left everything random.
BOUND_VALUES_JSON='{}'

# ---- 1.0: write variables.json (run-dir) + <prompt>.vars.yaml (sidecar) --
# variables.json is this session's resolved set (read by the runner). The
# sidecar persists values prompt-level across chats. The prompt file itself
# is NEVER touched → its SHA256 stays stable, cached /prompt-check audits
# remain valid (same contract as <prompt>.anchors.yaml).
python3 - "$RUN_DIR" "$ABS_PROMPT" "$BOUND_VALUES_JSON" <<'PY'
import sys, json, os
run_dir, prompt_path, bound_json = sys.argv[1], sys.argv[2], sys.argv[3]
detected = json.load(open(os.path.join(run_dir, 'detected.json'), encoding='utf-8')).get('detected', [])
try:
    bound = json.loads(bound_json) if bound_json.strip() else {}
    bound = bound if isinstance(bound, dict) else {}
except Exception:
    bound = {}
values = {str(k): str(v) for k, v in bound.items() if str(v) != ''}

with open(os.path.join(run_dir, 'variables.json'), 'w', encoding='utf-8') as f:
    json.dump({'detected': detected, 'values': values}, f, ensure_ascii=False, indent=2)

if values:
    try:
        import yaml
        sidecar = prompt_path + '.vars.yaml'
        existing = {}
        if os.path.exists(sidecar):
            sc = yaml.safe_load(open(sidecar, encoding='utf-8')) or {}
            if sc.get('schema_version') == 1 and isinstance(sc.get('variables'), dict):
                existing = {str(k): str(v) for k, v in sc['variables'].items()}
        doc = {'schema_version': 1, 'variables': {**existing, **values}}
        tmp = sidecar + '.tmp.' + str(os.getpid())
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False))
        chk = yaml.safe_load(open(tmp, encoding='utf-8')) or {}
        if chk.get('schema_version') == 1 and isinstance(chk.get('variables'), dict):
            os.rename(tmp, sidecar)
        else:
            os.remove(tmp)
    except Exception as e:
        print(f'WARN: vars sidecar not written: {e}', file=sys.stderr)
print('OK variables persisted')
PY

# ---- 1.1: pre-flight — platform / python / window-spawn capability --------
# Supported window-spawn helpers per platform:
#   macOS:   osascript (Terminal.app, bundled with macOS)
#   Linux:   tmux | gnome-terminal | xterm | konsole
#   Windows: cmd.exe `start` (built-in) or Windows Terminal (wt.exe)
PLATFORM=$(uname 2>/dev/null || echo Windows)
CLAUDE_CLI=$(command -v claude 2>/dev/null || true)
NEW_WINDOW_OK=false

if [ -n "$CLAUDE_CLI" ]; then
  case "$PLATFORM" in
    Darwin)
      command -v osascript >/dev/null && NEW_WINDOW_OK=true ;;
    Linux)
      if command -v tmux >/dev/null || command -v gnome-terminal >/dev/null \
         || command -v xterm >/dev/null || command -v konsole >/dev/null; then
        NEW_WINDOW_OK=true
      fi ;;
    MINGW*|MSYS*|CYGWIN*|Windows*)
      if command -v cmd.exe >/dev/null || command -v wt.exe >/dev/null \
         || command -v start >/dev/null; then
        NEW_WINDOW_OK=true
      fi ;;
  esac
fi

PYTHON_CLI=""
for p in python3 python py; do
  if command -v "$p" >/dev/null 2>&1; then PYTHON_CLI="$p"; break; fi
done
if [ -z "$PYTHON_CLI" ]; then
  echo "ERROR: no Python interpreter found on PATH (tried python3 / python / py)."
  echo "Install Python 3.8+ and rerun /prompt-chat."
  exit 1
fi

if [ "$NEW_WINDOW_OK" != "true" ]; then
  echo "ERROR: /prompt-chat requires a way to open a new terminal window."
  echo "Install one of:"
  echo "  - macOS:   (Terminal.app + osascript — bundled, should be auto)"
  echo "  - Linux:   tmux, gnome-terminal, xterm, or konsole"
  echo "  - Windows: cmd.exe (built-in) or Windows Terminal (wt.exe)"
  echo "You can still launch manually: $PYTHON_CLI <runner-path> $RUN_DIR"
  exit 1
fi

# ---- 1.2: resolve the orchestrator script --------------------------------
# Dev repo: bin/prompt-chat-runner.py. Installed plugin:
# ~/.claude/plugins/cache/onurgoz-labs/VoicePromptKit/<version>/bin/.
#
# v0.5.19: cache fallback now version-sorts. Pre-v0.5.19 the `for guess in
# <glob>` loop relied on bash's alphabetic glob expansion, which made
# "0.5.11" sort BEFORE "0.5.18" (and "0.5.19") — so the oldest cached
# version always won. `ls ... | sort -V | tail -1` picks the highest
# semver instead. Dev override env var VOICEPROMPTKIT_RUNNER short-
# circuits both lookups so a contributor can point at an in-tree file
# regardless of cwd.
RUNNER=""
if [ -n "$VOICEPROMPTKIT_RUNNER" ] && [ -f "$VOICEPROMPTKIT_RUNNER" ]; then
  RUNNER="$VOICEPROMPTKIT_RUNNER"
elif [ -f "$REPO_ROOT/bin/prompt-chat-runner.py" ]; then
  RUNNER="$REPO_ROOT/bin/prompt-chat-runner.py"
else
  RUNNER=$(ls -d "$HOME/.claude/plugins/cache/onurgoz-labs/VoicePromptKit/"*/bin/prompt-chat-runner.py 2>/dev/null \
    | sort -V | tail -1)
fi

if [ -z "$RUNNER" ] || [ ! -f "$RUNNER" ]; then
  echo "ERROR: bin/prompt-chat-runner.py not found in repo or plugin cache."
  echo "Reinstall VoicePromptKit (the runner script is part of the plugin distribution)."
  exit 1
fi

# ---- 1.3: dispatch to a new terminal -------------------------------------
case "$PLATFORM" in
  Darwin)
    # v0.5.23: when Terminal.app is not running, plain `do script ...`
    # opens TWO windows — first the default empty window from
    # Terminal's launch, then a second one with the script. Detect
    # that case and target the freshly-opened default window with
    # `in front window` so only one window appears.
    osascript >/dev/null <<OSA
tell application "Terminal"
    set wasRunning to running
    activate
    if not wasRunning then
        delay 0.5
        do script "$PYTHON_CLI '$RUNNER' '$RUN_DIR'" in front window
    else
        do script "$PYTHON_CLI '$RUNNER' '$RUN_DIR'"
    end if
end tell
OSA
    ;;
  Linux)
    if command -v tmux >/dev/null; then
      tmux new-window -n "chat-$BASENAME" "$PYTHON_CLI '$RUNNER' '$RUN_DIR'"
    elif command -v gnome-terminal >/dev/null; then
      gnome-terminal -- "$PYTHON_CLI" "$RUNNER" "$RUN_DIR"
    elif command -v xterm >/dev/null; then
      xterm -e "$PYTHON_CLI '$RUNNER' '$RUN_DIR'" &
    elif command -v konsole >/dev/null; then
      konsole -e "$PYTHON_CLI '$RUNNER' '$RUN_DIR'" &
    fi
    ;;
  MINGW*|MSYS*|CYGWIN*|Windows*)
    # Prefer Windows Terminal (modern UX), fall back to cmd's `start`.
    if command -v wt.exe >/dev/null; then
      wt.exe new-tab --title "chat-$BASENAME" "$PYTHON_CLI" "$RUNNER" "$RUN_DIR" &
    else
      # `start` opens a new console. /B would suppress the window; we want
      # one so we omit /B. The "" is the window-title arg `start` consumes
      # when the first positional is quoted.
      cmd.exe /c start "" "$PYTHON_CLI" "$RUNNER" "$RUN_DIR" &
    fi
    ;;
esac

# Echo enough for the main session to greet the user with the relative path.
RUN_DIR_REL=$(python3 -c "import os, sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))" "$RUN_DIR" "$REPO_ROOT")
echo "OK chat session launched in new window"
echo "RUN_DIR_REL=$RUN_DIR_REL"
```

After the bash block returns, print to the main session:

```
Chat oturumu yeni pencerede başlatıldı.
Run dir: <value of RUN_DIR_REL from the bash output>
Yeni pencere kapandıktan sonra `cat <run-dir>/chat.jsonl` ile konuşmayı inceleyebilirsin.
```

**Then exit this skill.** Main session is freed; the entire chat happens in the new window.

### `/prompt-chat-session` skill (manual resume only, v0.5.11+)

`skills/prompt-chat-session/SKILL.md` is the manual entry path — invoke it via `claude '/prompt-chat-session <run-dir>'` from a real Terminal prompt (NOT from inside another Claude Code session, where the Bash tool subshell has no interactive TTY and the chat would exit immediately). The skill validates the run dir, then execs `bin/prompt-chat-runner.py`. The primary path in v0.5.11 — `/prompt-chat` with the auto-spawned new window — does NOT route through this skill; it invokes the Python script directly.

## Phase 2 — Welcome screen

Render in `report_language`:

**TR:**
```
/prompt-chat başlatıldı.
Prompt: <basename> (<line count> satır, model: <target_model>)
İzolasyon: yeni pencere (bare Claude session)

Yaz, ben prompt'a göre cevap vereyim.

Komutlar:
  /save           — son turu anchor olarak kaydet (staging)
  /history        — bu oturumdaki turları göster
  /reset          — geçmişi sil, baştan başla
  /vars           — tespit edilen değişkenleri + değerlerini göster
  /set ad=değer   — değişkeni anlık değiştir (sonraki turda uygulanır)
  /unset ad       — değişken bağını kaldır (model uydurur)
  /commit         — staged anchor'ları sidecar'a (<prompt>.anchors.yaml) yaz
  /quit           — çıkış + final summary
```

**EN:** same content, English labels.

After printing the welcome, the runner waits for the user's first message.

## Phase 3 — Chat loop (per user turn)

> **Implemented in `bin/prompt-chat-runner.py`, not executed by this skill.** After Phase 1 dispatches the runner into a new window, this skill has already exited — the runner owns every subsequent turn. This section (and Phases 4–8) documents the runner's behavior; the canonical implementation is the Python script (see Phase 6).

Per user turn the runner:

1. **Slash command?** If the message starts with `/`, it dispatches to the matching handler (Phase 4); slash commands are control and are never written to `chat.jsonl`.
2. **Otherwise** it appends the user message to `chat.jsonl`, sends it to the long-lived bare-Claude subprocess, streams the persona's reply, appends the reply to `chat.jsonl`, bumps `session.json.turns`, and renders the reply followed by the compact footer (`― [tur N · …]`, language per `report_language`). Then it waits for the next turn.

**Loop invariant:** one user message → one subprocess exchange → one assistant reply → wait. No batching, no look-ahead.

## Phase 4 — Slash command dispatch

User typed something starting with `/`. Parse the command (lowercase, strip leading slash):

| Command | Action |
|---|---|
| `save` | Phase 5 — anchor save sub-flow (filled in Phase C of the implementation) |
| `history` | Pretty-print `chat.jsonl` (turn N · role · first 80 chars), then return to chat loop |
| `reset` | Move `chat.jsonl` to `chat-N-discarded.jsonl`, create fresh empty `chat.jsonl`, update `session.json.turns = 0`, AND regenerate `session.json.chat_session_uuid` (v0.5.6 — the next user message spawns a new bare Claude subprocess with a fresh session id, body reloaded). v0.6.0: re-substitutes current variable values into the wrapped body before the respawn. Inform user. saved_anchors.json is NOT touched. |
| `vars` | v0.6.0 — list detected variables with their current bound value (or `(rastgele)` when unbound). Read-only; returns to the chat loop. |
| `set <name>=<value>` | v0.6.0 — rebind a variable mid-chat. Updates `variables.json` + the `<prompt>.vars.yaml` sidecar and queues a `[SYSTEM: variable update]` cue applied on the next user turn (no respawn → history preserved). |
| `unset <name>` | v0.6.0 — drop a binding so the persona invents the value again (framing rule 10); persists the removal + queues the cue. |
| `commit` | Phase 7 — sidecar write (<prompt>.anchors.yaml) |
| `quit` | Phase 8 — final summary + optional commit prompt (filled in Phase C) |
| anything else | Print "Bilinmeyen komut: `/<x>`. Mevcut: /save /history /reset /vars /set /unset /commit /quit" and return |

These commands are implemented in the Python runner (`bin/prompt-chat-runner.py`, v0.5.6+): `/save` → Phase 5, `/commit` → Phase 7, `/quit` → Phase 8 (with optional commit prompt for staged anchors); `/history`, `/reset`, `/vars`, `/set`, `/unset` execute inline as state operations.

## Phase 5 — Anchor save sub-flow

User typed `/save`. Capture either (a) the most recent user → assistant exchange as a single-turn anchor, OR (b) the entire chat conversation as a flow anchor. The user picks the kind via AskUserQuestion; the per-kind sub-flows differ but both end with staging in `saved_anchors.json` (never touching the sidecar — sidecar write only happens in Phase 7 / `/commit`).

> **Implementation note (v0.7.0).** The canonical Python runner (`bin/prompt-chat-runner.py`, `_handle_save`) currently stages **single-turn anchors only** — it captures the last user → assistant exchange and prompts for `expect_contains` / `expect_not_contains` / `rubric`. The flow-anchor sub-flow described below (Steps 5.0 / 5.F*) is the planned design; until it lands, author multi-turn flow anchors by hand in `<prompt>.anchors.yaml`.

### Step 5.0 — Anchor kind selector (v0.5.1)

Before collecting assertions, ask the user which kind of anchor they want to save:

```
question (TR): "Hangi tür anchor kaydedelim?"
question (EN): "Which anchor kind to save?"
header:        "Anchor kind"
multiSelect:   false
options:
  - label: "Tek tur (son user → assistant)" | "Single turn (last user → assistant)"
    description: "Mevcut + opsiyonel context (v0.5.0 davranışı; sade test)"
  - label: "Akış (tüm konuşma)" | "Flow (entire conversation)"
    description: "chat.jsonl'in tamamını multi-turn anchor olarak kaydet — greeting, sessizlik, kapanış dahil"
```

Dispatch:
- **Single turn** → continue with Steps 5.1 → 5.7 (existing v0.5.0 flow, single-turn schema)
- **Flow** → jump to Step 5.F1 (flow capture sub-flow, v0.5.1)

### Step 5.1 — Locate the last turn (single-turn path)

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
    description: "Stage this anchor in saved_anchors.json. /commit will write it to the sidecar later."
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

- TR: `Anchor #<N> kaydedildi (staging). Toplam: <N> staged. /commit ile sidecar'a yazılır.`
- EN: `Anchor #<N> staged. Total: <N>. Use /commit to write them to the sidecar.`

Return to chat loop.

### Step 5.F1 — Flow capture sub-flow (v0.5.1)

The user picked "Flow" in Step 5.0. Convert the entire `chat.jsonl` into a flow anchor — every user entry becomes a `user_input` (or `silence_input` if it matches the silence pattern); every assistant entry becomes an `assistant_expect` whose assertions the user fills in turn by turn.

If `chat.jsonl` is empty or has zero assistant turns, surface:
- TR: `Konuşma çok kısa — flow anchor için en az bir user→assistant turu gerekli.`
- EN: `Conversation too short — flow anchor needs at least one user→assistant turn.`
…and return to chat loop.

**Step 5.F2 — Anchor name.** Free-form AskUserQuestion (single-line input):
- TR: `Bu flow için bir ad ver (ör. "happy path booking", boş bırakılabilir):`
- EN: `Give this flow a name (e.g. "happy path booking", may be empty):`

Trim whitespace. Empty → `None` (the reader/runner will fall back to the first user_input's content).

**Step 5.F3 — Read and pre-process chat.jsonl.**

```bash
python3 - "$RUN_DIR" <<'PY'
import json, os, re, sys
run_dir = sys.argv[1]

SILENCE_PATTERN = re.compile(r'^\[silence\s+for\s+(\d+)\s+seconds?\]\s*$', re.IGNORECASE)

entries = []
with open(os.path.join(run_dir, 'chat.jsonl'), encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

turns = []
for e in entries:
    role = e.get('role')
    content = e.get('content', '')
    if role == 'user':
        m = SILENCE_PATTERN.match(content)
        if m:
            turns.append({'kind': 'silence_input', 'duration_seconds': int(m.group(1))})
        else:
            turns.append({'kind': 'user_input', 'content': content})
    elif role == 'assistant':
        # placeholder — user fills assertions per turn in Step 5.F4
        turns.append({
            'kind': 'assistant_expect',
            '_actual': content,  # transient; stripped before staging
        })

# Write transient turn skeleton to a scratch file the skill iterates over.
with open(os.path.join(run_dir, '_flow_capture.json'), 'w', encoding='utf-8') as f:
    json.dump({'turns': turns}, f, indent=2, ensure_ascii=False)

print(json.dumps({
    'turn_count': len(turns),
    'assistant_turn_count': sum(1 for t in turns if t['kind'] == 'assistant_expect'),
    'silence_count': sum(1 for t in turns if t['kind'] == 'silence_input'),
}, ensure_ascii=False))
PY
```

Surface a one-line summary:
- TR: `<N> tur (kullanıcı: <U>, asistan: <A>, sessizlik: <S>). Şimdi her asistan turu için beklenen davranışı sorayım.`
- EN: `<N> turns (user: <U>, assistant: <A>, silences: <S>). Now asking expected behaviour per assistant turn.`

**Step 5.F4 — Per-turn assistant_expect questions.** For each `assistant_expect` turn in `_flow_capture.json.turns[]`, in chat order, ask three AskUserQuestion prompts using the same free-form text shape from Steps 5.2–5.4. Render a preview before the questions:

```
Asistan turu #<k> (toplam <A>):
> <_actual content of that turn, truncated to 200 chars>

(User filled fields go here)
```

Then ask:
1. `expect_contains` — TR: `Bu turda hangi sözcükler / ifadeler bulunmalı? (virgülle ayır, boş bırakılabilir)` / EN: same as Step 5.2.
2. `expect_not_contains` — same as Step 5.3.
3. `rubric` — TR: `Bu tur için rubrik (opsiyonel, ≤ 200 karakter)` / EN: same as Step 5.4.

**Skip option per turn.** Add a 4th question after the three above:
```
question: "Bu turu test etmek istiyor musun, yoksa atlayayım mı?"
header:   "Per-turn skip"
options:
  - label: "Test et" | "Test it"          (Recommended when at least one assertion was filled)
  - label: "Atla — sadece kayıt için" | "Skip — log only"
```

If "Skip", drop the assertions; the turn becomes a `user_input` follow-on instead of an `assistant_expect` (the assistant turn is still part of the conversation but unverified). Practical: if the user only cares about the closing turn, they can skip middle turns.

**Step 5.F5 — Optional `end_call_expect` terminal step.**

```
question (TR): "Bu akış bir kapanış (end_call) ile bitiyor mu?"
question (EN): "Does this flow end with a call closure?"
header:        "End call"
multiSelect:   false
options:
  - label: "Evet — kapanış turu ekle" | "Yes — append end_call_expect"
    description: "Son asistan turunu end_call_expect olarak işaretle; örtük 'session closed' rubric'i uygulanır."
  - label: "Hayır" | "No"
    description: "Akış mevcut son turla biter (varsa assistant_expect; yoksa user_input)."
```

If "Yes": find the LAST `assistant_expect` in `turns[]` (after Step 5.F4 finalisation) and change its `kind` to `end_call_expect`. Also ask one more rubric question specific to the closing (default empty, the implicit "session closed" rubric still applies).

**Step 5.F6 — Preview and confirm.**

Render the staged flow anchor with all turns + per-turn assertions + optional end_call rubric. Use AskUserQuestion `Confirm` (same 3 options as Step 5.6: Yes / Edit / Cancel). On Edit, jump back to Step 5.F4 (re-run per-turn questions). On Cancel, no-op.

**Step 5.F7 — Stage the flow anchor.**

Strip transient `_actual` fields from each turn. Atomic append to `saved_anchors.json` (same pattern as Step 5.7). Remove `_flow_capture.json`. Update `session.json.saved_anchors`.

The staged anchor shape mirrors the sidecar schema verbatim:

```yaml
- kind: flow
  name: "<from Step 5.F2 or null>"
  turns:
    - kind: user_input
      content: "Merhaba"
    - kind: assistant_expect
      expect_contains: [...]    # only present if user filled
      expect_not_contains: [...]
      rubric: "..."
    - kind: silence_input       # if matched
      duration_seconds: 6
    - kind: assistant_expect
      rubric: "..."
    - kind: end_call_expect     # if user picked Yes in Step 5.F5
      rubric: "..."
```

Surface to user:
- TR: `Flow anchor #<N> kaydedildi (staging — <T> tur, <A> assistant_expect). /commit ile sidecar'a yazılır.`
- EN: `Flow anchor #<N> staged (<T> turns, <A> assistant_expects). Use /commit to write to sidecar.`

Return to chat loop.

## Phase 6 — Chat loop runtime (v0.5.6 — Python orchestrator + bare claude subprocess)

**v0.5.4's persistent-subagent approach failed** because Claude Code's Agent-tool `SendMessage` mechanism triggers transcript replay — every turn re-processes the body + chat.jsonl, costing ~32k tokens / ~50s per turn. v0.5.5's projected improvement (~50%, skill-as-persona) fell short of the target because the skill's own SKILL.md + tool definitions + CLAUDE.md overhead adds an unavoidable baseline.

**v0.5.6 architecture pivot:** move the chat loop entirely out of the Claude Code skill / subagent world. Instead, `bin/prompt-chat-runner.py` (pure Python, stdlib + PyYAML) runs this way:

1. The Python script is spawned ONCE (via the `/prompt-chat-session` skill's `exec python3 ...` call).
2. The script in turn spawns ONE `claude` subprocess — `--input-format stream-json --output-format stream-json --include-partial-messages --system-prompt-file <body> --session-id <uuid> --disable-slash-commands --allowedTools "" --permission-mode bypassPermissions`. The subprocess cwd is `/tmp` (neutral) — the project CLAUDE.md is not auto-discovered.
3. For each user turn the Python script writes a single `{"type":"user","message":{...}}` JSON line to stdin, parses events from stdout, streams `text_delta` events to the user in real time (lower TTFT), and completes on the `result` event.
4. The subprocess stays alive for the whole conversation — NO re-spawn per turn, NO permission prompt, NO body re-read.

**Per-turn token / latency (measured on a short 5-line test body):**

| Metric | v0.5.4 (broken) | v0.5.6 (this) |
|---|---|---|
| Turn cost (cache hit) | ~32k | **~3-5k** (input 9 + cache_read body) |
| Turn latency | ~50s | **~3-5s** (Haiku, streaming output) |
| Subprocess per turn | New Agent + SendMessage | Same long-lived process |
| Permission prompts per turn | Varies (Bash tool permissions) | None (--permission-mode bypassPermissions) |
| Body load count | Every turn (transcript replay) | Once (subprocess startup) |

**Skill-level dispatch does NOT happen here.** The skill is limited to Phase 0 (bootstrap) and Phase 1 (run-mode selection). The "Open new window" mode starts a new Terminal via `claude '/prompt-chat-session <run-dir>'`; that skill immediately hands off to Python with `exec python3 bin/prompt-chat-runner.py <run-dir>`. From that point the chat flow lives entirely in the Python orchestrator:

- **Welcome screen** — Python `_print_welcome()`.
- **Slash command handling** — Python `_dispatch_slash()`. /save / /history / /reset / /commit / /quit are all Python-side, doing file-system mutations + `input()` prompts (an AskUserQuestion-like flow). The Phase 5 / 7 / 8 prose stays in this file, but **the canonical implementation is the Python script** — this spec text documents the Python code's behaviour.
- **Non-slash user turn** — Python `_SubprocessCtx.send()` writes a JSON line to the claude subprocess, streams `text_delta` events to stdout, and on the `result` event atomically appends the reply to chat.jsonl and increments session.turns.

### What this changes for downstream phases

- **session.json schema:** the `chat_simulator_agent_id` field was removed. It is replaced by `chat_session_uuid` — the id of the conversation the claude CLI subprocess started via `--session-id`. Phase 0 bootstrap writes this field as null; the Python script generates a UUID on the first turn and persists it; /reset replaces it with a new UUID (the old subprocess is killed and a fresh one is spawned).
- **`agents/chat-simulator.md` removed.** The per-turn subagent is gone (deprecated v0.5.6, deleted in v0.6.0); the long-lived bare-Claude subprocess in `bin/prompt-chat-runner.py` replaced it.
- **`/prompt-test` and `drift-runner` are unaffected.** Drift simulation still runs with target_model (Opus) — chat exploration and regression simulation use different models and different runtimes.

### Failure modes for Phase 6 runtime

Python orchestrator handles these (see bin/prompt-chat-runner.py docstring + RuntimeError paths):

- **claude subprocess dies mid-chat** — Python catches the broken pipe / non-zero exit, prints `[chat error: ...]` to stderr, REPL loop continues. User can /quit and re-spawn.
- **subprocess timeout** (>180s per turn) — Python aborts that turn with timeout error; user can re-send.
- **stream-json parse error** on a line — silently skipped (defensive); only `type:"result"` events are required for turn completion.
- **/reset mid-chat** — old subprocess closed gracefully, new one spawned with fresh session_uuid. Single-turn latency spike (body re-load) but persona is clean.

## Phase 7 — Frontmatter commit (`/commit`)

User typed `/commit`. Atomic-write the staged anchors from `saved_anchors.json` into `<prompt>.anchors.yaml` (the sidecar). The prompt file is NEVER touched in v0.5.1 — neither body nor frontmatter. The sidecar lives alongside the prompt; its content is `{schema_version: 1, anchors: [...]}`.

### Step 7.1 — Load staged anchors

```bash
STAGED_COUNT=$(python3 -c "import json; print(len(json.load(open('$RUN_DIR/saved_anchors.json'))))")
```

If `STAGED_COUNT == 0`:

- TR: `Staged anchor yok. Önce /save kullan.`
- EN: `No staged anchors. Use /save first.`

…and return to chat loop.

### Step 7.2 — Atomic sidecar write

The write is in three stages: build the new sidecar content, write to a temp file, validate the temp by parsing it back, then atomically rename. Rollback on any failure. **The prompt file itself is never touched** — only `<prompt>.anchors.yaml` is created or modified, so the prompt SHA256 stays stable and any cached `/prompt-check` audits remain valid.

```bash
python3 - "$ABS_PROMPT" "$RUN_DIR/saved_anchors.json" <<'PY'
import sys, os, json, yaml, datetime, shutil

prompt_path, staged_path = sys.argv[1], sys.argv[2]
staged = json.load(open(staged_path, encoding='utf-8'))
if not staged:
    print("WARN: nothing to commit", file=sys.stderr)
    sys.exit(0)

sidecar_path = prompt_path + ".anchors.yaml"

# Read existing sidecar if any. Refuse to overwrite an unknown schema_version.
if os.path.exists(sidecar_path):
    try:
        sidecar = yaml.safe_load(open(sidecar_path, encoding='utf-8')) or {}
    except yaml.YAMLError as e:
        print(f"ERROR: existing sidecar unparseable — aborting commit. {e}", file=sys.stderr)
        sys.exit(2)
    if sidecar.get('schema_version') != 1:
        print(
            f"ERROR: sidecar schema_version mismatch: got {sidecar.get('schema_version')!r}, "
            f"expected 1. Aborting commit to avoid clobbering a future schema.",
            file=sys.stderr,
        )
        sys.exit(2)
    existing_anchors = sidecar.get('anchors') or []
else:
    sidecar = {'schema_version': 1, 'anchors': []}
    existing_anchors = []

sidecar['schema_version'] = 1
sidecar['anchors'] = existing_anchors + staged

# Emit with allow_unicode=True (TR characters in input/expected fields),
# sort_keys=False (schema_version first, anchors next — readable order),
# default_flow_style=False (block style for readability of nested turns[]).
new_text = yaml.safe_dump(sidecar, allow_unicode=True, sort_keys=False, default_flow_style=False)

# Write temp, validate, rename.
tmp = sidecar_path + '.chat-commit.tmp.' + str(os.getpid())
with open(tmp, 'w', encoding='utf-8') as f:
    f.write(new_text)

# Validation: re-parse temp file. If we can't, do not commit.
try:
    check = yaml.safe_load(open(tmp, encoding='utf-8')) or {}
    if check.get('schema_version') != 1:
        raise ValueError(f"schema_version not 1 after write: got {check.get('schema_version')!r}")
    if not isinstance(check.get('anchors'), list):
        raise ValueError("anchors field is not a list")
    if len(check['anchors']) != len(existing_anchors) + len(staged):
        raise ValueError(f"anchor count mismatch: got {len(check['anchors'])}, expected {len(existing_anchors)+len(staged)}")
except Exception as e:
    os.remove(tmp)
    print(f"ERROR: post-write validation failed — rollback complete. {e}", file=sys.stderr)
    sys.exit(3)

# Validation passed — atomic rename.
os.rename(tmp, sidecar_path)

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
- `2` — existing sidecar unparseable or has unknown schema_version (sidecar untouched; surface to user)
- `3` — post-write validation failed (temp removed; sidecar unchanged)

### Step 7.3 — Surface result

On success, print to user (in `report_language`):

- TR: `<N> anchor sidecar'a yazıldı (<basename>.anchors.yaml). /prompt-test ile çalıştırabilirsin.`
- EN: `<N> anchors written to sidecar (<basename>.anchors.yaml). Use /prompt-test to run them.`

On error (exit 2 or 3), print the stderr message verbatim and return to chat loop. The user can investigate the sidecar file (manual YAML edits, schema_version drift) and try again.

### Why sidecar (v0.5.1) — staged-then-commit (unchanged)

The anchors live in a sibling file `<prompt>.anchors.yaml`, not in the prompt's frontmatter. This keeps the prompt SHA256 stable across anchor edits — cached `/prompt-check` audits remain valid, the prompt body and frontmatter stay author-content-only, and the test configuration has a single file the user can edit / diff / commit independently.

Staged saves still gate the write: `/save` × N → `/commit` writes all staged anchors at once. The friction is small (one extra command) and the safety is worth it: a typo'd anchor can be discarded by `/quit → Discard all` without ever touching the sidecar file.

## Phase 8 — `/quit` final

User typed `/quit`. Offer to commit any uncommitted staged anchors, then print the session summary and exit.

### Step 8.1 — Check staged anchors

```bash
STAGED_COUNT=$(python3 -c "import json; print(len(json.load(open('$RUN_DIR/saved_anchors.json'))))")
```

If `STAGED_COUNT > 0`, ask the user:

```
question (TR): "<N> staged anchor var ama henüz commit edilmedi. Sidecar'a (<prompt>.anchors.yaml) yazayım mı?"
question (EN): "<N> staged anchors are uncommitted. Write them to frontmatter now?"
header:        "Commit on exit"
multiSelect:   false
options:
  - label: "Evet, yaz" | "Yes, commit"
    description: "Run Phase 7 commit before exit. Anchors written to the sidecar (<prompt>.anchors.yaml)."
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
- Run dir: .voicepromptkit/<basename>/<chat-NNN>/

Sonraki adım: <one of>
  - /prompt-test <prompt> ile committed anchor'ları çalıştır (M > 0 ise)
  - /prompt-check <prompt> ile audit yap
  - frontmatter'ı manuel düzenle
```

**EN:** same structure, English labels.

The chat always runs in a separate new terminal window (v0.5.11+); after the summary prints, the window can be closed manually.

### Step 8.3 — Exit

Skill returns. No further user input is consumed by `/prompt-chat`. Main session control returns to the user.

## Variables (v0.6.0)

Voice-agent scripts carry placeholder tokens that production Vapi fills from `variableValues` before the call connects. `/prompt-chat` mirrors this with an explicit **detect → bind → inject** layer so constants are set (and reproducible) instead of being randomly invented by the persona.

- **Token forms detected:** Vapi mustache `{{ name }}` / `{{ customer.name }}` and bracketed upper-snake `[BÜYÜK_HARF]` (TR uppercase included). Harness-control brackets (`[SYSTEM: …]`, `[PERSONA_NAME: …]`, `[silence …]`, `<<END_CALL>>`) are never detected — `SYSTEM` / `PERSONA_NAME` / `END_CALL` are also denylisted.
- **Storage — sidecar `<prompt>.vars.yaml`** (prompt-level, persistent), NOT frontmatter. The prompt file is never touched → its SHA256 stays stable and cached `/prompt-check` audits remain valid (same contract as `<prompt>.anchors.yaml`). Schema:
  ```yaml
  schema_version: 1
  variables:
    musteri_adi: "Zeynep Kaya"
    randevu_tarihi: "12 Haziran 14:00"
  ```
  An author-provided `chat_variables:` block in the prompt frontmatter is read as a **seed only** (never written); the sidecar wins.
- **Pre-chat binding (Phase 0.5):** only *unbound* variables are asked (already-bound values from the sidecar / seed are reused silently). Values are substituted into the system prompt at spawn (`_write_wrapped_body`); unbound tokens are left literal so the persona invents them per framing rule 10.
- **Mid-chat editing:** `/vars` lists state; `/set name=value` and `/unset name` update `variables.json` + the sidecar and queue a `[SYSTEM: variable update]` cue (framing rule 13) applied on the next user turn — no subprocess respawn, conversation history preserved. `/reset` re-substitutes current values into a fresh wrapped body.
- **Run-dir contract:** `variables.json` = `{"detected": [{name, token}], "values": {name: value}}`. The runner falls back to detecting from `body.txt` + reading the sidecar when `variables.json` is absent (manual launch).

## Invariants

- **Never modify `body.txt`** in any phase. Body is read-only after Phase 0.
- **Never modify the original prompt file.** Anchors go to `<prompt>.anchors.yaml` (Phase 7); variable values go to `<prompt>.vars.yaml` (Phase 1 / `/set`). Both sidecars keep the prompt SHA256 stable. All other chat-time state is in `chat-NNN/`.
- **Slash commands are case-insensitive.** `/SAVE` works the same as `/save`.
- **Every persisted state update is atomic** (temp + rename) — never partial writes.
- **`chat.jsonl` is append-only.** Never overwrite, never delete mid-session. `/reset` moves the file aside; it does not delete it.
