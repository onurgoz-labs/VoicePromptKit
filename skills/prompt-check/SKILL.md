---
name: prompt-check
description: Audit a prompt file (system prompt, agent definition, voice script, chained workflow) across four lenses — conflict, dominance, gap, drift — plus an optional Turkish phonetic lens for voice agents. Use when the user invokes /prompt-check, asks to "audit a prompt", "check this prompt for contradictions / silent overrides / gaps / drift / voice readability", or passes a path to a prompt file for review. On first run in a repo, walks the user through a 5-question wizard and saves repo defaults to `.promptchecker.json`. Produces line-anchored findings as `report.md` + `findings.json` in an isolated run directory. Never modifies the original prompt file.
---

# prompt-check

You audit a prompt file at the path supplied as `$1`. Read the prompt once, then dispatch each lens family to its dedicated subagent (`static-lens-runner`, `drift-runner`, `tr-phonetic-runner`). Merge their outputs in Phase 7. After the terminal summary (Phase 8), automatically enter the **interactive review** — Phase 9 (summary + decision parsing) and Phase 10 (action dispatch) are part of the default flow, not a separate command. Write all artefacts under an isolated run directory. **Never modify the original prompt file except in Phase 10's `applied` step, governed by the SHA256 stale-audit guard.**

## Inputs you have

- `$1` — relative or absolute path to the prompt file under audit.
- `references/lens-rules.md` — full criteria for the static lenses; read by `static-lens-runner`.
- `references/tr-phonetic.md` — Turkish phonetic rules; read by `tr-phonetic-runner`.
- `references/probes.md` — adversarial probe templates; read by `drift-runner`.
- `references/dialog-flow.md` — interactive templates, free-form decision grammar, lens-selection question shape, and the "konuşalım" sub-flow. Read by Phase 9 and Phase 10.
- `references/overlay-format.md` — `inline-suggestions.md` layout, `decisions.jsonl` shape, and Phase 10 ordering rules. Read by Phase 10.

The skill itself does not need to read the three lens reference files (`lens-rules.md`, `tr-phonetic.md`, `probes.md`) — it only passes their paths to the matching subagent. The two interactive references (`dialog-flow.md`, `overlay-format.md`) ARE read by the skill itself in Phase 9 and Phase 10.

## Phase 0 — Project config (wizard on first run)

Project config lives at `<repo-root>/.promptchecker.json`. It captures repo-level defaults so the user does not write the same frontmatter on every prompt.

Locate the config path:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
CONFIG_PATH="$REPO_ROOT/.promptchecker.json"
if [ -f "$CONFIG_PATH" ]; then
  CONFIG_EXISTS=true
else
  CONFIG_EXISTS=false
fi
echo "REPO_ROOT=$REPO_ROOT"
echo "CONFIG_PATH=$CONFIG_PATH"
echo "CONFIG_EXISTS=$CONFIG_EXISTS"
```

**Branch on the `CONFIG_EXISTS` line the bash block just echoed — do not infer from the path string alone.**

- **If `CONFIG_EXISTS=true` (the bash block printed this line):** STOP — skip the wizard, do not write `.promptchecker.json`, do not ask any questions. Continue to Phase 1. The pre-existing file is the source of truth and will be read in Phase 2 during the frontmatter merge.
- **Only if `CONFIG_EXISTS=false`:** run the first-run wizard before continuing. Ask the user the five questions below (prefer `AskUserQuestion` if available, otherwise plain conversational prompts; either way wait for all five answers before writing the file).

**Sanity check before asking the wizard questions:** read the last echoed `CONFIG_EXISTS=` line from the bash output. If it is `true`, the wizard MUST NOT run regardless of any other reasoning. Overwriting an existing config is a silent data-loss bug.

1. **Default prompt type** for this repo (frontmatter `type:` overrides per-prompt):
   - Options: `system`, `agent`, `vapi`, `task`, `chain`, or *unspecified*.
2. **Turkish phonetic lens** active by default?
   - If answer #1 was `vapi`, recommend `true`; otherwise recommend `false`. User chooses either way.
3. **Target model** (reports + drift simulation):
   - Suggested presets `claude-opus-4-7` (default), `claude-sonnet-4-6`. Free text accepted.
4. **Output formats** (multi-select, ≥ 1):
   - `markdown` (default ✓), `findings_json` (default ✓), `json`.
5. **Drift `expand_count`** (extra scenarios beyond anchors + conflict budget):
   - Integer 0–20. Default `3`. Zero disables drift entirely.

After collecting answers, write `$CONFIG_PATH` as pretty JSON (2-space indent):

```json
{
  "default_type": "<choice or null if unspecified>",
  "target_model": "<answer>",
  "output": ["..."],
  "expand_count": <int>,
  "tr_phonetic": <bool>
}
```

Confirm to the user: `Saved repo defaults to <relative path>. Edit it any time or override per-prompt via frontmatter.`

**Invariants:**
- Never run the wizard if `$CONFIG_PATH` already exists. The user owns that file.
- **Never run the wizard when `CONFIG_EXISTS=true`.** This is a hard rule — no edge case justifies overwriting a populated `.promptchecker.json`. If something seems off (corrupt file, unknown keys), warn the user and continue to Phase 1; do NOT run the wizard.
- If `git rev-parse` fails (not a git repo), use the current working directory as the repo root and warn the user that the config lives in cwd, not a tracked repo.

## Phase 1 — Working directory + versioning

Run this Bash block once. It computes `$RUN_DIR` and updates the `latest` symlink. Echo `$RUN_DIR` so later steps reference the same path.

```bash
ABS_PROMPT=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
BASENAME=$(basename "$1" | sed 's/\.[^.]*$//')
PROMPT_DIR=".promptcheck/$BASENAME"
mkdir -p "$PROMPT_DIR"

# Atomic run-NNN allocation. mkdir without -p fails if the directory exists,
# so a concurrent run claiming the same number loses cleanly and we retry.
ATTEMPT=1
while [ "$ATTEMPT" -le 100 ]; do
  N=$(ls -1 "$PROMPT_DIR" 2>/dev/null | grep -c '^run-')
  NEXT_NUM=$((N + ATTEMPT))
  RUN_NAME=$(printf 'run-%03d' "$NEXT_NUM")
  RUN_DIR="$PROMPT_DIR/$RUN_NAME"
  if mkdir "$RUN_DIR" 2>/dev/null; then break; fi
  ATTEMPT=$((ATTEMPT + 1))
done
if [ "$ATTEMPT" -gt 100 ]; then
  echo "error: could not allocate a free run-NNN slot in $PROMPT_DIR"
  exit 1
fi

# IMPORTANT: $PROMPT_DIR/latest is updated ONLY on success (Phase 8).
# A run that fails mid-way leaves `latest` pointing at the previous good run.

# Timing instrumentation — env-gated, zero overhead when off.
# When PROMPTCHECKER_TIMING=true, every phase boundary appends a
# millisecond-precision line to $RUN_DIR/timing.log.
if [ "$PROMPTCHECKER_TIMING" = "true" ]; then
  TIMING_LOG="$RUN_DIR/timing.log"
  : > "$TIMING_LOG"
  date_ms() { date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))'; }
  log_t() { [ -n "$TIMING_LOG" ] && echo "[$(date_ms)] $1" >> "$TIMING_LOG"; }
  log_t "phase_1_end (run-dir allocated: $RUN_DIR)"
  export TIMING_LOG
fi

# Bootstrap interactive state placeholders so Phase 9/10 can append without
# checking existence. These start empty; Phase 9 writes the real session.json
# at interactive entry, and Phase 9 appends the first decision to
# decisions.jsonl. Both files are append-only / rewrite-in-full after that.
: > "$RUN_DIR/decisions.jsonl"
printf '{}' > "$RUN_DIR/session.json"

echo "RUN_DIR=$RUN_DIR"
echo "RUN_NAME=$RUN_NAME"
echo "ABS_PROMPT=$ABS_PROMPT"
```

**Invariants for the entire run:**
- All artefacts go under `$RUN_DIR/`. Never `.promptcheck/.tmp/`.
- Original prompt file is read-only EXCEPT in Phase 10's `applied` step (governed by SHA256 guard + explicit user decision). No inline annotation, no `.bak`, no edits in any other phase.
- Previous run directories are left intact (versioning).
- `session.json` and `decisions.jsonl` are bootstrapped here so Phase 9/10 never have to test for existence.

## Phase 2 — Frontmatter (deterministic, not LLM)

Extract YAML frontmatter and merge it against env vars + project config + built-in defaults with a single Bash call so the result is deterministic and cheap. Pass `$CONFIG_PATH` from Phase 0 as the third argument.

**Override hierarchy (most specific wins):**
1. Per-prompt frontmatter (in the prompt file itself)
2. Env var (`PROMPTCHECKER_*`)
3. Project config (`.promptchecker.json`)
4. Built-in defaults

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_2_start" >> "$TIMING_LOG"
python3 - "$ABS_PROMPT" "$RUN_DIR" "$CONFIG_PATH" <<'PY'
import sys, re, json, os, hashlib
prompt_path, run_dir, config_path = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(prompt_path, encoding='utf-8').read()

# D1: snapshot the prompt content hash for stale-audit detection in Phase 10's
# applied step (SHA mismatch → auto-route applied decisions to overlay).
prompt_sha256 = hashlib.sha256(text.encode('utf-8')).hexdigest()

# B1: compute body_line_offset so downstream phases can map body.txt lines
# back to the original prompt file's line numbers (Phase 10's applied step
# depends on this — it locates findings by original-file line + current_excerpt).
m = re.match(r'^---\r?\n(.*?)\r?\n---\r?\n?(.*)$', text, re.DOTALL)
if m:
    raw_fm = m.group(1)
    body = m.group(2)
    pre_body = text[:m.start(2)]
    body_line_offset = pre_body.count('\n') + 1  # 1-indexed line in original
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

project = {}
if config_path and os.path.exists(config_path):
    try:
        project = json.load(open(config_path, encoding='utf-8'))
    except Exception:
        project = {}

env = os.environ.get

def truthy(v):
    return str(v).strip().lower() in ('1','true','yes','on') if v is not None else False

# type: frontmatter > project.default_type > None
resolved = {}
resolved['type'] = fm.get('type') or project.get('default_type') or None

resolved['target_model'] = (
    fm.get('target_model')
    or env('PROMPTCHECKER_TARGET_MODEL')
    or project.get('target_model')
    or 'claude-opus-4-7'
)

out = fm.get('output')
if out is None:
    env_out = env('PROMPTCHECKER_OUTPUT')
    if env_out:
        out = [s.strip() for s in env_out.split(',')]
    elif project.get('output'):
        out = project['output']
    else:
        out = ['markdown', 'findings_json']
resolved['output'] = [str(o).strip() for o in (out if isinstance(out, list) else [out])]

# F2: expand_count must preserve 0 (zero explicitly disables drift); avoid `or 3` truthy trap
ec = fm.get('expand_count')
if ec is None:
    ec = env('PROMPTCHECKER_EXPAND_COUNT')
    if ec is None or str(ec).strip() == '':
        ec = project.get('expand_count')
        if ec is None:
            ec = 3
resolved['expand_count'] = int(ec)

resolved['anchors'] = fm.get('anchors') or []

tr = fm.get('tr_phonetic')
if tr is None:
    env_tr = env('PROMPTCHECKER_TR_PHONETIC')
    if env_tr is not None and env_tr != '':
        tr = truthy(env_tr)
    elif 'tr_phonetic' in project:
        tr = bool(project['tr_phonetic'])
    else:
        tr = False
resolved['tr_phonetic'] = bool(tr)

# B1 + D1 metadata
resolved['body_line_offset'] = body_line_offset
resolved['prompt_sha256'] = prompt_sha256

# Collect warnings for unknown frontmatter / config keys (surfaced in Phase 8).
KNOWN_FM = {'type','target_model','output','expand_count','anchors','tr_phonetic'}
KNOWN_CFG = {'$schema','default_type','target_model','output','expand_count','tr_phonetic'}
warnings = []
for k in fm.keys():
    if k not in KNOWN_FM:
        warnings.append(f"unknown frontmatter key: {k}")
for k in project.keys():
    if k not in KNOWN_CFG:
        warnings.append(f"unknown config key: {k}")
if 'html' in resolved['output']:
    warnings.append("output: 'html' is no longer supported in v0.3 — emitting markdown + findings_json instead")
    resolved['output'] = [o for o in resolved['output'] if o != 'html']
    if not resolved['output']:
        resolved['output'] = ['markdown', 'findings_json']
resolved['config_warnings'] = warnings

with open(os.path.join(run_dir, 'frontmatter.json'), 'w', encoding='utf-8') as f:
    json.dump(resolved, f, indent=2, ensure_ascii=False)
# IMPORTANT: write body verbatim — no lstrip — so body.txt line N corresponds
# exactly to original-file line (N + body_line_offset - 1).
with open(os.path.join(run_dir, 'body.txt'), 'w', encoding='utf-8') as f:
    f.write(body)
PY
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_2_end" >> "$TIMING_LOG"
```

If `python3` is unavailable, fall back to reading the file yourself, splitting on the first two `---` lines, and applying the same merge logic by reasoning. State the fallback in the terminal summary.

## Phase 3 — Rule extraction (inline)

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_3_start" >> "$TIMING_LOG"
```

Read `$RUN_DIR/body.txt`. Number lines starting at 1, **including blank lines**, so that `body.txt` line N maps to original-file line `N + frontmatter.body_line_offset - 1`. Extract every atomic rule, instruction, constraint, or directive into a flat list. Apply the criteria in `references/lens-rules.md` section "Rule extraction" — split compound sentences, preserve absolutes ("always", "never"), use the lowest line where the rule begins.

**Line-number contract for every phase that follows (rule-extractor, conflict, dominance, gap, drift, TR phonetic):** all `line` fields you produce are body.txt line numbers (1-indexed, blank lines included). Phase 7 (render) is the single place that translates these to original-file line numbers before writing `findings.json`. **Never** write original-file line numbers from inside a lens — that breaks the contract.

Hold the rules in memory as JSON. Also write `$RUN_DIR/rules.json` with shape:

```json
{ "rules": [{"id":"R1","category":"behavior|format|tone|policy|persona","text":"...","line":12,"source_excerpt":"..."}] }
```

If you extract zero rules, abort with an error written to `$RUN_DIR/error.txt` and surface that to the user.

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_3_end" >> "$TIMING_LOG"
```

## Phase 3.5 — Lens-selection wizard (per-run)

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_3_5_start" >> "$TIMING_LOG"
```

This wizard runs **once per `/prompt-check` invocation**, after rule extraction and before lens dispatch. It is separate from the Phase 0 repo-level wizard (which governs `.promptchecker.json` defaults). Phase 3.5 captures per-run intent.

Ask the user via `AskUserQuestion`:

1. **"Bu prompt için hangi mercekleri çalıştırayım?"** — multi-select. Options: `conflict`, `dominance`, `gap`, `drift`, `tr_phonetic`. Default: all five pre-selected. The frontmatter `tr_phonetic` value (from Phase 2) pre-selects/deselects `tr_phonetic` accordingly; the user can still override.
2. **If `drift` is included in the selection:** ask an integer follow-up for `expand_count`. Default = `frontmatter.expand_count` (which already merges per-prompt → env → project → 3). Range 0–20. If the user picks 0, drift is effectively disabled even though the lens was selected — Phase 5's existing `expand_count == 0` kill switch handles this.
3. **If `tr_phonetic` is included AND `frontmatter.tr_phonetic` was `false`:** ask a yes/no confirmation "Türkçe sesli ajan için TTS denetimi yapılsın mı?". If the user says no, drop `tr_phonetic` from the selection.

The exact wording and option labels live in `references/dialog-flow.md`. Refer to that file for the prompt strings — do not inline copy them here.

Persist the answer in memory as `user_intent`:

```json
{
  "selected_lenses": ["conflict","dominance","gap","drift","tr_phonetic"],
  "expand_count": 3,
  "anchors": [],
  "tr_phonetic_enabled": true,
  "asked_at": "<ISO 8601 UTC>"
}
```

`anchors` is copied verbatim from `frontmatter.anchors` (no per-run question for anchors; they live in frontmatter only).

`tr_phonetic_enabled` is the **runtime authoritative** value for whether to run the TR phonetic lens this pass — computed as `("tr_phonetic" in selected_lenses)` after the wizard answers + Phase 3.5 confirmation. Frontmatter's `tr_phonetic` is only the **default** that pre-selects the checkbox; once the user has answered, `user_intent.tr_phonetic_enabled` overrides it for this run.

Phase 9 writes this `user_intent` block into `session.json` at interactive entry. Until then it is held in memory by the skill.

**Dispatch impact:**
- Phase 4 (static lenses): if `conflict`, `dominance`, or `gap` is unselected, instruct `static-lens-runner` to skip those sub-lenses by passing `selected_lenses` in the dispatch inputs (see Phase 4). If all three are unselected, skip the dispatch entirely and write empty placeholder files.
- Phase 5 (drift): the existing skip gate (`expand_count == 0` OR no anchors/conflicts/role-overrides) already handles drift opt-out. Additionally, if `drift` is unselected here, skip Phase 5 entirely and write `drift.json` with `skipped_reason: "drift lens deselected by user"`.
- Phase 6 (TR phonetic): gate on `user_intent.tr_phonetic_enabled == true` — NOT on `frontmatter.tr_phonetic`. The user's runtime selection is authoritative. If false, skip Phase 6 entirely — no `tr_phonetic.json` is written, and Phase 7 treats missing files as "lens disabled".

Phase 7 already handles missing per-lens JSON files as "lens disabled" — no change there.

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_3_5_end" >> "$TIMING_LOG"
```

## Phases 4 + 5 + 6 — Parallel lens dispatch (drift fans out as soon as Phase 4 lands)

Phase 4 (static lenses) and Phase 6 (TR phonetic, when its gate passes) are **independent** — they read different inputs and write different outputs. Dispatch both subagents in a **single message with two concurrent `Agent` calls** so they execute in parallel. Phase 5 (drift) is **downstream of Phase 4 only** — drift-runner reads `conflicts.json`, `gaps.json`, and `dominances.json` as inputs, so it MUST run after Phase 4's outputs land, but it does NOT depend on Phase 6. The correct order is:

```
   ┌─ static-lens-runner ──┬─→ drift-runner (Phase 5 — needs Phase 4 outputs)
   │                       │
   └─ tr-phonetic-runner ──┘
   (Phase 4 + Phase 6 fan out together;
    Phase 5 starts as soon as Phase 4 lands,
    in parallel with Phase 6 — does not wait for TR)
```

Concretely: in one assistant turn, emit both Phase 4 and Phase 6 `Agent` tool calls. As soon as Phase 4's outputs land, evaluate Phase 5's gate and dispatch drift-runner — do NOT wait for Phase 6 to finish first. Phase 7 awaits all three (Phase 4 + Phase 5 + Phase 6) before rendering.

## Phase 4 — Static lenses (conflict + dominance + gap)

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_4_dispatch_start" >> "$TIMING_LOG"
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_6_dispatch_start" >> "$TIMING_LOG"
```

Dispatch the `static-lens-runner` subagent to apply the three static lenses in one pass. Detection criteria live in `references/lens-rules.md` — the subagent reads that document. The skill itself does no lens analysis.

**Line-number contract:** every `line` field the subagent writes is a body.txt index (1-indexed, blank lines included). Phase 7 is the single place that translates these to original-file line numbers.

Pass inputs and output paths as **separate** top-level fields so the subagent never reads its own future outputs:

```
Agent({
  subagent_type: "static-lens-runner",
  prompt: JSON.stringify({
    inputs: {
      body:            "<absolute path to $RUN_DIR/body.txt>",
      frontmatter:     "<absolute path to $RUN_DIR/frontmatter.json>",
      rules:           "<absolute path to $RUN_DIR/rules.json>",
      lens_rules_ref:  "<absolute path to skills/prompt-check/references/lens-rules.md>",
      selected_lenses: ["conflict", "dominance", "gap"]  // ← subset of these three; runner skips unselected
    },
    output_paths: {
      conflicts:  "<absolute path to $RUN_DIR/conflicts.json>",
      dominances: "<absolute path to $RUN_DIR/dominances.json>",
      gaps:       "<absolute path to $RUN_DIR/gaps.json>"
    }
  }),
  description: "static lenses for " + BASENAME
})
```

Populate `selected_lenses` from `user_intent.selected_lenses` (computed in Phase 3.5), intersected with `["conflict", "dominance", "gap"]` — i.e. drop `drift` and `tr_phonetic` since those belong to other runners. The static-lens-runner reads this field and writes empty `{"conflicts": []}` / `{"dominances": []}` / `{"gaps": []}` for any sub-lens not in the list.

**Skip the Phase 4 dispatch entirely** if all three static lenses are deselected (the intersection is empty). In that case write empty placeholders directly:

```bash
printf '{"conflicts": []}' > "$RUN_DIR/conflicts.json"
printf '{"dominances": []}' > "$RUN_DIR/dominances.json"
printf '{"gaps": []}' > "$RUN_DIR/gaps.json"
```

…and proceed to Phase 5 / 7 without spawning the runner.

`static-lens-runner` writes `$RUN_DIR/conflicts.json`, `$RUN_DIR/dominances.json`, and `$RUN_DIR/gaps.json`. The skill reads them in Phase 7.

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_4_dispatch_end" >> "$TIMING_LOG"
```

## Phase 5 — Drift (conditional)

**Orchestration:** dispatched as soon as Phase 4 completes (i.e. when `conflicts.json`, `dominances.json`, `gaps.json` have landed). Runs **in parallel with Phase 6** (if Phase 6 was triggered) — Phase 5 does NOT wait for the TR runner. Phase 7 awaits all in-flight subagents (Phase 4 + Phase 5 + Phase 6) before rendering.

**Skip Phase 5 if EITHER condition holds:**

A) `expand_count == 0` — the user / project config / env var explicitly disabled drift. This is the hard kill switch. Write `$RUN_DIR/drift.json` with `skipped_reason: "expand_count is 0 — drift disabled"`.

B) ALL of the following are true:
- `frontmatter.anchors` is empty AND
- `conflicts` is empty AND
- no `dominance.mechanism == "role-override"` exists

Write `$RUN_DIR/drift.json` with `skipped_reason: "no anchors, conflicts, or role-overrides — drift adds no signal"`.

In either skip case the file shape is `{"scenarios": [], "runs": [], "verdicts": [], "skipped_reason": "..."}`. Move on to Phase 6.

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_5_skip" >> "$TIMING_LOG"
```

Otherwise dispatch the `drift-runner` subagent (it is the only subagent this skill uses). Pass inputs and the output path as **separate** top-level fields so the subagent does not accidentally read its own future output:

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_5_start" >> "$TIMING_LOG"
```

```
Agent({
  subagent_type: "drift-runner",
  prompt: JSON.stringify({
    inputs: {
      body:                  "<absolute path to $RUN_DIR/body.txt>",
      frontmatter:           "<absolute path to $RUN_DIR/frontmatter.json>",
      rules:                 "<absolute path to $RUN_DIR/rules.json>",
      conflicts:             "<absolute path to $RUN_DIR/conflicts.json>",
      gaps:                  "<absolute path to $RUN_DIR/gaps.json>",
      dominances:            "<absolute path to $RUN_DIR/dominances.json>",
      probes_ref:            "<absolute path to skills/prompt-check/references/probes.md>",
      expand_count_override: 3  // ← from user_intent.expand_count; takes precedence over frontmatter
    },
    output_path: "<absolute path to $RUN_DIR/drift.json>"
  }),
  description: "drift analysis for " + BASENAME
})
```

Populate `expand_count_override` with `user_intent.expand_count` from Phase 3.5 (the per-run integer the user picked in the lens-selection wizard). The drift-runner uses this override when present, falling back to `frontmatter.expand_count` only when the override is absent.

`drift-runner` generates scenarios, simulates the model on each, judges outputs, and writes `$RUN_DIR/drift.json` with shape `{scenarios, runs, verdicts}`. The skill never decomposes drift inline because it is the only step whose token cost scales with prompt length.

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_5_end" >> "$TIMING_LOG"
```

## Phase 6 — Turkish phonetic lens (conditional)

**Run this phase only if `user_intent.tr_phonetic_enabled == true`** (the runtime value computed in Phase 3.5 — NOT `frontmatter.tr_phonetic`). Frontmatter's `tr_phonetic` is the default that pre-selects the wizard checkbox; the user can flip the selection on or off in Phase 3.5, and that runtime decision is authoritative here. Otherwise skip to Phase 7 — `tr_phonetic.json` is not written, and Phase 7 treats the absence as "TR lens disabled".

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_6_skip" >> "$TIMING_LOG"
```

When the gate passes, dispatch the `tr-phonetic-runner` subagent. It seeds `pronunciation_map` from any existing pronunciation guide block in the body, scans for new findings, dedupes against the seed, and writes a single `$RUN_DIR/tr_phonetic.json`. Every rule (skip rules, whitelist, strategy semantics, the "no semantic translation" hard rule, the three `fix_kind` values, the seed block formats and line-range tracking) lives in `references/tr-phonetic.md` — the subagent reads that document; the skill does not repeat the criteria here.

**Line-number contract:** every `line` field (including `seed_block_range.start_line` / `end_line`) the subagent writes is a body.txt index. Phase 7 translates to original-file lines.

**Category-based fix_kind (v0.4.2+):** TR findings carry `fix_kind: "advisory"` OR `fix_kind: "replace"` depending on the category:

- `foreign_word` and `abbreviation` → `fix_kind: "advisory"` — PromptChecker never auto-applies these. They populate `pronunciation_entry` (and optionally `suggested_fix`) and are surfaced for the author to act on by hand. Phase 9.6's commit and Phase 10.3's TR advisory guard force-route any such finding decided as `applied` to `overlay` before any write happens.
- `number_readability` and `punctuation` → `fix_kind: "replace"` — these are concrete substring replacements (e.g. "10kg" → "on kilogram", "—" → ", ") and follow the normal apply flow in Phase 10.3 (SHA check → fix_strategy dispatch → strategy-specific feasibility → apply). They are NOT force-routed to overlay.

PromptChecker therefore auto-applies TR `replace` findings just like conflict / dominance / gap / drift findings, while keeping TR `advisory` findings author-driven. The author still owns the pronunciation_map injection — Phase 10 never writes pronunciation block content back into the prompt file.

Pass inputs and the output path as **separate** top-level fields:

```
Agent({
  subagent_type: "tr-phonetic-runner",
  prompt: JSON.stringify({
    inputs: {
      body:                  "<absolute path to $RUN_DIR/body.txt>",
      frontmatter:           "<absolute path to $RUN_DIR/frontmatter.json>",
      tr_phonetic_ref:       "<absolute path to skills/prompt-check/references/tr-phonetic.md>",
      user_intent_tr_phonetic: true  // ← from user_intent.tr_phonetic_enabled; runtime authoritative
    },
    output_path: "<absolute path to $RUN_DIR/tr_phonetic.json>"
  }),
  description: "TR phonetic lens for " + BASENAME
})
```

Populate `user_intent_tr_phonetic` with `user_intent.tr_phonetic_enabled`. The runner's defensive guard checks this field instead of `frontmatter.tr_phonetic`, so user runtime overrides survive even when the frontmatter default would have suppressed the lens.

`tr-phonetic-runner` writes `$RUN_DIR/tr_phonetic.json` with shape `{ findings[], seed_entries[], warnings[] }`. The skill reads it in Phase 7.

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_6_dispatch_end" >> "$TIMING_LOG"
```

## Phase 7 — Render outputs

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_7_start" >> "$TIMING_LOG"
```

Read every artefact that landed in `$RUN_DIR/` so far: `frontmatter.json`, `rules.json`, `conflicts.json`, `dominances.json`, `gaps.json`, `drift.json`, and (if the TR gate ran) `tr_phonetic.json`. Build a single merged `findings.json` and a human-readable `report.md`. Both are line-anchored.

**Line translation (mandatory):** Every lens wrote `line` numbers as body.txt indices. Before writing findings.json, translate each `line` to an original-file line:

```
original_line = body_line + frontmatter.body_line_offset - 1
```

Apply this to every `findings[].line` and `findings[].related_lines[]`. After translation, body.txt indices must no longer appear in any rendered output. Phase 9 (summary view), Phase 10 (applied step), and `report.md` all depend on this.

**Carry the prompt hash:** copy `frontmatter.prompt_sha256` into the top of findings.json so Phase 10's applied step can detect a stale audit and auto-route to overlay.

### `$RUN_DIR/findings.json`

```json
{
  "prompt_path": "<absolute path>",
  "prompt_sha256": "<hex from frontmatter.prompt_sha256>",
  "run_id": "run-NNN",
  "generated_at": "<ISO 8601 UTC>",
  "summary": {
    "rules": N,
    "conflicts": { "total": N, "high": N, "medium": N, "low": N },
    "dominances": { "total": N, "by_mechanism": { "role-override": N, ... } },
    "gaps": { "total": N, "high": N, "medium": N, "low": N },
    "drift": { "scenarios": N, "passed": N, "failed": N, "skipped": false },
    "tr_phonetic": { "total": N, "by_kind": { ... } }
  },
  "findings": [
    {
      "id": "C1",
      "lens": "conflict|dominance|gap|drift|tr_phonetic",
      "fix_kind": "replace|advisory",
      "severity": "low|medium|high",
      "line": 42,
      "related_lines": [42, 47],
      "current_excerpt": "<verbatim from body.txt>",
      "suggested_fix": "<concrete edit — populated only for fix_kind: replace>",
      "pronunciation_entry": null,
      "rationale": "<one paragraph, ≤ 240 chars>",
      "rule_ids": ["R3","R8"]
    }
  ],
  "pronunciation_map": [
    {
      "term": "DHL",
      "strategy": "pronounce",
      "phonetic": "de-ha-el",
      "alt_translation": null,
      "note": null,
      "source": "finding",
      "source_finding_ids": ["T3"]
    },
    {
      "term": "Konstantinopolis",
      "strategy": "pronounce",
      "phonetic": null,
      "alt_translation": "Bizans başkenti",
      "note": "...",
      "source": "seed",
      "source_finding_ids": []
    }
  ]
}
```

`pronunciation_map` is the union of `tr_phonetic.json.seed_entries` (entries the prompt already had — `source: "seed"`) and the `pronunciation_entry` payload of TR findings (`source: "finding"`). Dedupe by `term` (case-insensitive); if a seed entry and a finding entry collide, **seed wins** (the author's curated text is the source of truth). It is a flat reference list rendered in `report.md` and surfaced in `findings.json` for downstream tooling — Phase 10 never injects it back into the prompt.

**Sort order for findings[] and rendered output:**

1. **Severity descending** — high (h) → medium (m) → low (l). High-severity findings appear first regardless of where they are in the file.
2. **Lens group within severity bucket** — within each severity bucket, group by lens in this fixed order: conflict, dominance, gap, drift, tr_phonetic.
3. **Line ascending within (severity, lens) group** — within the same severity AND lens, sort by line number ascending.

In Python:
```python
LENS_ORDER = {"conflict": 0, "dominance": 1, "gap": 2, "drift": 3, "tr_phonetic": 4}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
findings.sort(key=lambda f: (
    SEVERITY_ORDER.get(f.get("severity", "low"), 2),
    LENS_ORDER.get(f["lens"], 99),
    f.get("line", 0)
))
```

Apply this sort BOTH to `findings.json.findings[]` AND to the visual rendering in report.md.

For each finding:
- `fix_kind: "replace"` → Phase 10's applied step rewrites the line so it produces `suggested_fix` instead of `current_excerpt`. Emitted by `conflict`, `dominance`, `gap`, `drift` lenses, and by TR `number_readability` / `punctuation` findings.
- `fix_kind: "advisory"` → no automatic apply. Emitted by TR `foreign_word` / `abbreviation` findings — Phase 9.6's commit and Phase 10.3's TR advisory guard force-route these to overlay.

### `$RUN_DIR/report.md`

```markdown
# PromptChecker Report — <basename>

- **Prompt:** `<absolute path>`
- **Run:** `<run-NNN>`
- **Generated:** <ISO 8601>
- **Target model:** <target_model>

## Summary

| Lens | Total | High | Medium | Low |
|---|---|---|---|---|
| Conflict | … | … | … | … |
| Dominance | … | … | … | … |
| Gap | … | … | … | … |
| Drift | <scenarios>: <passed>✓ / <failed>✗ | — | — | — |
| TR phonetic | … | … | … | … | (omit row if tr_phonetic disabled)

## Findings

(Grouped by severity, then by lens. Within each (severity, lens) group, findings are line-ordered.)

### HIGH severity

#### Conflicts
- **L<line>** [<id> severity=high, R<a>↔R<b>] — <rationale>
  - <diff-aware body — see render rules below>
- (or "_None._" if no high-severity conflicts)

#### Dominances
- ...

#### Gaps
- ...

#### Drift
- ...

#### TR phonetic
- ...

### MEDIUM severity

#### Conflicts
- ...

(etc.)

### LOW severity

(etc.)
```

If an entire severity bucket has zero findings across all lenses, omit that bucket entirely (don't render "### HIGH severity\n_None._"). If a (severity, lens) pair has zero findings, render the section heading with "_None._" so the structure stays consistent.

**Drift section special-case:** when drift was skipped at the run level (`drift.json.skipped_reason` is set), render a single `_Skipped — <skipped_reason>._` line under whichever severity bucket the drift scenarios would have landed in, OR under HIGH if no severity context exists. Per-scenario rendering still uses:

```
- **S1** [<kind>] <pass|fail> score=<0.00–1.00>
  - Input: `<scenario.input truncated to 120 chars>`
  - Reasons: <reasons joined>
```

Treat drift `fail` as high severity, `pass` as low severity for bucketing purposes.

### Diff rendering for findings

For each finding, determine the render mode:

- If `current_excerpt` and `suggested_fix` look like a substring substitution
  (suggested_fix is similar to current_excerpt with localised character / word changes),
  render a unified diff block:

  ```diff
  - <current_excerpt>
  + <suggested_fix>
  ```

- If `suggested_fix` looks like a structural command
  (starts with "Add ", "Rewrite ", "Move ", "Replace R", "Remove ", "Reword ", or is a
  TODO sentinel like "TODO: ...", or is "Intentional — dismiss this finding"),
  render as-is:

  **Action:** <suggested_fix>

Heuristic for "substring-style":
- Compute a simple longest-common-subsequence ratio between current_excerpt and suggested_fix.
- If ratio > 0.6 AND suggested_fix does not start with one of the structural keywords above,
  it's substring-style → diff render.
- Otherwise → action render.

In Python (use difflib in the render heredoc):

```python
import difflib

STRUCTURAL_PREFIXES = ("Add ", "Add: ", "Rewrite ", "Move ", "Replace R", "Remove ",
                       "Reword ", "TODO:", "Intentional —", "Intentional -")

def render_finding_body(current, suggested):
    if not suggested or suggested.strip() == "":
        return "**Action:** _(see rationale)_"
    if any(suggested.startswith(p) for p in STRUCTURAL_PREFIXES):
        return f"**Action:** {suggested}"
    ratio = difflib.SequenceMatcher(None, current, suggested).ratio()
    if ratio > 0.6:
        return f"```diff\n- {current}\n+ {suggested}\n```"
    return f"**Action:** {suggested}"
```

Drift findings still render with `Input:` and `Reasons:` per the per-scenario template above — the diff rule applies to conflict / dominance / gap / tr_phonetic findings.

`report.md` is the canonical user-facing artefact. If `frontmatter.output` contains `findings_json` but not `markdown`, still write `report.md` — it costs nothing and is the doc humans read. If `output` contains `json`, write the merged report as `$RUN_DIR/report.json` (same shape as findings.json plus a `body_lines` field with the numbered body).

**Removed output modes:** `inline` (v0.2) and `html` (v0.3.1) are no longer supported. Phase 2 strips them with a warning; this phase need not handle them.

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_7_end" >> "$TIMING_LOG"
```

## Phase 8 — Terminal summary

After all writes succeed, **update the `latest` symlink** so it points at this run (the run is now durable), then print the summary:

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_8_start" >> "$TIMING_LOG"
ln -sfn "$RUN_NAME" "$PROMPT_DIR/latest"
```

If `frontmatter.config_warnings[]` is non-empty, include them in the summary so the user notices typos / removed fields.

```
PromptChecker complete — <run-NNN>

- Rules: <N> | Conflicts: <N> (<H> high) | Dominances: <N> | Gaps: <N>
- Drift: <skipped|<N> scenarios, <P> passed, <F> failed>
- TR phonetic: <disabled|<N> findings>

Report:   <relative path to $RUN_DIR/report.md>
Findings: <relative path to $RUN_DIR/findings.json>
Pronunciations master: .promptcheck/<basename>/pronunciations.md (<M> unique terms across <N> runs)
Previous runs: .promptcheck/<basename>/ (run-001 … run-NNN)
Repo defaults: <relative path to .promptchecker.json>

Entering interactive review (Phase 9). Use a compact decision string such as
  "C1, C3 düzelt; G2 yorum bırak; T1..T5 konuşalım; gerisini atla"
to choose what to do with each finding. Type "iptal" to leave the session as
pending and resume later with /prompt-check-resume.
```

The `Pronunciations master:` line surfaces `.promptcheck/<basename>/pronunciations.md` —
the cross-version aggregate file rebuilt by Phase 10.2.1. `<M>` is the number
of unique terms in that file, `<N>` is the count of `run-NNN/` directories
under the prompt that have ever contributed at least one `pronunciation_map`
entry. **Omit this line entirely** when `pronunciations.md` has zero
entries (i.e. no run under this prompt has produced a non-empty
`pronunciation_map` yet). The line is informational and only appears when
there is something for the user to read.

When `PROMPTCHECKER_TIMING=true`, append one extra line to the summary block above (between `Repo defaults:` and the blank line before "Entering interactive review"):

```
Timing log: <relative path to $RUN_DIR/timing.log>
```

Otherwise omit — users without timing enabled see no change.

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_8_end" >> "$TIMING_LOG"
```

After printing this block, **do not stop** — automatically transition to Phase 9 in the same turn. Phase 9 + Phase 10 are part of the default `/prompt-check` flow; the audit is not finished until the user either resolves every finding or explicitly cancels with "iptal".

## Phase 9 — Interactive selection

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_9_start" >> "$TIMING_LOG"
```

Triggered automatically once Phase 8 has printed its summary. There is no separate trigger phrase — the audit flow always passes through Phase 9. (The `/prompt-check-resume` slash command re-enters Phase 9 for a pending run from a previous session.)

Read `references/dialog-flow.md` for the templates, the free-form decision grammar, the verb-to-status mapping, and the "konuşalım" sub-flow contract. Do not inline the grammar here.

### 9.1 — Bootstrap session.json

Write `$RUN_DIR/session.json` (overwriting the placeholder from Phase 1) with the initial shape:

```json
{
  "run_id": "<run-NNN>",
  "prompt_path": "<absolute path>",
  "prompt_sha256_at_audit": "<from findings.json.prompt_sha256>",
  "user_intent": {
    "selected_lenses": ["conflict","dominance","gap","drift","tr_phonetic"],
    "expand_count": 3,
    "anchors": [],
    "asked_at": "<ISO 8601 UTC from Phase 3.5>"
  },
  "findings_state": {
    "C1": { "status": "pending", "lens": "conflict", "line": 12, "updated_at": "<ISO 8601 UTC>" },
    "...": "..."
  },
  "phase": 9,
  "updated_at": "<ISO 8601 UTC>"
}
```

`findings_state` is keyed by `finding.id` and seeded from `findings.json.findings[]`. Every entry starts at `status: "pending"`. `lens` and `line` are mirrored from the finding so Phase 10's TR routing rule and ordering pass do not have to re-read `findings.json`.

All timestamps are ISO 8601 UTC with millisecond precision, e.g. `2026-05-27T14:23:45.123Z`.

### 9.2 — Render the summary view

Print a single markdown table containing every finding. Columns: `id | lens | severity | line | excerpt | suggestion`. Sort by `line` ascending, then by `severity` descending (`high > medium > low`; drift findings are surfaced with their `kind` instead of severity — treat `fail` as `high`, `pass` as `low`).

Truncate `excerpt` and `suggestion` to 60 chars each, appending `…` if cut. For TR phonetic findings, show `pronunciation_entry.phonetic` (or `.alt_translation`) as the suggestion column when `suggested_fix` is null.

The exact table header / footer wording lives in `references/dialog-flow.md`.

### 9.3 — Prompt for decisions

Emit the prompt string from `references/dialog-flow.md` (Turkish + English example), e.g.:

> Hangilerini ne yapayım? Örnek: `C1, C3 düzelt; G2 yorum bırak; T1..T5 konuşalım; gerisini atla`. "iptal" yazarak oturumu beklemede bırakıp daha sonra `/prompt-check-resume` ile devam edebilirsin.

**Use the conversational channel — NOT `AskUserQuestion`.** The decision grammar is compact free-form text; `AskUserQuestion` would force discrete options. Wait for the user's reply on the next turn.

If the user types `iptal` (or `cancel`), write `session.json.phase = "paused"`, surface a one-line "Session paused. Resume with /prompt-check-resume <run-NNN>." message, and exit. `decisions.jsonl` stays untouched.

### 9.4 — Parse + plan (Stage 1: NO writes)

`references/dialog-flow.md` Section 3.2 mandates a **plan → confirm → commit** flow: parse the user's decision string into an in-memory plan, surface the plan with counts + TR redirects + parse errors, get explicit `evet`-style confirmation, and **only then** write to `decisions.jsonl` or rewrite `session.json`.

This sub-section (9.4) is **Stage 1 — parse only, no I/O writes**. Stage 2 (commit) lives in 9.6 and is gated on Stage 1.5's confirmation.

The detailed grammar (id-lists, ranges with `..`, wildcards like `gerisini` / `rest`, verb aliases for `düzelt`/`fix`/`apply`, `yorum bırak`/`overlay`/`note`, `atla`/`skip`/`dismiss`, `konuşalım`/`discuss`/`talk`) lives in `references/dialog-flow.md`. Implement the parser as a single Python heredoc so it is deterministic. The block below **parses into memory and emits a plan JSON to stdout** — it must NOT open `decisions.jsonl` or `session.json` for writing. Skeleton:

```bash
python3 - "$RUN_DIR" <<'PY'
import sys, json, os, datetime, re
run_dir = sys.argv[1]
user_input = os.environ.get('PROMPTCHECKER_DECISION_INPUT', '')

session = json.load(open(os.path.join(run_dir, 'session.json'), encoding='utf-8'))
findings_state = session['findings_state']
known_ids = list(findings_state.keys())

# Load findings.json so we can look up fix_kind per finding (needed for TR routing).
# Only TR findings with fix_kind == "advisory" (foreign_word / abbreviation) are
# force-routed to overlay; TR findings with fix_kind == "replace" (number_readability
# / punctuation) follow the normal apply flow.
findings_path = os.path.join(run_dir, 'findings.json')
findings_by_id = {}
if os.path.exists(findings_path):
    try:
        fj = json.load(open(findings_path, encoding='utf-8'))
        for f in fj.get('findings', []):
            findings_by_id[f['id']] = f
    except Exception:
        pass

# Verb mapping — full table is in references/dialog-flow.md
VERBS = {
    # Turkish
    'düzelt':'applied','duzelt':'applied','uygula':'applied',
    'yorum':'overlay','yorum bırak':'overlay','not bırak':'overlay','overlay':'overlay',
    'atla':'dismissed','geç':'dismissed','sil':'dismissed','iptal et':'dismissed',
    'konuşalım':'discussed','konusalim':'discussed','tartış':'discussed',
    # English
    'fix':'applied','apply':'applied','accept':'applied',
    'note':'overlay','comment':'overlay',
    'skip':'dismissed','dismiss':'dismissed','ignore':'dismissed',
    'discuss':'discussed','talk':'discussed',
}

WILDCARDS = {'gerisini','geri kalan','rest','others','remaining','all'}

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')

def expand_id_token(tok, known):
    tok = tok.strip()
    if not tok: return []
    if tok.lower() in WILDCARDS:
        return [fid for fid, st in findings_state.items() if st['status'] == 'pending']
    if '..' in tok:
        a, b = tok.split('..', 1)
        a, b = a.strip(), b.strip()
        # Same prefix (e.g. T1..T5)
        ma, mb = re.match(r'^([A-Za-z]+)(\d+)$', a), re.match(r'^([A-Za-z]+)(\d+)$', b)
        if ma and mb and ma.group(1) == mb.group(1):
            lo, hi = sorted([int(ma.group(2)), int(mb.group(2))])
            return [f"{ma.group(1)}{n}" for n in range(lo, hi+1) if f"{ma.group(1)}{n}" in known]
        return []
    return [tok] if tok in known else []

# Split top-level on ';' — segments
decisions_resolved = []   # list of (fid, status, raw_segment)
unrecognised = []
seen_ids = set()

for segment in user_input.split(';'):
    segment = segment.strip()
    if not segment: continue
    # Find the verb by greedy-longest match against VERBS keys
    verb_key, status = None, None
    seg_lower = segment.lower()
    for v in sorted(VERBS.keys(), key=len, reverse=True):
        if v in seg_lower:
            verb_key, status = v, VERBS[v]
            break
    if status is None:
        unrecognised.append(segment); continue
    # The id-list is everything before the verb token
    idx = seg_lower.index(verb_key)
    id_part = segment[:idx].strip().rstrip(',')
    ids = []
    for tok in re.split(r'[,\s]+', id_part):
        ids.extend(expand_id_token(tok, known_ids))
    for fid in ids:
        if fid in seen_ids: continue
        seen_ids.add(fid)
        decisions_resolved.append((fid, status, segment))

# TR routing rule: tr_phonetic + applied + fix_kind == "advisory" → overlay.
# Only foreign_word / abbreviation (fix_kind: advisory) are force-routed.
# number_readability / punctuation (fix_kind: replace) follow the normal apply flow.
routed = []
for i, (fid, status, raw) in enumerate(decisions_resolved):
    if status != 'applied' or findings_state[fid]['lens'] != 'tr_phonetic':
        continue
    finding = findings_by_id.get(fid, {})
    if finding.get('fix_kind') == 'advisory':
        routed.append(fid)
        decisions_resolved[i] = (fid, 'overlay', raw)

# Outcome counters for the plan
buckets = {'applied':0,'overlay':0,'dismissed':0,'discussed':0}
for fid, status, _ in decisions_resolved:
    buckets[status] = buckets.get(status, 0) + 1

# Emit the in-memory plan as JSON — NO writes to decisions.jsonl or session.json here.
# Stage 2 (Phase 9.6) will commit this plan after the user confirms.
plan = {
    'parsed': len(decisions_resolved),
    'applied': buckets['applied'],
    'overlay': buckets['overlay'],
    'dismissed': buckets['dismissed'],
    'discussed': buckets['discussed'],
    'tr_routed': len(routed),
    'tr_routed_ids': routed,
    'unrecognised': unrecognised,
    'decisions': [
        {'finding': fid, 'lens': findings_state[fid]['lens'], 'action': status, 'raw_segment': raw}
        for (fid, status, raw) in decisions_resolved
    ],
}
print(json.dumps(plan, ensure_ascii=False))
PY
```

Pass the user's reply as `PROMPTCHECKER_DECISION_INPUT` (or stdin — whichever is cleaner in the harness). The block above is illustrative; trust `references/dialog-flow.md` as the source of truth for the verb table and grammar edge cases.

### 9.5 — Surface the plan and request confirmation (Stage 1.5)

Render the parsed plan back to the user as a clear prose message and ask for confirmation. Do NOT write anything to disk yet.

```
Plan: <N> kararı çözümledim — <A> applied, <B> overlay, <C> dismissed, <D> discussed.
TR auto-redirect: <E> bulgu (<id list>) overlay'e yönlendirilecek (TR findings never modify the prompt).
Parse errors: <F> segmenti çözemedim (<list>).

Onaylıyor musun? Onay için `evet` yaz; iptal için `iptal` yaz; değiştirmek için yeni karar dizesi yaz.
```

If `unrecognised` is non-empty, surface the unparsed segments in the same block and remind the user they can type a corrected decision string to retry. If after parsing every pending finding still has `status: "pending"`, mention this and remind the user that `gerisini atla` is the explicit way to dismiss the remainder.

Wait for the user's reply on the next turn.

### 9.6 — Confirmation gate + commit (Stage 2)

Read the user's next message. The accepted confirmation tokens are: `evet`, `yes`, `onayla`, `ok`, `tamam`, `confirm` (case-insensitive).

Branching on the reply:

- **Confirmation token** → commit the plan: append the routed entries + decision entries to `decisions.jsonl`, rewrite `session.json` with updated `findings_state` and `phase: 9`, then transition to Phase 10. The commit block (Python heredoc) is below.
- **`iptal` / `cancel`** → write `session.json.phase = "paused"`, surface "Session paused. Resume with /prompt-check-resume <run-NNN>.", and exit. `decisions.jsonl` stays untouched (no Stage 2 writes happened).
- **Any other text** → treat it as a new decision string. Loop back to 9.4 (re-parse), then 9.5 (re-plan), then 9.6 again. Repeat until the user either confirms or cancels.

**Commit block (Stage 2 — runs ONLY after a confirmation token):**

```bash
python3 - "$RUN_DIR" <<'PY'
import sys, json, os, datetime
run_dir = sys.argv[1]
plan = json.loads(os.environ.get('PROMPTCHECKER_DECISION_PLAN', '{}'))

session = json.load(open(os.path.join(run_dir, 'session.json'), encoding='utf-8'))
findings_state = session['findings_state']

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')

REQUIRED_KEYS = ('ts', 'finding', 'lens', 'action')

def emit(f, record):
    # Self-check: every emitted JSONL record MUST carry ts/finding/lens/action.
    # Spec: references/overlay-format.md Section 2.
    missing = [k for k in REQUIRED_KEYS if k not in record or record[k] in (None, '')]
    if missing:
        print(f"warning: refusing to write decisions.jsonl record missing keys {missing}: {record}", file=sys.stderr)
        return
    f.write(json.dumps(record, ensure_ascii=False) + '\n')

out_path = os.path.join(run_dir, 'decisions.jsonl')
with open(out_path, 'a', encoding='utf-8') as f:
    # Routed entries first (one per TR-routed id)
    for fid in plan.get('tr_routed_ids', []):
        emit(f, {
            'ts': now(),
            'finding': fid,
            'lens': findings_state[fid]['lens'],
            'action': 'routed_to_overlay',
            'reason': 'TR phonetic findings never modify the prompt file',
        })
    # Then the actual decisions
    for d in plan.get('decisions', []):
        emit(f, {
            'ts': now(),
            'finding': d['finding'],
            'lens': d['lens'],
            'action': d['action'],
            'source': 'phase_9_decision_string',
            'raw_segment': d.get('raw_segment', ''),
        })

# Update session.json
for d in plan.get('decisions', []):
    findings_state[d['finding']]['status'] = d['action']
    findings_state[d['finding']]['updated_at'] = now()
session['phase'] = 9
session['updated_at'] = now()
json.dump(session, open(os.path.join(run_dir, 'session.json'), 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
PY
```

Pass the in-memory plan from 9.4 as `PROMPTCHECKER_DECISION_PLAN` (JSON-encoded). The self-check refuses to write any record missing `ts`, `finding`, `lens`, or `action` — these are the spec minimum from `references/overlay-format.md` Section 2.

After the commit succeeds, print a single line:

```
Parsed N decisions: A applied, B overlay, C dismissed, D discussed, E TR-routed to overlay.
```

Then hand off to Phase 10 in the same turn. Do not wait for further confirmation — the user already confirmed at Stage 1.5.

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_9_end" >> "$TIMING_LOG"
```

## Phase 10 — Action dispatch

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_10_start" >> "$TIMING_LOG"
```

Phase 10 reads `session.json` and `decisions.jsonl` from Phase 9 and executes each decided action. The order below is fixed — see `references/overlay-format.md` Section 4 for the rationale.

**Do not read decisions.jsonl or session.json while a Phase 10 sub-step is still writing to them.** Each sub-step reads its inputs once at entry, performs its writes, then yields to the next sub-step.

**Pre-flight (Phase 10 entry, runs ONCE before 10.1):** compute the prompt file's SHA at audit time vs now exactly once, then keep that decision frozen for the rest of Phase 10.

```bash
ORIGINAL_PROMPT_SHA=$(shasum -a 256 "$ABS_PROMPT" | awk '{print $1}')
AUDIT_SHA=$(python3 -c "import json;print(json.load(open('$RUN_DIR/findings.json'))['prompt_sha256'])")
if [ "$ORIGINAL_PROMPT_SHA" != "$AUDIT_SHA" ]; then
  STALE_AUDIT=true
else
  STALE_AUDIT=false
fi
```

`ORIGINAL_PROMPT_SHA` is the file's hash **at Phase 10 entry** — captured once, before any apply happens. It is compared to `findings.json.prompt_sha256` (the audit snapshot from Phase 2) to answer one question only: "did the file change BEFORE the audit started?" The comparison is performed ONCE here; intra-pass mutations (the file changing because Phase 10.3 itself applied a fix) are intended and MUST NOT trigger a stale-audit signal on subsequent findings.

If `STALE_AUDIT == true`, surface the stale-audit message ONCE (not per finding), and route every `applied` finding to overlay for the rest of this Phase 10 pass (outcome `sha_mismatch` for all of them).

After each successful apply in 10.3, the skill records the post-write SHA in the JSONL entry for archival purposes only — **never** as a new value to compare back against `findings.json.prompt_sha256`. That snapshot stays frozen.

### 10.1 — Dismissed

For each finding with `findings_state[fid].status == "dismissed"`: log only, no I/O. Count for the closing summary. Do not append anything to `decisions.jsonl` here — the Phase 9 entry already records the decision.

### 10.2 — Overlay

Collect every finding whose status is `overlay` (this includes the TR-routed findings from Phase 9.6's commit). Rebuild `$RUN_DIR/inline-suggestions.md` in full per the layout in `references/overlay-format.md`. The file is **rewritten from scratch on every Phase 10 pass** — idempotent regeneration ensures resumed sessions produce consistent overlays.

Do not append per-finding entries to `decisions.jsonl` for this step — the `overlay` decision was already logged in Phase 9.6's commit (or 10.3 if auto-converted from `applied`).

### 10.2.1 — Cross-version pronunciations.md rebuild

After writing this run's `inline-suggestions.md`, rebuild the prompt-scoped
master file `.promptcheck/<basename>/pronunciations.md` so it reflects every
audit run that has ever produced a `pronunciation_map` under this prompt.
The file sits ONE level above the per-run `run-NNN/` directories and is the
single source of truth for the TTS provider config (Vapi / ElevenLabs /
OpenAI Realtime). Full format spec lives in `references/overlay-format.md`
Section 5.

Procedure:

1. Scan `.promptcheck/<basename>/` for every `run-NNN/` directory that
   contains a `findings.json` with a non-empty `pronunciation_map` array.
   Order them by run number ascending (run-001, run-002, …).

2. Build an in-memory aggregate dictionary keyed by term (case-insensitive
   match, but preserve the original casing from the first occurrence):

   ```python
   agg = {}
   for run_dir in sorted(run_dirs):
       findings = json.load(open(run_dir / "findings.json"))
       run_id = run_dir.name
       run_date = findings.get("generated_at", "")[:10]  # YYYY-MM-DD
       for entry in findings.get("pronunciation_map", []):
           key = entry["term"].lower()
           if key not in agg:
               agg[key] = {
                   "term": entry["term"],  # preserve first-seen casing
                   "strategy": entry.get("strategy"),
                   "phonetic": entry.get("phonetic"),
                   "alt_translation": entry.get("alt_translation"),
                   "note": entry.get("note"),
                   "first_seen": (run_id, run_date),
                   "last_seen": (run_id, run_date),
                   "finding_refs": [],
                   "source": entry.get("source"),
               }
           else:
               # update last_seen + collect contributing findings;
               # later runs may also refine strategy/phonetic/note — last wins
               agg[key]["last_seen"] = (run_id, run_date)
               if entry.get("phonetic"):
                   agg[key]["phonetic"] = entry["phonetic"]
               if entry.get("note"):
                   agg[key]["note"] = entry["note"]
               if entry.get("alt_translation"):
                   agg[key]["alt_translation"] = entry["alt_translation"]
           for fid in entry.get("source_finding_ids", []):
               agg[key]["finding_refs"].append(f"{fid}@{run_id}")
   ```

3. Preserve any `## Custom additions` block from an existing
   `pronunciations.md` (between the `<!-- promptchecker:custom-additions:start -->`
   and `<!-- promptchecker:custom-additions:end -->` markers). Read the file
   if it exists, extract everything between those markers, and re-emit it
   verbatim in the rewritten file. If the file does not exist or the markers
   are absent, emit an empty managed block.

4. Render the new `pronunciations.md` per the template in
   `references/overlay-format.md` Section 5. Sort entries alphabetically by
   term (case-insensitive). Sort the YAML pronunciations block in the same
   order.

5. Write the file. Idempotent rewrite — same input produces byte-identical
   output modulo the `Last updated:` timestamp.

Failure modes:
- If `findings.json.pronunciation_map` is empty across every run, write a
  minimal `pronunciations.md` with the header + `_No pronunciation entries
  recorded yet across <N> runs._` and the empty Custom additions block.
  Do not omit the file — its existence is part of the user-facing contract.
- If a run's `findings.json` cannot be parsed, skip that run with a console
  warning. Don't abort the rebuild.

### 10.3 — Applied (feasibility-first, single-event logging)

**Core invariant:** `decisions.jsonl` records ONE event per finding outcome in this sub-step. The `applied` action is written **only when the prompt file was genuinely modified**. If a finding cannot be applied for any reason, it produces a single `routed_to_overlay` entry followed by a single `overlay` entry — never a misleading `applied` entry first.

Note on TR routing: Phase 9.6's commit already wrote `routed_to_overlay` lines for TR `foreign_word` / `abbreviation` (`fix_kind: advisory`) decisions, so those TR findings arrive at Phase 10 as `overlay` and are handled by 10.2 directly. TR findings with `fix_kind: "replace"` (i.e. `kind: number_readability` or `kind: punctuation`) are NOT force-routed — they enter 10.3 with `status: applied` and follow the same flow as conflict/dominance/gap/drift findings (SHA → fix_strategy dispatch → strategy-specific feasibility → apply). The advisory-only feasibility check below remains as a defensive guard in case a future caller skips Phase 9's TR routing.

For each finding with `findings_state[fid].status == "applied"`, run a **feasibility check first** (read-only — no writes to `decisions.jsonl`, no writes to the prompt file).

Possible feasibility outcomes (one per finding):
- `tr_routed` — TR finding with `fix_kind: "advisory"` (defensive guard; foreign_word/abbreviation never modify the prompt file)
- `sentinel_todo` — `suggested_fix` starts with `"TODO:"`; author must resolve by hand → routed to overlay
- `sentinel_intentional` — `suggested_fix` starts with `"Intentional —"` or `"Intentional -"`; runner judged this benign → dismissed
- `no_concrete_fix` — empty or null `suggested_fix` (should never happen with v0.4.1+, but defensive)
- `sha_mismatch` — stale audit detected at Phase 10 entry (Pre-flight saw `STALE_AUDIT == true`)
- `ambiguous` — substring-strategy only: `current_excerpt` appears zero or multiple times on `finding.line`
- `structural_declined` — user declined the risk warning for a `fix_strategy: "structural"` finding
- `applicable` — passes all feasibility checks; the prompt file is modified

**Ordered feasibility rules — apply in this order:**

1. **TR advisory guard:** if `finding.lens == "tr_phonetic"` AND `finding.fix_kind == "advisory"` → outcome `tr_routed`, reason `"TR foreign_word/abbreviation findings never modify the prompt file"`. (TR findings with `fix_kind: "replace"` skip this rule and continue to step 2.)
2. **No concrete fix:** if `finding.suggested_fix` is `null` or an empty string → outcome `no_concrete_fix`, reason `"no concrete suggested_fix — manual author revision required"`.
3. **Stale audit:** if Phase 10's pre-flight set `STALE_AUDIT == true` → outcome `sha_mismatch`, reason `"stale audit — prompt SHA256 mismatch (file changed before this audit)"`. (This check uses the pre-flight result; it is NOT recomputed per finding. Intra-pass mutations from previous applies in this pass are intended and must not trigger this outcome — see Pre-flight section above.)
4. **Sentinel guard:** before any strategy dispatch, inspect `suggested_fix`:
   - If it starts with `"TODO:"` → outcome `sentinel_todo`, reason `"sentinel: TODO — author must resolve by hand"`. Route to overlay.
   - If it starts with `"Intentional —"` or `"Intentional -"` → outcome `sentinel_intentional`, reason `"sentinel: Intentional — runner judged this benign, no action"`. Dismiss (no overlay).
   - Sentinels are structural by definition (per `lens-rules.md`); they MUST NOT fall through to the Edit dispatch.
5. **`fix_strategy` dispatch** — at this point the finding has a non-empty, non-sentinel `suggested_fix` and the audit is fresh. Branch on `fix_strategy`:

   **5a. `fix_strategy: "substring"` (or absent, for backward compatibility):**
   - Run the substring locatability check: read the prompt file fresh, index to `finding.line` (already an original-file line; Phase 7 translated it). Count occurrences of `current_excerpt` on that line:
     - Zero matches → outcome `ambiguous`, reason `"ambiguous occurrence — substring not locatable on line N"`.
     - More than one match → outcome `ambiguous`, reason `"ambiguous occurrence — substring matches multiple positions on line N"`.
     - Exactly one match → outcome `applicable`. Perform the substring replacement: read the prompt file, locate `line` + `current_excerpt`, replace with `suggested_fix`, write the file back.

   **5b. `fix_strategy: "structural"`:**
   - Structural fixes may add a new clause, move a rule, or rewrite across lines. They do NOT replace a literal substring on a single line, so the `ambiguous` / `current_excerpt`-must-be-unique check from 5a is SKIPPED for structural fixes. (The sentinel guard in step 4 already filtered out TODO/Intentional sentinels.)
   - Surface a risk warning to the user BEFORE applying: "⚠ Structural change for <finding-id>: the suggestion is an action description, not a literal substring. I will use the Edit tool to apply: <suggested_fix>. Confirm? (y/n)".
   - If the user declines → outcome `structural_declined`. Route to overlay.
   - If the user accepts → outcome `applicable`. Apply via the Edit tool (semantic edit reflecting the intent of `suggested_fix`).
   - Implementation note: when the user said `hepsini düzelt` (wildcard), the risk-warning prompt is shown ONCE per structural finding, not bundled. Each structural application is its own decision point.

**Single-event write per finding** based on the resolved outcome:

- **`applicable` + successful write** — append ONE `applied` line to `decisions.jsonl`.

  For substring writes:

  ```json
  {"ts":"<now>","finding":"<id>","lens":"<lens>","action":"applied","target":"prompt_file","tool":"substring_replace","fix_strategy":"substring","from":"<current_excerpt>","to":"<suggested_fix>","line":<N>,"source":"phase_9_decision_string","raw_segment":"<user-segment>","original_sha256":"<ORIGINAL_PROMPT_SHA at Phase 10 entry>","new_sha256":"<post-write SHA>"}
  ```

  For structural writes:

  ```json
  {"ts":"<now>","finding":"<id>","lens":"<lens>","action":"applied","target":"prompt_file","tool":"edit","fix_strategy":"structural","intent":"<suggested_fix>","line":<N>,"risk_acknowledged":true,"source":"phase_9_decision_string","raw_segment":"<user-segment>","original_sha256":"<ORIGINAL_PROMPT_SHA at Phase 10 entry>","new_sha256":"<post-write SHA>"}
  ```

  The `new_sha256` field is **archival only** — it records what the file looks like after this write so resume / audit-trail tooling can reconstruct history. It is NOT compared back to `findings.json.prompt_sha256` for the next finding in the same pass; see Pre-flight.

- **`sentinel_intentional`** — do NOT modify the prompt file, do NOT add to the overlay set. The runner already marked the finding as benign. Update `findings_state[fid].status = "dismissed"` in memory. Append ONE `dismissed` line:

  ```json
  {"ts":"<now>","finding":"<id>","lens":"<lens>","action":"dismissed","reason":"sentinel: Intentional — runner judged this benign, no action","source":"phase_10_sentinel_guard"}
  ```

- **`tr_routed` / `sentinel_todo` / `no_concrete_fix` / `sha_mismatch` / `ambiguous` / `structural_declined`** — do NOT modify the prompt file. Update `findings_state[fid].status = "overlay"` in memory. Append ONE `routed_to_overlay` line then ONE `overlay` line to `decisions.jsonl`:

  ```json
  {"ts":"<now>","finding":"<id>","lens":"<lens>","action":"routed_to_overlay","reason":"<reason from outcome>","source":"phase_9_decision_string","raw_segment":"<user-segment>"}
  {"ts":"<now>","finding":"<id>","lens":"<lens>","action":"overlay"}
  ```

  Add the finding to the overlay set processed in 10.2.

**No `applied` line is ever written for a finding whose outcome was anything other than `applicable` + successful dispatch.** The feasibility check (and, for structural, the user's risk acknowledgement) precedes the log write — full stop.

After the applied pass completes, record `current_prompt_sha256 = shasum -a 256 "$ABS_PROMPT"` in memory for archival / downstream queries. **Do not modify `findings.json.prompt_sha256`** — that field is the audit snapshot from Phase 2 and stays frozen.

If any auto-conversion happened in this sub-step (any outcome other than `applicable` + applied), re-run 10.2 once so `inline-suggestions.md` includes the newly routed overlay findings.

Track the per-outcome counts for the Phase 10.5 closing summary:
- `applied_count` — outcome was `applicable` and the write succeeded (any `fix_strategy`)
- `structural_applied_count` — subset of `applied_count` where `fix_strategy: "structural"` (user acknowledged the risk warning and the Edit tool fired)
- `tr_routed_count` — outcome `tr_routed`
- `sentinel_todo_count` — outcome `sentinel_todo` (routed to overlay)
- `sentinel_intentional_count` — outcome `sentinel_intentional` (dismissed)
- `no_fix_count` — outcome `no_concrete_fix`
- `stale_audit_count` — outcome `sha_mismatch`
- `ambiguous_count` — outcome `ambiguous`
- `structural_declined_count` — outcome `structural_declined` (user said no to the risk warning)

### 10.4 — Discussed (the "konuşalım" sub-flow)

Process findings whose status is `discussed` in `id` order (stable across resume). For each one:

1. **Display** the full finding to the user — lens, severity, original-file line, full `current_excerpt`, full `rationale`, full `suggested_fix` (or `pronunciation_entry` for TR), and any related rule ids.
2. **Ask** via `AskUserQuestion` with the four options defined in `references/dialog-flow.md`. The expected options are:
   - Apply as suggested (`applied`)
   - Add as overlay only (`overlay`)
   - Skip (`dismissed`)
   - I'll revise it myself (`revised`)
3. **If the user picks "ben revize ediyorum" / "revised":**
   - Open a free-form conversational prompt: ask the user to type the new suggestion text.
   - Append a `revised` entry to `decisions.jsonl` with `new_suggestion: <user-supplied text>`, `original_suggestion: <findings.suggested_fix>`, `at: <now>`.
   - Then ask via `AskUserQuestion` whether to apply the revised text or store it as overlay only — two options.
   - The user's chosen final action runs through the same logic as 10.3 (if `applied`) or 10.2 (if `overlay`). For `applied`, the substring being replaced is still `current_excerpt`; the replacement is the revised text from the user.
4. **TR advisory routing rule still applies inside 10.4:** if the finding has `lens == "tr_phonetic"` AND `fix_kind == "advisory"` (foreign_word / abbreviation) and the user picks `applied` (or `revised → applied`), auto-convert to `overlay` with a `routed_to_overlay` entry in `decisions.jsonl` preceding the `overlay` entry. TR findings with `fix_kind == "replace"` (number_readability / punctuation) are NOT force-routed and follow the normal 10.3 flow.
5. **Record the final action** in `decisions.jsonl` (`applied` / `overlay` / `dismissed`). Update `findings_state[fid].status` to that terminal status. Do not leave `discussed` or `revised` as the final status — they are transient.
6. **Loop** until every `discussed` finding has been resolved to one of the four terminal states.

If during 10.4 a SHA mismatch is detected (the user took a long pause and an external tool changed the file), apply the same drift handling as 10.3: auto-convert pending `applied` decisions in 10.4 to `overlay`, log the routing, continue.

### 10.5 — Phase 10 closing summary

Print a single block. The counts mirror the per-outcome accounting from 10.3 and the user-driven outcomes from 10.2 / 10.4 — each finding is counted exactly once.

```
Interactive review complete — <run-NNN>

- Applied:        <N> (each one actually wrote to <prompt-path>; of which <S> structural via Edit tool)
- Auto-routed:    <X> (TR: <a>, sentinel TODO: <st>, no-fix: <b>, stale-audit: <c>, ambiguous: <d>, structural-declined: <e>)
- Sentinel Intentional: <SI> (dismissed — runner judged benign, no action)
- Manually overlay: <Y> (user chose "yorum bırak" directly)
- Dismissed:      <Z>
- Revised:        <W> (of which: A applied, B overlay)

Overlay file: <relative path to $RUN_DIR/inline-suggestions.md>  (if any overlays exist)
Decisions log: <relative path to $RUN_DIR/decisions.jsonl>
Session state: <relative path to $RUN_DIR/session.json>
```

- `Applied` = `applied_count` from 10.3 (the number of `applied` lines in `decisions.jsonl` for this pass; never inflated by failed-feasibility findings). The `of which <S> structural via Edit tool` split surfaces `structural_applied_count` so the user can see how many writes went through the risk-warned Edit path versus plain substring replacement.
- `Auto-routed` = `tr_routed_count + sentinel_todo_count + no_fix_count + stale_audit_count + ambiguous_count + structural_declined_count` from 10.3. Always break down by the six reasons so the user knows why each one was redirected. (Sentinel TODO is broken out separately because it has a distinct semantic — author must resolve by hand.)
- `Sentinel Intentional` = `sentinel_intentional_count` from 10.3 (findings the runner pre-marked as benign, dismissed without overlay).
- `Manually overlay` = findings whose Phase 9 decision was `overlay` (or `konuşalım → overlay`). Does NOT include the auto-routed bucket — those are reported separately to avoid double-counting.
- `Revised` = findings that went through the `konuşalım → revised` path in 10.4. The `A applied, B overlay` split reflects the terminal action chosen for each revised entry.

Update `session.json.phase = "complete"` and `session.json.updated_at` on exit. If any findings remain `pending` at this point (user did not address them and did not type `gerisini atla`), set `session.json.phase = "paused"` instead and remind the user they can resume with `/prompt-check-resume <run-NNN>`.

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_10_end" >> "$TIMING_LOG"
```

## Don'ts

- Don't extract frontmatter with an LLM pass; use Bash/Python — it's deterministic and free.
- Don't read the original prompt more than once per phase; pass `body.txt` between steps.
- Don't run lens analysis inline in the skill. Each lens family has a dedicated subagent (`static-lens-runner`, `drift-runner`, `tr-phonetic-runner`); the skill only dispatches and reads outputs.
- Don't write outside `$RUN_DIR/` (except for `.promptchecker.json` in Phase 0 with explicit wizard consent, and the original prompt file in Phase 10's applied step under SHA guard + user decision).
- Don't modify the original prompt file in any phase other than Phase 10's applied step — and even there, only when the SHA matches and the user explicitly decided `applied`.
- Don't run the Phase 0 wizard if `.promptchecker.json` already exists. The user owns that file.
- Don't reintroduce batch / apply-mode. There is no `/prompt-check-apply` anymore — its semantics are folded into Phase 10. Resume uses `/prompt-check-resume`.
- Don't define `inline-suggestions.md` format, `decisions.jsonl` shape, or the decision grammar inline in this skill — they live in `references/overlay-format.md` and `references/dialog-flow.md`.
- Don't read `decisions.jsonl` or `session.json` in the same sub-step that writes to them — finish the write, then move on.
- Don't append duplicate entries to `decisions.jsonl` on resume — append only NEW actions, not re-statements of historical decisions. Resume reads the existing file as history and continues from there.
- Don't keep `findings_state[*].status` at `"discussed"` or `"revised"` as a terminal state — both are transient. Every finding must end at `pending` (only if the run is paused), `applied`, `overlay`, or `dismissed`.
- Don't use `AskUserQuestion` for the free-form decision string in Phase 9.3 — it must be conversational so the user can express ranges, wildcards, and verb aliases in one line. Use `AskUserQuestion` only for the four-option choice inside the Phase 10.4 sub-flow.
- Don't write an `applied` line to `decisions.jsonl` for a finding that didn't actually modify the prompt file. The feasibility check must precede the log write — single event per finding outcome. A failed-feasibility finding produces exactly one `routed_to_overlay` line followed by exactly one `overlay` line; an applicable finding produces exactly one `applied` line. Never two events that imply a prompt-file mutation when none occurred.
- Don't sort findings by line number alone — severity-first grouping is mandatory for both findings.json and report.md.
- Don't enable PROMPTCHECKER_TIMING in production runs unless debugging. The overhead is small but the timing.log file grows on every run and clutters the run dir.
- Don't re-compare the prompt file's SHA to `findings.json.prompt_sha256` after the first apply in a Phase 10 pass. The stale-audit guard is computed ONCE in Phase 10's Pre-flight and checks for *pre-audit* drift; intra-pass mutations from successful applies are intended and must not feed back into the comparison. The `new_sha256` field in `decisions.jsonl` is archival only.
- Don't write to `decisions.jsonl` or `session.json` during Phase 9.4 parsing — those writes are gated on explicit user confirmation in Phase 9.6 (Stage 2). The parser emits an in-memory plan only; the commit block runs after `evet`/`yes`/`onayla`/`ok`/`tamam`/`confirm`.
- Don't emit JSONL decision records missing the spec-required keys `ts`, `finding`, `lens`, `action`. The commit block self-checks every record and refuses to write incomplete lines (see `references/overlay-format.md` Section 2).
- Don't force-route every TR finding to overlay. Only TR findings with `fix_kind: "advisory"` (categories `foreign_word` and `abbreviation`) bypass the prompt file. TR findings with `fix_kind: "replace"` (categories `number_readability` and `punctuation`) follow the normal apply flow in Phase 10.3.
- Don't apply a TODO/Intentional sentinel as if it were a regular structural fix. The sentinel guard in Phase 10.3 (step 4) intercepts them: `TODO:` routes to overlay (`sentinel_todo`), `Intentional —` is dismissed (`sentinel_intentional`). Neither ever reaches the Edit tool.
- Don't overwrite the `## Custom additions` block in `pronunciations.md`. The author owns content between the `<!-- promptchecker:custom-additions:start -->` and `<!-- promptchecker:custom-additions:end -->` markers; the rebuild MUST preserve that block verbatim.
