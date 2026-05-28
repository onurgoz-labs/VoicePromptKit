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
- **Only if `CONFIG_EXISTS=false`:** run the first-run wizard before continuing. Ask the user the seven questions below (prefer `AskUserQuestion` if available, otherwise plain conversational prompts; either way wait for all seven answers before writing the file).

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
6. **Max prompt character limit** (triggers compact mode for large prompts):
   - Options: 25000 (small), 50000 (default ✓), 100000 (large), 0 (unlimited — disables compact mode entirely)
   - Free text accepted (positive integer or 0).
   - When set to a non-zero value, prompts whose body exceeds this threshold are audited in "compact mode" — cheaper analysis policies that trade depth for speed (low-severity findings skipped, conflict pair budget capped, drift expand_count halved, rule text trimmed). The threshold compares against the BODY length (frontmatter stripped), not the full file size.
7. **Report language** (controls skill-rendered text in report.md, terminal summary, and Phase 9 dialog prompts):
   - Options: `tr` (Türkçe, default ✓), `en` (English).
   - Lens-generated content (rationale, suggested_fix, current_excerpt) stays in whatever language the lens runner produces — only the skill's own template strings translate. So an English conflict rationale remains English in a TR report; the surrounding section headings (Summary / Findings / High severity / etc.) translate.

After collecting answers, write `$CONFIG_PATH` as pretty JSON (2-space indent):

```json
{
  "default_type": "<choice or null if unspecified>",
  "target_model": "<answer>",
  "output": ["..."],
  "expand_count": <int>,
  "tr_phonetic": <bool>,
  "max_char_limit": <int>,
  "report_language": "<tr | en>"
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

# Resolve max_char_limit: frontmatter > env (PROMPTCHECKER_MAX_CHAR_LIMIT) > project config > default 50000
mcl = fm.get('max_char_limit')
if mcl is None:
    mcl_env = env('PROMPTCHECKER_MAX_CHAR_LIMIT')
    if mcl_env is not None and str(mcl_env).strip() != '':
        try:
            mcl = int(mcl_env)
        except ValueError:
            mcl = None
    if mcl is None:
        mcl = project.get('max_char_limit', 50000)
try:
    resolved['max_char_limit'] = int(mcl)
except (TypeError, ValueError):
    resolved['max_char_limit'] = 50000

# Measure body length and decide compact_mode
body_char_count = len(body)
resolved['body_char_count'] = body_char_count
resolved['compact_mode'] = (
    resolved['max_char_limit'] > 0 and body_char_count > resolved['max_char_limit']
)

# Resolve report_language: frontmatter > env (PROMPTCHECKER_REPORT_LANGUAGE) > project config > default 'tr'
rlang = fm.get('report_language')
if rlang is None:
    rlang_env = env('PROMPTCHECKER_REPORT_LANGUAGE')
    if rlang_env is not None and str(rlang_env).strip() != '':
        rlang = str(rlang_env).strip().lower()
    if rlang is None:
        rlang = project.get('report_language', 'tr')
# Normalise + fallback warning if unknown value supplied.
rlang_norm = rlang.strip().lower() if isinstance(rlang, str) else None
if rlang_norm in ('tr', 'en'):
    resolved['report_language'] = rlang_norm
    _rlang_unknown = None
else:
    resolved['report_language'] = 'tr'
    # Only warn when the user (or env / config) supplied an unknown value;
    # silent default-fallback when nothing was set at all.
    _rlang_unknown = rlang if rlang is not None else None

# Collect warnings for unknown frontmatter / config keys (surfaced in Phase 8).
KNOWN_FM = {'type','target_model','output','expand_count','anchors','tr_phonetic','max_char_limit','report_language'}
KNOWN_CFG = {'$schema','default_type','target_model','output','expand_count','tr_phonetic','max_char_limit','report_language'}
warnings = []
if _rlang_unknown is not None:
    warnings.append(
        f"unknown report_language value: {_rlang_unknown!r} — falling back to 'tr'"
    )
if resolved['compact_mode']:
    warnings.append(
        f"compact mode active: body is {body_char_count} chars, exceeds max_char_limit "
        f"{resolved['max_char_limit']} — cheaper analysis policies will apply"
    )
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

## Phase 3 — Rule extraction (inline) + section_index build

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_3_start" >> "$TIMING_LOG"
```

**Phase 3 also produces `section_index.json` — a deterministic line-to-section map.**

The skill scans body.txt once and records every numbered section header. The resulting map lets every downstream lens (and Phase 7 render) attach a `section_ref` to every finding by simple line lookup.

Bash + Python implementation (runs as part of Phase 3, before rule extraction):

```bash
python3 - "$RUN_DIR/body.txt" "$RUN_DIR/section_index.json" <<'PY'
import sys, re, json

body_path, out_path = sys.argv[1], sys.argv[2]
lines = open(body_path, encoding='utf-8').read().split('\n')

section_re    = re.compile(r'^##\s+SECTION\s+(\d+)\b\s*[—\-:]?\s*(.*)$')
subsection_re = re.compile(r'^###\s+(\d+)\.(\d+)\b\s*[—\-:]?\s*(.*)$')

current_section = None
current_section_title = None
current_subsection = None
current_subsection_title = None
index = []  # list of {line, section, subsection, section_title, subsection_title}

for i, line in enumerate(lines, start=1):
    m_sec = section_re.match(line)
    m_sub = subsection_re.match(line)
    if m_sec:
        current_section = m_sec.group(1)
        current_section_title = m_sec.group(2).strip() or None
        current_subsection = None
        current_subsection_title = None
    elif m_sub:
        # Subsection number must match current section; orphans handled by schema lens.
        current_subsection = f"{m_sub.group(1)}.{m_sub.group(2)}"
        current_subsection_title = m_sub.group(3).strip() or None
    index.append({
        "line": i,
        "section": current_section,
        "subsection": current_subsection,
        "section_title": current_section_title if current_section else None,
        "subsection_title": current_subsection_title if current_subsection else None
    })

# Compact representation: ranges of consecutive same-section lines.
compact_ranges = []
last_key = None
range_start = None
prev_section_title = None
prev_subsection_title = None
for entry in index:
    key = (entry["section"], entry["subsection"])
    if key != last_key:
        if last_key is not None:
            compact_ranges.append({
                "from_line": range_start,
                "to_line": entry["line"] - 1,
                "section": last_key[0],
                "subsection": last_key[1],
                "section_title": prev_section_title,
                "subsection_title": prev_subsection_title
            })
        range_start = entry["line"]
        last_key = key
    prev_section_title = entry["section_title"]
    prev_subsection_title = entry["subsection_title"]

# Flush final range
if last_key is not None and range_start is not None:
    compact_ranges.append({
        "from_line": range_start,
        "to_line": index[-1]["line"],
        "section": last_key[0],
        "subsection": last_key[1],
        "section_title": prev_section_title,
        "subsection_title": prev_subsection_title
    })

out = {
    "applicable": any(e["section"] is not None for e in index),
    "ranges": compact_ranges
}

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
PY
```

The output shape is `{"applicable": bool, "ranges": [{from_line, to_line, section, subsection, section_title, subsection_title}]}`. When the body has no numbered sections, `applicable: false` and `ranges: []`. Lens runners use this for `section_ref` lookups; Phase 7 renderer uses it for section-aware report headings.

**Lookup helper** (downstream lens runners + Phase 7 use this):
- Given a line number L, find the range R where `R.from_line <= L <= R.to_line`.
- If R exists AND R.section is not null: `section_ref = {"section": R.section, "subsection": R.subsection, "section_title": R.section_title, "subsection_title": R.subsection_title}`.
- Otherwise: `section_ref = null` (line is outside any numbered section, e.g. preamble).

### Rule extraction

Read `$RUN_DIR/body.txt`. Number lines starting at 1, **including blank lines**, so that `body.txt` line N maps to original-file line `N + frontmatter.body_line_offset - 1`. Extract every atomic rule, instruction, constraint, or directive into a flat list. Apply the criteria in `references/lens-rules.md` section "Rule extraction" — split compound sentences, preserve absolutes ("always", "never"), use the lowest line where the rule begins.

**Line-number contract for every phase that follows (rule-extractor, conflict, dominance, gap, drift, TR phonetic):** all `line` fields you produce are body.txt line numbers (1-indexed, blank lines included). Phase 7 (render) is the single place that translates these to original-file line numbers before writing `findings.json`. **Never** write original-file line numbers from inside a lens — that breaks the contract.

Hold the rules in memory as JSON. Also write `$RUN_DIR/rules.json` with shape:

```json
{ "rules": [{"id":"R1","category":"behavior|format|tone|policy|persona","text":"...","line":12,"source_excerpt":"..."}] }
```

**Compact mode (frontmatter.compact_mode == true):** keep each rule's `text` field to ≤ 100 characters (rough one-line summary; truncate the explanatory clause if needed) and `source_excerpt` to ≤ 120 characters. This trims rule-list payload that downstream lenses load. Atomic-rule semantics unchanged — the policy is about VERBOSITY, not correctness.

If you extract zero rules, abort with an error written to `$RUN_DIR/error.txt` and surface that to the user.

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_3_end" >> "$TIMING_LOG"
```

## Phase 3.5 — Lens-selection wizard (per-run)

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_3_5_start" >> "$TIMING_LOG"
```

This wizard runs **once per `/prompt-check` invocation**, after rule extraction and before lens dispatch. It is separate from the Phase 0 repo-level wizard (which governs `.promptchecker.json` defaults). Phase 3.5 captures per-run intent.

**CRITICAL — AskUserQuestion is MANDATORY here.**

Phase 3.5 MUST emit an `AskUserQuestion` tool call. Do NOT substitute prose
"which lenses?" questions. Do NOT default to "all six selected" silently.
Do NOT skip Phase 3.5 even if it seems like the user has implied
preferences. The wizard is an interactive contract — the user must see and
respond to the multi-select widget.

Failure modes that bypass this rule:
- Emitting a free-text "Which lenses do you want? (list them)" prose
  question instead of AskUserQuestion → WRONG. Always use the tool.
- Inferring lens selection from `.promptchecker.json` repo defaults
  without asking → WRONG. Repo defaults SEED the AskUserQuestion option
  states (pre-checked), they don't replace the question.
- Proceeding silently when "all defaults are obvious" → WRONG. The user
  may want to deselect specific lenses for THIS audit.
- Combining lens selection with other questions into one prose block →
  WRONG. AskUserQuestion handles its own UI.

If for any reason AskUserQuestion is unavailable in the current execution
context (e.g. headless / batch invocation), abort Phase 3.5 with an
explicit error: "Phase 3.5 requires AskUserQuestion. Re-invoke
/prompt-check from an interactive Claude Code session." Do not proceed
with silent defaults.

The AskUserQuestion shape:
- Question text: "Bu prompt için hangi mercekleri çalıştırayım?" (when
  `report_language == "tr"`) or "Which lenses should I run for this prompt?"
  (when `report_language == "en"`)
- `multiSelect: true`
- Options: the six lenses (conflict, dominance, gap, drift, tr_phonetic, schema)
- Each option's selected state seeds from `.promptchecker.json` /
  user_intent computed defaults.

After the user submits, IF `expand_count` needs adjusting (drift selected)
or anchors discussion is warranted, emit a SECOND AskUserQuestion or a
direct prose follow-up. These follow-ups are also mandatory when
applicable; do not silently default.

Ask the user via `AskUserQuestion`:

1. **"Bu prompt için hangi mercekleri çalıştırayım?"** — multi-select. Options:

   ```
   options: [
     { label: "conflict",     description: "logical contradictions" },
     { label: "dominance",    description: "silent overrides" },
     { label: "gap",          description: "undefined cases / ambiguous terms" },
     { label: "drift",        description: "adversarial scenario simulation" },
     { label: "tr_phonetic",  description: "Turkish TTS readability" },
     { label: "schema",       description: "section numbering / ordering / heading consistency" }
   ]
   ```

   Default: all six pre-selected (`conflict`, `dominance`, `gap`, `drift`, `tr_phonetic`, `schema`). The frontmatter `tr_phonetic` value (from Phase 2) pre-selects/deselects `tr_phonetic` accordingly; the user can still override. `schema` is pre-checked by default like the other static / TR lenses; for `drift`, the existing pre-check logic stays (depends on anchors/conflicts).

   **Wizard follow-up clarification (surface this to the user alongside the schema option):** "schema is auto-skipped on prompts with no numbered section headings — if your prompt is a flat instruction set, you'll see `schema lens: not applicable` in the Phase 8 summary."

2. **If `drift` is included in the selection:** ask an integer follow-up for `expand_count`. Default = `frontmatter.expand_count` (which already merges per-prompt → env → project → 3). Range 0–20. If the user picks 0, drift is effectively disabled even though the lens was selected — Phase 5's existing `expand_count == 0` kill switch handles this.
3. **If `tr_phonetic` is included AND `frontmatter.tr_phonetic` was `false`:** ask a yes/no confirmation "Türkçe sesli ajan için TTS denetimi yapılsın mı?". If the user says no, drop `tr_phonetic` from the selection.

The exact wording and option labels live in `references/dialog-flow.md`. Refer to that file for the prompt strings — do not inline copy them here.

Persist the answer in memory as `user_intent`:

```json
{
  "selected_lenses": ["conflict","dominance","gap","drift","tr_phonetic","schema"],
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
- Phase 4 (static lenses): if `conflict`, `dominance`, `gap`, or `schema` is unselected, instruct `static-lens-runner` to skip those sub-lenses by passing `selected_lenses` in the dispatch inputs (see Phase 4). If all four are unselected, skip the dispatch entirely and write empty placeholder files.
- Phase 5 (drift): the existing skip gate (`expand_count == 0` OR no anchors/conflicts/role-overrides) already handles drift opt-out. Additionally, if `drift` is unselected here, skip Phase 5 entirely and write `drift.json` with `skipped_reason: "drift lens deselected by user"`.
- Phase 6 (TR phonetic): gate on `user_intent.tr_phonetic_enabled == true` — NOT on `frontmatter.tr_phonetic`. The user's runtime selection is authoritative. If false, skip Phase 6 entirely — no `tr_phonetic.json` is written, and Phase 7 treats missing files as "lens disabled".
- **Schema is integrated into `static-lens-runner`** — there is no new subagent. If `schema` is unselected, the runner writes a skipped placeholder (`{"findings": [], "applicable": null, "skipped": true, "reason": "lens not selected in per-run wizard"}`) to `$RUN_DIR/schema.json`. If the prompt has no numbered section headings, the runner writes `{"findings": [], "applicable": false, "reason": "no numbered section headings detected"}` — auto-skip is the runner's decision, not the skill's.

Phase 7 already handles missing per-lens JSON files as "lens disabled" — no change there.

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_3_5_end" >> "$TIMING_LOG"
```

## Phases 4 + 6 — Parallel lens dispatch (5 concurrent Agent calls)

Five lens runs fan out in ONE assistant turn, in five parallel `Agent` calls:

```
  ┌─ conflict   (static-lens-runner, selected_lenses=["conflict"])
  ├─ dominance  (static-lens-runner, selected_lenses=["dominance"])
  ├─ gap        (static-lens-runner, selected_lenses=["gap"])         ├─→ drift-runner (Phase 5; needs conflicts/gaps/dominances)
  ├─ schema     (static-lens-runner, selected_lenses=["schema"])
  └─ tr_phonetic (tr-phonetic-runner; conditional on user_intent.tr_phonetic_enabled)
```

CRITICAL: emit ALL FIVE Agent tool calls in a single assistant turn so they
run concurrently — NOT in five separate turns. Concurrent calls land at the
same time; await all five before evaluating Phase 5's gate. Serial dispatch
defeats the parallel design.

If the user deselected some lenses in Phase 3.5, dispatch ONLY the selected
ones. For each unselected static lens, write the skipped-placeholder JSON
directly (no Agent call needed for skipped lenses).

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_4_dispatch_start" >> "$TIMING_LOG"
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_6_dispatch_start" >> "$TIMING_LOG"
```

Detection criteria for the four static lenses (conflict, dominance, gap, schema) live in `references/lens-rules.md` — `static-lens-runner` reads that document. The TR phonetic criteria live in `references/tr-phonetic.md` — `tr-phonetic-runner` reads that document. The skill itself does no lens analysis.

**Line-number contract:** every `line` field each subagent writes is a body.txt index (1-indexed, blank lines included). Phase 7 is the single place that translates these to original-file line numbers.

**Dispatch shape — emit all five Agent calls in ONE turn:**

```
Agent({
  subagent_type: "static-lens-runner",
  prompt: JSON.stringify({
    inputs: {
      body:            "<absolute path to $RUN_DIR/body.txt>",
      frontmatter:     "<absolute path to $RUN_DIR/frontmatter.json>",
      rules:           "<absolute path to $RUN_DIR/rules.json>",
      lens_rules_ref:  "<absolute path to skills/prompt-check/references/lens-rules.md>",
      selected_lenses: ["conflict"],
      compact_mode:    <bool from frontmatter.compact_mode>,
      max_char_limit:  <int from frontmatter.max_char_limit>,
      section_index:   "<absolute path to $RUN_DIR/section_index.json>",
      report_language: <string from frontmatter.report_language, default "tr">
    },
    output_paths: {
      conflicts:  "<absolute path to $RUN_DIR/conflicts.json>"
    }
  }),
  description: "conflict lens for " + BASENAME,
  isolation: "worktree"
})

Agent({
  subagent_type: "static-lens-runner",
  prompt: JSON.stringify({
    inputs: { ..., selected_lenses: ["dominance"], ..., report_language: <string from frontmatter.report_language, default "tr"> },
    output_paths: { dominances: "<absolute path to $RUN_DIR/dominances.json>" }
  }),
  description: "dominance lens for " + BASENAME,
  isolation: "worktree"
})

Agent({
  subagent_type: "static-lens-runner",
  prompt: JSON.stringify({
    inputs: { ..., selected_lenses: ["gap"], ..., report_language: <string from frontmatter.report_language, default "tr"> },
    output_paths: { gaps: "<absolute path to $RUN_DIR/gaps.json>" }
  }),
  description: "gap lens for " + BASENAME,
  isolation: "worktree"
})

Agent({
  subagent_type: "static-lens-runner",
  prompt: JSON.stringify({
    inputs: { ..., selected_lenses: ["schema"], ..., report_language: <string from frontmatter.report_language, default "tr"> },
    output_paths: { schema: "<absolute path to $RUN_DIR/schema.json>" }
  }),
  description: "schema lens for " + BASENAME,
  isolation: "worktree"
})

Agent({
  subagent_type: "tr-phonetic-runner",
  prompt: JSON.stringify({
    inputs: { ..., report_language: <string from frontmatter.report_language, default "tr"> },
    ...
  }),
  description: "tr phonetic lens for " + BASENAME,
  isolation: "worktree"
})
```

**Every runner receives `report_language` so it produces native-language `rationale` + `suggested_fix` directly. The render layer no longer translates content fields (only section headings and lens labels translate via `TEMPLATE_STRINGS`).**

Each Agent call uses `isolation: "worktree"` — the runner gets its own clean
git worktree. Output JSONs land at the per-lens path; the runner returns
when its single output file is written. The skill awaits all five returns
before Phase 5's drift gate.

**Per-lens runner contract.** The `static-lens-runner` accepts the same `inputs` schema (`body`, `frontmatter`, `rules`, `lens_rules_ref`, `selected_lenses`, `compact_mode`, `max_char_limit`, `section_index`, `report_language`) and `output_paths` (any subset of `conflicts`, `dominances`, `gaps`, `schema`). The change is in HOW the skill calls it: FOUR singleton dispatches instead of one combined call with all four lenses in `selected_lenses`. The runner still handles a multi-lens `selected_lenses` array (backward compat) — singleton-per-call is the new RECOMMENDED dispatch shape.

**`report_language` propagation (mandatory).** Every Phase 4 / 5 / 6 Agent call MUST pass `report_language` from `frontmatter.report_language` in its `inputs` block. Runners write `rationale` + `suggested_fix` (and TR `pronunciation_entry.note`) directly in this language — there is NO post-render translation in Phase 7. Skipping this field on dispatch forces the runner into the EN fallback with a warning.

`compact_mode` and `max_char_limit` are additive — the runner uses them when `compact_mode == true`, ignores them otherwise (backward compat). `tr-phonetic-runner` is already line-level / cheap, so `compact_mode` has no effect on its analysis; the fields are passed for symmetry.

**Per-finding output:** each singleton dispatch writes ONLY its own lens output file. The conflict dispatch writes `conflicts.json` and ignores `dominances` / `gaps` / `schema`. The dominance dispatch writes `dominances.json`. The gap dispatch writes `gaps.json`. The schema dispatch writes `schema.json`. Empty placeholder files are still required for lenses the user deselected (see below).

**`selected_lenses` resolution.** For each of the four static lenses, dispatch a singleton call ONLY IF the lens is in `user_intent.selected_lenses` (computed in Phase 3.5). For each lens NOT in the selection, DO NOT dispatch — instead write the skipped-placeholder JSON directly:

```bash
# conflict deselected
printf '{"conflicts": [], "skipped": true, "reason": "lens not selected in per-run wizard"}' > "$RUN_DIR/conflicts.json"
# dominance deselected
printf '{"dominances": [], "skipped": true, "reason": "lens not selected in per-run wizard"}' > "$RUN_DIR/dominances.json"
# gap deselected
printf '{"gaps": [], "skipped": true, "reason": "lens not selected in per-run wizard"}' > "$RUN_DIR/gaps.json"
# schema deselected
printf '{"findings": [], "skipped": true, "reason": "lens not selected in per-run wizard"}' > "$RUN_DIR/schema.json"
```

Schema additionally auto-skips on flat prompts (no numbered section headings) — when the lens IS selected, the runner inspects the body and emits `{"findings": [], "applicable": false, "reason": "no numbered section headings detected"}` to `schema.json` if no numbered sections exist. Auto-skip is the runner's decision, not the skill's.

If ALL four static lenses are deselected, no static-lens-runner dispatches happen — just the four placeholder writes above. Proceed to the tr-phonetic dispatch and Phase 5 / 7 without spawning any static-lens-runner.

**Phase 6 (tr-phonetic) dispatch — fifth concurrent call.** Gated on `user_intent.tr_phonetic_enabled == true` (the runtime authoritative value from Phase 3.5 — NOT `frontmatter.tr_phonetic`). If the gate fails, the fifth call is omitted; Phase 7 treats the missing `tr_phonetic.json` as "TR lens disabled". The full tr-phonetic dispatch shape (with `tr_phonetic_ref`, `user_intent_tr_phonetic`, `section_index`, etc.) lives in the Phase 6 detail section below — emit the same payload, just in the same turn as the four static dispatches.

**Phase 5 dispatch trigger:** as soon as `conflicts.json` AND `gaps.json` AND `dominances.json` all exist on disk (the three static lenses drift needs as inputs), dispatch `drift-runner`. Do NOT wait for `schema.json` or `tr_phonetic.json` — drift is independent of those.

`static-lens-runner` writes `$RUN_DIR/conflicts.json`, `$RUN_DIR/dominances.json`, `$RUN_DIR/gaps.json`, or `$RUN_DIR/schema.json` depending on which singleton call dispatched it. `tr-phonetic-runner` writes `$RUN_DIR/tr_phonetic.json`. The skill reads them in Phase 7 after awaiting ALL pending dispatches (the five lens runs plus drift, if drift was triggered).

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_4_dispatch_end" >> "$TIMING_LOG"
```

## Phase 5 — Drift (conditional)

**Orchestration:** dispatched as soon as the THREE static lenses drift needs as inputs have landed — `conflicts.json` AND `gaps.json` AND `dominances.json` all exist on disk. Drift does NOT wait for `schema.json` or `tr_phonetic.json` to finish — schema and tr_phonetic are independent of drift. Runs **in parallel with whichever lens dispatches are still in-flight** (typically schema + tr_phonetic). Phase 7 awaits all in-flight subagents (the five Phase 4+6 lens runs + Phase 5 drift) before rendering.

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
      expand_count_override: 3,  // ← from user_intent.expand_count; takes precedence over frontmatter
      compact_mode:          <bool from frontmatter.compact_mode>,
      max_char_limit:        <int from frontmatter.max_char_limit>,
      section_index:         "<absolute path to $RUN_DIR/section_index.json>",
      report_language:       <string from frontmatter.report_language, default "tr">
    },
    output_path: "<absolute path to $RUN_DIR/drift.json>"
  }),
  description: "drift analysis for " + BASENAME,
  isolation: "worktree"
})
```

Populate `expand_count_override` with `user_intent.expand_count` from Phase 3.5 (the per-run integer the user picked in the lens-selection wizard). The drift-runner uses this override when present, falling back to `frontmatter.expand_count` only when the override is absent.

**Compact mode:** when `compact_mode == true`, drift-runner halves the final `expand_count` (max(1, n//2)) so adversarial scenario simulation cost drops linearly with prompt size. The override-chain still applies first — `expand_count_override` from user_intent is the value drift-runner halves.

`drift-runner` generates scenarios, simulates the model on each, judges outputs, and writes `$RUN_DIR/drift.json` with shape `{scenarios, runs, verdicts}`. The skill never decomposes drift inline because it is the only step whose token cost scales with prompt length.

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_5_end" >> "$TIMING_LOG"
```

## Phase 6 — Turkish phonetic lens (conditional — dispatched in the same turn as Phase 4)

**Run this phase only if `user_intent.tr_phonetic_enabled == true`** (the runtime value computed in Phase 3.5 — NOT `frontmatter.tr_phonetic`). Frontmatter's `tr_phonetic` is the default that pre-selects the wizard checkbox; the user can flip the selection on or off in Phase 3.5, and that runtime decision is authoritative here. Otherwise skip — `tr_phonetic.json` is not written, and Phase 7 treats the absence as "TR lens disabled".

**Dispatch ordering:** Phase 6's `tr-phonetic-runner` call is the FIFTH parallel Agent call emitted in the same assistant turn as the four Phase 4 static lens dispatches (see "Phases 4 + 6 — Parallel lens dispatch" above). The Phase 6 detail below documents the per-runner contract; the dispatch shape (along with `isolation: "worktree"`) is part of the combined Phase 4+6 fan-out.

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_6_skip" >> "$TIMING_LOG"
```

When the gate passes, `tr-phonetic-runner` seeds `pronunciation_map` from any existing pronunciation guide block in the body, scans for new findings, dedupes against the seed, and writes a single `$RUN_DIR/tr_phonetic.json`. Every rule (skip rules, whitelist, strategy semantics, the "no semantic translation" hard rule, the three `fix_kind` values, the seed block formats and line-range tracking) lives in `references/tr-phonetic.md` — the subagent reads that document; the skill does not repeat the criteria here.

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
      user_intent_tr_phonetic: true,  // ← from user_intent.tr_phonetic_enabled; runtime authoritative
      compact_mode:          <bool from frontmatter.compact_mode>,
      max_char_limit:        <int from frontmatter.max_char_limit>,
      section_index:         "<absolute path to $RUN_DIR/section_index.json>",
      report_language:       <string from frontmatter.report_language, default "tr">
    },
    output_path: "<absolute path to $RUN_DIR/tr_phonetic.json>"
  }),
  description: "TR phonetic lens for " + BASENAME,
  isolation: "worktree"
})
```

Populate `user_intent_tr_phonetic` with `user_intent.tr_phonetic_enabled`. The runner's defensive guard checks this field instead of `frontmatter.tr_phonetic`, so user runtime overrides survive even when the frontmatter default would have suppressed the lens.

TR phonetic is already line-level / cheap; `compact_mode` has no effect on its analysis. The fields are passed for symmetry and future use (logging, telemetry).

**Section_index propagation (applies to every runner — Phase 4 / 5 / 6).** Every runner receives `section_index` so it can attach a `section_ref` to each finding via line lookup. Runners that don't need section context still receive the path — they simply ignore it. Findings without a section context get `section_ref: null`. Runs from versions before this field existed (no `section_index.json` on disk) are handled gracefully: runners that find the file missing skip the lookup and emit `section_ref: null` on every finding; Phase 7's renderer falls back to bare line markers.

`tr-phonetic-runner` writes `$RUN_DIR/tr_phonetic.json` with shape `{ findings[], seed_entries[], warnings[] }`. The skill reads it in Phase 7.

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_6_dispatch_end" >> "$TIMING_LOG"
```

## Phase 7 — Render outputs

```bash
[ "$PROMPTCHECKER_TIMING" = "true" ] && echo "[$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')] phase_7_start" >> "$TIMING_LOG"
```

**Await all pending dispatches first.** Before reading any per-lens output, block until ALL in-flight Agent calls have returned: the four Phase 4 static-lens-runner singletons (conflict / dominance / gap / schema), the Phase 6 tr-phonetic-runner (if its gate passed), and the Phase 5 drift-runner (if its gate passed). Phase 7 is the synchronisation barrier — do not start reading per-lens JSONs until every dispatched runner has signalled completion (its output file is written).

Read every artefact that landed in `$RUN_DIR/` so far: `frontmatter.json`, `rules.json`, `conflicts.json`, `dominances.json`, `gaps.json`, `schema.json`, `drift.json`, and (if the TR gate ran) `tr_phonetic.json`. Build a single merged `findings.json` and a human-readable `report.md`. Both are line-anchored.

**Schema findings merging.** After loading `conflicts.json`, `dominances.json`, `gaps.json`, `drift.json`, and `tr_phonetic.json`, ALSO load `schema.json` and merge with the following rules:

- **If `schema.json.applicable == false`** (auto-skipped on flat prompt):
  - Do not add any schema findings to `findings[]`.
  - Set `summary.schema = { "total": 0, "applicable": false, "reason": "no numbered section headings detected" }`.
- **If `schema.json.skipped == true`** (user deselected the lens in Phase 3.5):
  - Do not add any schema findings to `findings[]`.
  - Set `summary.schema = { "total": 0, "applicable": null, "skipped": true, "reason": "lens not selected in per-run wizard" }`.
- **If `schema.json.applicable == true`:**
  - For each finding in `schema.json.findings`, promote to `findings[]` with:
    - `lens: "schema"`
    - `fix_kind: "replace"` (the runner sets `fix_strategy` per category — `section_gap`, `subsection_gap`, `out_of_order`, `subsection_orphan`, `heading_style_inconsistent`, `missing_parent`, `step_gap`, etc.)
    - All other fields verbatim from the schema finding (`severity`, `line`, `related_lines`, `current_excerpt`, `suggested_fix`, `rationale`, `rule_ids`, `fix_strategy`).
  - Translate the `line` field via `body_line_offset`, exactly as for every other lens.
  - Set `summary.schema = { "total": N, "applicable": true, "by_kind": { "section_gap": ..., "subsection_gap": ..., "out_of_order": ..., ... }, "high": ..., "medium": ..., "low": ... }`.

**Dominance metadata invariant (mandatory).** After loading `dominances.json` and before line translation, validate every dominance finding has `dominant_rule_id != dominated_rule_id`. A rule cannot dominate itself — when the runner emits one, it indicates the rubric collapsed two distinct rule references into one ID (a known runner failure mode). For each violation:

1. Downgrade the finding's `severity` to `"low"` (preserve the finding so the author still sees it; do not drop silently).
2. Append a warning to the top-level `findings.json.warnings[]` array: `"D<n>: dominant_rule_id == dominated_rule_id (R<x>) — severity downgraded to low; re-examine the runner output"`.
3. Tag the finding object with `metadata_invariant_violation: true` so downstream tooling (Phase 9 render, Phase 10 apply) can surface a distinct visual cue.

Apply this validation BEFORE line translation — operating on raw runner output keeps the failure-mode signal close to its source. The downgraded finding still flows through the rest of Phase 7 (line translation, section_ref attachment, render) like any other finding.

**Line translation (mandatory):** Every lens wrote `line` numbers as body.txt indices. Before writing findings.json, translate each `line` to an original-file line:

```
original_line = body_line + frontmatter.body_line_offset - 1
```

Apply this to every `findings[].line` and `findings[].related_lines[]`. After translation, body.txt indices must no longer appear in any rendered output. Phase 9 (summary view), Phase 10 (applied step), and `report.md` all depend on this.

**Carry the prompt hash:** copy `frontmatter.prompt_sha256` into the top of findings.json so Phase 10's applied step can detect a stale audit and auto-route to overlay.

**Section_ref attachment (mandatory):** For every finding, attach a `section_ref` object using `section_index.json`. Look up `finding.line` (the post-translation original-file line — but `section_index.json` was built from body.txt, so do the lookup BEFORE applying `body_line_offset`, or apply the offset to the index ranges first; the simpler path is to record `section_ref` while still in body.txt coordinates, then translate `line` / `related_lines` independently — `section_ref` carries no line numbers itself). When `section_index.applicable == false` OR the line falls outside every range, emit `section_ref: null`. Never omit the field — explicit null signals "line outside any numbered section".

For backward compatibility: if `section_index.json` does not exist (older run dirs from before this feature), set `section_ref: null` for every finding without crashing. The renderer below also falls back to bare line markers when `section_ref` is null.

**Language switching (mandatory):** Load `frontmatter.report_language` (`tr` or `en`; default `tr` — Phase 2 already normalised it). Use this as the key into `TEMPLATE_STRINGS` (defined below). Apply throughout the report.md render: title, top metadata labels, summary heading + column headers + lens row labels, findings heading, findings-table column headers, section/line cell prefixes, per-row lens + severity labels, drift `— / —` no-line marker, drift `passed — no fix` cell, and TODO / Intentional sentinel cell replacements.

```
TEMPLATE_STRINGS = {
  "tr": {
    "report_title": "PromptChecker Raporu — {basename}",
    "prompt_label": "**Prompt:**",
    "run_label": "**Çalıştırma:**",
    "generated_label": "**Oluşturulma:**",
    "target_model_label": "**Hedef model:**",
    "summary_heading": "## Özet",
    "lens_column": "Mercek",
    "total_column": "Toplam",
    "high_column": "Yüksek",
    "medium_column": "Orta",
    "low_column": "Düşük",
    "conflict_row": "Çelişki",
    "dominance_row": "Baskınlık",
    "gap_row": "Boşluk",
    "schema_row": "Şema",
    "drift_row": "Davranışsal sapma",
    "tr_phonetic_row": "Türkçe fonetik",
    "findings_heading": "## Bulgular",
    "high_severity_heading": "### Yüksek önem",
    "medium_severity_heading": "### Orta önem",
    "low_severity_heading": "### Düşük önem",
    "conflicts_subheading": "#### Çelişkiler",
    "dominances_subheading": "#### Baskınlıklar",
    "gaps_subheading": "#### Boşluklar",
    "schema_subheading": "#### Şema",
    "drift_subheading": "#### Davranışsal sapma",
    "tr_phonetic_subheading": "#### Türkçe fonetik",
    "none_marker": "_(yok)_",
    "section_prefix": "Bölüm",
    "line_prefix": "Satır",
    "lens_label_conflict": "çelişki",
    "lens_label_dominance": "baskınlık",
    "lens_label_gap": "boşluk",
    "lens_label_schema": "şema",
    "lens_label_drift": "davranışsal sapma",
    "lens_label_tr_phonetic": "türkçe fonetik",
    "severity_label_high": "yüksek",
    "severity_label_medium": "orta",
    "severity_label_low": "düşük",
    "table_id_column": "id",
    "table_lens_column": "mercek",
    "table_severity_column": "önem",
    "table_section_line_column": "bölüm / satır",
    "table_rationale_column": "açıklama",
    "table_fix_column": "düzeltme",
    "drift_passed_no_fix": "(geçti — düzeltme yok)",
    "sentinel_todo_render": "_TODO: {text}_",
    "sentinel_intentional_render": "_Intentional — atla_",
    "no_line_marker": "— / —",
    "no_section_marker": "—"
  },
  "en": {
    "report_title": "PromptChecker Report — {basename}",
    "prompt_label": "**Prompt:**",
    "run_label": "**Run:**",
    "generated_label": "**Generated:**",
    "target_model_label": "**Target model:**",
    "summary_heading": "## Summary",
    "lens_column": "Lens",
    "total_column": "Total",
    "high_column": "High",
    "medium_column": "Medium",
    "low_column": "Low",
    "conflict_row": "Conflict",
    "dominance_row": "Dominance",
    "gap_row": "Gap",
    "schema_row": "Schema",
    "drift_row": "Drift",
    "tr_phonetic_row": "TR phonetic",
    "findings_heading": "## Findings",
    "high_severity_heading": "### HIGH severity",
    "medium_severity_heading": "### MEDIUM severity",
    "low_severity_heading": "### LOW severity",
    "conflicts_subheading": "#### Conflicts",
    "dominances_subheading": "#### Dominances",
    "gaps_subheading": "#### Gaps",
    "schema_subheading": "#### Schema",
    "drift_subheading": "#### Drift",
    "tr_phonetic_subheading": "#### TR phonetic",
    "none_marker": "_None._",
    "section_prefix": "Section",
    "line_prefix": "L",
    "lens_label_conflict": "conflict",
    "lens_label_dominance": "dominance",
    "lens_label_gap": "gap",
    "lens_label_schema": "schema",
    "lens_label_drift": "drift",
    "lens_label_tr_phonetic": "tr phonetic",
    "severity_label_high": "high",
    "severity_label_medium": "medium",
    "severity_label_low": "low",
    "table_id_column": "id",
    "table_lens_column": "lens",
    "table_severity_column": "sev",
    "table_section_line_column": "section / line",
    "table_rationale_column": "rationale",
    "table_fix_column": "fix",
    "drift_passed_no_fix": "(passed — no fix)",
    "sentinel_todo_render": "_TODO: {text}_",
    "sentinel_intentional_render": "_Intentional — dismiss_",
    "no_line_marker": "— / —",
    "no_section_marker": "—"
  }
}
```

Lens-generated content (`rationale`, `suggested_fix`, `current_excerpt`) is NOT translated — it stays in whatever language the runner produced. Only the skill-side template strings above switch language. So an English conflict rationale remains English in a TR report; the surrounding section headings and labels translate.

**Table-format finding render (mandatory).** Findings render as a markdown TABLE — one row per finding. NO bullet lists, NO blockquote sentinel lines under bullets, NO `→` arrow separators. The table is the primary output of Phase 7 and Phase 9.

**Columns per `report_language`:**

- `tr` (default):
  ```
  | id | mercek | önem | bölüm / satır | açıklama | düzeltme |
  ```
- `en`:
  ```
  | id | lens | sev | section / line | rationale | fix |
  ```

The column header strings come from `TEMPLATE_STRINGS[lang]` (`table_id_column`, `table_lens_column`, `table_severity_column`, `table_section_line_column`, `table_rationale_column`, `table_fix_column`).

**Per-cell content rules:**

- **`id`** — the finding id verbatim (`C1`, `D3`, `G2`, `S1`, `T1`, `drift-S3`).
- **`lens`** (= `mercek` in TR) — translated per `TEMPLATE_STRINGS[lang]["lens_label_<lens>"]`:
  - `conflict` → `çelişki` (tr) / `conflict` (en)
  - `dominance` → `baskınlık` / `dominance`
  - `gap` → `boşluk` / `gap`
  - `schema` → `şema` / `schema`
  - `drift` → `davranışsal sapma` / `drift`
  - `tr_phonetic` → `türkçe fonetik` / `tr phonetic`
- **`sev`** (= `önem` in TR) — translated per `TEMPLATE_STRINGS[lang]["severity_label_<sev>"]`:
  - `high` → `yüksek` / `high`
  - `medium` → `orta` / `medium`
  - `low` → `düşük` / `low`
- **`section / line`** (= `bölüm / satır` in TR) — the section-aware location, single cell:
  - When `section_ref.subsection` is present: `Bölüm 7.2 / Satır 284` (tr) / `Section 7.2 / L284` (en)
  - When `section_ref` is non-null but `subsection` is null: `Bölüm 7 / Satır 284` (tr) / `Section 7 / L284` (en)
  - When `section_ref` is null AND `finding.line` is non-null (line outside numbered section, e.g. preamble): `Satır 284` (tr) / `L284` (en)
  - **Drift `— / —` rule:** when BOTH `finding.line == null` AND `finding.section_ref == null` (drift findings are behavioural — they have no anchored line), render this cell as the literal string `TEMPLATE_STRINGS[lang]["no_line_marker"]` (= `— / —`). NEVER `Satır 1` / `L1`. The em-dash + slash + em-dash signals "no anchor".
  - The prefixes (`Bölüm` / `Section`, `Satır` / `L`) come from `TEMPLATE_STRINGS[lang]["section_prefix"]` and `TEMPLATE_STRINGS[lang]["line_prefix"]`.
- **`rationale`** (= `açıklama` in TR) — the finding's `rationale` field VERBATIM. NO truncation. NO ellipsis. NO first-sentence extraction. Runners are responsible for keeping rationale ≤ 200 characters (per their own "Compact writing" invariants); the renderer uses the full text as-is. If the runner exceeded the cap, that is a runner error — surface as-is, do not paper over it.
- **`fix`** (= `düzeltme` in TR) — the finding's `suggested_fix` field VERBATIM. NO truncation. Special-case rendering:
  - When `suggested_fix` is null or empty AND the finding is a drift verdict (`lens == "drift"`) — render `TEMPLATE_STRINGS[lang]["drift_passed_no_fix"]` (= `(geçti — düzeltme yok)` / `(passed — no fix)`).
  - When `suggested_fix` starts with `"TODO:"` — render `TEMPLATE_STRINGS[lang]["sentinel_todo_render"]` (= `TODO — yazar çözmeli` / `TODO — author must resolve`).
  - When `suggested_fix` starts with `"Intentional —"` or `"Intentional -"` — render `TEMPLATE_STRINGS[lang]["sentinel_intentional_render"]` (= `Bilinçli — atla` / `Intentional — dismiss`).
  - Otherwise — render `suggested_fix` verbatim. Runners cap fix text at ≤ 150 chars; renderer uses the full text.

**Markdown table escaping:** pipe characters (`|`) inside a cell value would break the table layout. Escape every `|` in rationale / fix / current_excerpt content as `\|` (or substitute with `│` if you prefer the cosmetic Unicode bar). Apply line breaks (`\n`) inside a cell as `<br>`. These escapes are render-only; `findings.json` keeps the raw text.

**Sort order inside the table** follows the global sort (severity descending, then lens group, then line ascending). Severity groups are NOT broken into separate sub-tables — the single table is sorted, and the column values speak for themselves.

**The full rationale + suggested_fix remain available in findings.json verbatim (no escaping there).** The runner emits compact text; the renderer pipes it through pipe-escape + line-break-escape for the markdown table only.

Python helper:

```python
def escape_cell(text):
    if not text:
        return ""
    return text.replace("|", "\\|").replace("\n", "<br>")

def build_section_line_cell(f, T):
    sref = f.get("section_ref")
    line = f.get("line")
    # Drift "— / —" rule: both line and section_ref are null.
    if line is None and sref is None:
        return T["no_line_marker"]
    sec_p = T["section_prefix"]
    line_p = T["line_prefix"]
    if sref is not None and sref.get("subsection"):
        return f"{sec_p} {sref['subsection']} / {line_p}{line}"
    if sref is not None and sref.get("section"):
        return f"{sec_p} {sref['section']} / {line_p}{line}"
    # section_ref null but line present (preamble line)
    return f"{line_p}{line}"

def render_fix_cell(f, T):
    suggested = f.get("suggested_fix") or ""
    if not suggested.strip():
        if f.get("lens") == "drift":
            return T["drift_passed_no_fix"]
        return ""
    if suggested.startswith("TODO:"):
        return T["sentinel_todo_render"]
    if suggested.startswith("Intentional —") or suggested.startswith("Intentional -"):
        return T["sentinel_intentional_render"]
    return suggested  # verbatim — no truncation

def render_finding_row(f, lang):
    T = TEMPLATE_STRINGS[lang]
    lens_label = T[f"lens_label_{f['lens']}"]
    sev_label = T[f"severity_label_{f.get('severity','low')}"]
    section_line = build_section_line_cell(f, T)
    rationale = escape_cell(f.get("rationale", ""))
    fix = escape_cell(render_fix_cell(f, T))
    return f"| {f['id']} | {lens_label} | {sev_label} | {section_line} | {rationale} | {fix} |"

def render_findings_table(findings, lang):
    T = TEMPLATE_STRINGS[lang]
    header = (
        f"| {T['table_id_column']} | {T['table_lens_column']} | {T['table_severity_column']} "
        f"| {T['table_section_line_column']} | {T['table_rationale_column']} | {T['table_fix_column']} |"
    )
    sep = "|---|---|---|---|---|---|"
    rows = [render_finding_row(f, lang) for f in findings]
    return "\n".join([header, sep] + rows)
```

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
    "schema": {
      "total": N,
      "applicable": true,
      "by_kind": { "section_gap": N, "subsection_gap": N, "out_of_order": N, "subsection_orphan": N, "heading_style_inconsistent": N, "missing_parent": N, "step_gap": N },
      "high": N, "medium": N, "low": N
    },
    "drift": { "scenarios": N, "passed": N, "failed": N, "skipped": false },
    "tr_phonetic": { "total": N, "by_kind": { ... } }
  },
  "findings": [
    {
      "id": "C1",
      "lens": "conflict|dominance|gap|schema|drift|tr_phonetic",
      "fix_kind": "replace|advisory",
      "severity": "low|medium|high",
      "line": 42,
      "section_ref": {
        "section": "7",
        "subsection": "7.2",
        "section_title": "VALUE FRAMING AXES",
        "subsection_title": "MÜBADELE VALUE HIERARCHY"
      },
      "related_lines": [42, 47],
      "current_excerpt": "<verbatim from body.txt>",
      "suggested_fix": "<concrete edit — populated only for fix_kind: replace>",
      "pronunciation_entry": null,
      "rationale": "<runner-emitted, ≤ 200 chars per the runner's Compact writing invariant>",
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

`section_ref` is a new top-level field on EVERY finding. Shape: `{"section": "<n>", "subsection": "<n.m> | null", "section_title": "<str | null>", "subsection_title": "<str | null>"}`. When the finding's line falls outside every numbered section (e.g. preamble before SECTION 1), emit `section_ref: null` explicitly — absent field is ambiguous; explicit null signals "line outside any numbered section". Lookup is via `section_index.json` (built in Phase 3). When `section_index.json` does not exist on disk (runs from older versions), set `section_ref: null` on every finding and let the renderer fall back to bare line markers.

**`summary.schema` shape variants** depend on the lens state and follow the Schema findings merging rules above:

```json
// Applicable run (numbered sections detected, lens enabled):
"schema": { "total": N, "applicable": true, "by_kind": { ... }, "high": N, "medium": N, "low": N }

// Auto-skipped (flat prompt, no numbered headings):
"schema": { "total": 0, "applicable": false, "reason": "no numbered section headings detected" }

// User-deselected in Phase 3.5 wizard:
"schema": { "total": 0, "applicable": null, "skipped": true, "reason": "lens not selected in per-run wizard" }
```

**Sort order for findings[] and rendered output:**

1. **Severity descending** — high (h) → medium (m) → low (l). High-severity findings appear first regardless of where they are in the file.
2. **Lens group within severity bucket** — within each severity bucket, group by lens in this fixed order: conflict, dominance, gap, schema, drift, tr_phonetic. (Schema sits between gap and drift — it's another static structural lens; drift is the only behavioural lens.)
3. **Line ascending within (severity, lens) group** — within the same severity AND lens, sort by line number ascending.

In Python:
```python
LENS_ORDER = {"conflict": 0, "dominance": 1, "gap": 2, "schema": 3, "drift": 4, "tr_phonetic": 5}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
findings.sort(key=lambda f: (
    SEVERITY_ORDER.get(f.get("severity", "low"), 2),
    LENS_ORDER.get(f["lens"], 99),
    f.get("line", 0)
))
```

Apply this sort BOTH to `findings.json.findings[]` AND to the visual rendering in report.md.

For each finding:
- `fix_kind: "replace"` → Phase 10's applied step rewrites the line so it produces `suggested_fix` instead of `current_excerpt`. Emitted by `conflict`, `dominance`, `gap`, `schema`, `drift` lenses, and by TR `number_readability` / `punctuation` findings.
- `fix_kind: "advisory"` → no automatic apply. Emitted by TR `foreign_word` / `abbreviation` findings — Phase 9.6's commit and Phase 10.3's TR advisory guard force-route these to overlay.

### `$RUN_DIR/report.md`

The template skeleton below is shown in `en` (English) for readability; the actual rendered output uses `TEMPLATE_STRINGS[frontmatter.report_language]` for every label. The structural layout (heading levels, summary table columns, findings table columns, row sort order) is identical across both languages — only the text translates.

```markdown
# <report_title — TEMPLATE_STRINGS.report_title with {basename} interpolation>

- <prompt_label> `<absolute path>`
- <run_label> `<run-NNN>`
- <generated_label> <ISO 8601>
- <target_model_label> <target_model>

<summary_heading>

| <lens_column> | <total_column> | <high_column> | <medium_column> | <low_column> |
|---|---|---|---|---|
| <conflict_row> | … | … | … | … |
| <dominance_row> | … | … | … | … |
| <gap_row> | … | … | … | … |
| <schema_row> | … | … | … | … | (render `<schema_row> | 0 (not applicable — no numbered section headings)` when `applicable: false`; render `<schema_row> | 0 (skipped — lens deselected)` when `applicable: null && skipped: true`)
| <drift_row> | <scenarios>: <passed>✓ / <failed>✗ | — | — | — |
| <tr_phonetic_row> | … | … | … | … | (omit row if tr_phonetic disabled)

<findings_heading>

| <table_id_column> | <table_lens_column> | <table_severity_column> | <table_section_line_column> | <table_rationale_column> | <table_fix_column> |
|---|---|---|---|---|---|
| C1 | <lens_label_conflict> | <severity_label_high> | <section_prefix> 7.2 / <line_prefix>284 | <rationale verbatim> | <suggested_fix verbatim> |
| drift-S1 | <lens_label_drift> | <severity_label_low> | — / — | <rationale verbatim> | <drift_passed_no_fix> |
| ... |
```

Findings render as a **single markdown table**, sorted by (severity desc, lens group order, line asc). NO bullet lists, NO severity sub-headings, NO per-lens sub-headings, NO `none_marker` placeholders — empty severity / lens combinations simply produce no row.

Each row is built by `render_finding_row` (see "Table-format finding render" above). Cells are populated VERBATIM from the runner's `rationale` / `suggested_fix` text; pipe (`|`) and newline (`\n`) characters are escaped for markdown table safety but otherwise the text is unchanged. Runners are responsible for keeping rationale ≤ 200 chars and fix ≤ 150 chars (per their "Compact writing" invariants) — the renderer does NOT truncate.

**Drift row special-cases:**
- When `drift.json.skipped_reason` is set (drift skipped at run level), do NOT add per-scenario rows. Instead append a single italic line ABOVE the table: `_<drift_subheading>: skipped — <skipped_reason>._`
- When a drift scenario has BOTH `line == null` AND `section_ref == null`, the `section / line` cell renders as `— / —` (= `no_line_marker`). NEVER `Satır 1` / `L1`.
- When a drift scenario passed AND `suggested_fix` is empty, the `fix` cell renders as `<drift_passed_no_fix>` (= `(geçti — düzeltme yok)` / `(passed — no fix)`).
- Treat drift `fail` as high severity, `pass` as low severity for sort placement inside the table.

**Schema lens applicability special-cases (one italic line above the table, NOT a row):**
- When `summary.schema.applicable == false`: prepend `_Schema: not applicable — no numbered section headings detected._` (TR: `_Şema uygulanabilir değil — numaralandırılmış başlık yok._`)
- When `summary.schema.applicable == null && summary.schema.skipped == true`: prepend `_Schema: skipped — lens deselected in wizard._` (TR: `_Şema atlandı — mercek sihirbazda seçilmedi._`)

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

**Language switching.** The Phase 8 summary block also honours `frontmatter.report_language`. The body of the summary (file paths, counts, run identifier) stays the same across languages — only the human-readable labels translate. Two canonical templates follow.

**`en` (English) template:**

```
PromptChecker complete — <run-NNN>

- Rules: <N> | Conflicts: <N> (<H> high) | Dominances: <N> | Gaps: <N> | Schema: <N> (<M> high) [applicability: <APPLICABLE | NOT APPLICABLE | SKIPPED>]
- Drift: <skipped|<N> scenarios, <P> passed, <F> failed>
- TR phonetic: <disabled|<N> findings>
- <K> TR pronunciation findings auto-filed to overlay's pronunciation_map (foreign_word: <a>, abbreviation: <b>)

Report:          <relative path to $RUN_DIR/report.md>
Findings:        <relative path to $RUN_DIR/findings.json>
Session:         <relative path to $RUN_DIR/session.json>
Decisions log:   <relative path to $RUN_DIR/decisions.jsonl>
Overlay:         <relative path to $RUN_DIR/inline-suggestions.md>
Pronunciations:  .promptcheck/<basename>/pronunciations.md (<M> unique terms across <N> runs)
Previous runs: .promptcheck/<basename>/ (run-001 … run-NNN)
Repo defaults: <relative path to .promptchecker.json>
Body size: <body_char_count> chars [compact mode <ACTIVE | inactive — under <max_char_limit> char threshold | DISABLED via max_char_limit=0>]

Entering interactive review (Phase 9). Use a compact decision string such as
  "C1, C3 düzelt; G2 yorum bırak; T1..T5 konuşalım; gerisini atla"
to choose what to do with each finding. Type "iptal" to leave the session as
pending and resume later with /prompt-check-resume.
```

**`tr` (Türkçe) template:**

```
PromptChecker tamamlandı — <run-NNN>

- Kurallar: <N> | Çelişki: <N> (<H> yüksek) | Baskınlık: <N> | Boşluk: <N> | Şema: <N> (<M> yüksek) [uygulanabilir: <AKTİF | DEĞİL | ATLANDI>]
- Davranışsal sapma: <atlandı|<N> senaryo, <P> geçti, <F> kaldı>
- Türkçe fonetik: <devre dışı|<N> bulgu>
- <K> Türkçe telaffuz bulgusu overlay'in pronunciation_map bölümüne otomatik kaydedildi (foreign_word: <a>, abbreviation: <b>)

Rapor:           <relative path to $RUN_DIR/report.md>
Bulgular:        <relative path to $RUN_DIR/findings.json>
Oturum:          <relative path to $RUN_DIR/session.json>
Karar günlüğü:   <relative path to $RUN_DIR/decisions.jsonl>
Overlay:         <relative path to $RUN_DIR/inline-suggestions.md>
Telaffuz ana:    .promptcheck/<basename>/pronunciations.md (<M> benzersiz terim, <N> koşu boyunca)
Önceki koşular: .promptcheck/<basename>/ (run-001 … run-NNN)
Repo varsayılan: <relative path to .promptchecker.json>
Body boyutu: <body_char_count> chars [compact mode <AKTİF | inaktif — <max_char_limit> char eşiğin altında | DEVRE DIŞI (max_char_limit=0)>]

Etkileşimli incelemeye geçiyorum (Faz 9). Karar dizesi olarak şu örneği kullanabilirsin:
  "C1, C3 düzelt; G2 yorum bırak; T1..T5 konuşalım; gerisini atla"
Oturumu beklemeye almak için "iptal" yaz; sonra /prompt-check-resume ile devam edebilirsin.
```

Note that the *example* decision string inside both templates stays Turkish even in `en` mode — the verb tokens (`düzelt`, `yorum bırak`, `konuşalım`, `atla`, `iptal`, `evet`) are the parser's canonical keywords (see `references/dialog-flow.md`). English synonyms (`fix`, `note`, `discuss`, `skip`, `cancel`, `yes`) are still accepted; the example just shows the Turkish form because it is the documented default. Phase 9 dialog prompts in the decision-string ask surface in the chosen language as well, but the verb tokens stay Turkish in both modes.

The auto-filed line is shown ONLY when the count is non-zero. It sits alongside the existing rules / conflicts / dominances / gaps / schema / drift / TR phonetic counts. **The TR phonetic count line still shows the TOTAL TR findings (advisory + replace) — the auto-filed line is an additional drill-down, not a replacement.** Compute `K` as the number of TR findings with `lens == "tr_phonetic" AND fix_kind == "advisory"`; `a` = count where `kind == "foreign_word"`; `b` = count where `kind == "abbreviation"`. These are the same findings that Phase 9.2's partition will assign to AUTO_FILED_SET.

**Schema applicability tag (mirrors `summary.schema` shape from Phase 7):**

- `summary.schema.applicable == true` → render `Schema: <N> (<M> high) [applicability: APPLICABLE]`.
- `summary.schema.applicable == false` → render `Schema: 0 (no numbered section headings detected)` (auto-skipped on a flat prompt). Surface the reason inline; do NOT emit a phantom `[applicability: NOT APPLICABLE]` tag without the reason.
- `summary.schema.applicable == null && summary.schema.skipped == true` → render `Schema: 0 (deselected in wizard)`. Surface the reason inline.

`<M>` is `summary.schema.high` when applicable, otherwise omit the `(<M> high)` parenthetical. The applicability marker mirrors what `static-lens-runner` reported in `schema.json` — the skill does not recompute it.

The `Pronunciations master:` line surfaces `.promptcheck/<basename>/pronunciations.md` — the cross-version aggregate file rebuilt by Phase 10.2.1. `<M>` is the number of unique terms in that file, `<N>` is the count of `run-NNN/` directories under the prompt that have ever contributed at least one `pronunciation_map` entry. **Omit this line entirely** when `pronunciations.md` has zero entries (i.e. no run under this prompt has produced a non-empty `pronunciation_map` yet). The line is informational and only appears when there is something for the user to read.

**Body size / compact mode line — render rules** (pull values from `frontmatter.body_char_count`, `frontmatter.max_char_limit`, `frontmatter.compact_mode`):

- If `compact_mode == true`: `Body size: 87432 chars [compact mode ACTIVE — exceeds 50000 char threshold]`
- If `compact_mode == false AND max_char_limit > 0`: `Body size: 32100 chars [compact mode inactive — under 50000 char threshold]`
- If `max_char_limit == 0`: `Body size: 87432 chars [compact mode DISABLED via max_char_limit=0]`

When compact mode is active, downstream lenses apply cheaper analysis policies — low-severity findings may be skipped, drift simulation halves its scenario budget, and rule extraction trims verbose explanations. To audit at full depth, set `max_char_limit: 0` in `.promptchecker.json` or pass `PROMPTCHECKER_MAX_CHAR_LIMIT=0`.

When `PROMPTCHECKER_TIMING=true`, append one extra line to the summary block above (after the `Body size:` line and before the blank line preceding "Entering interactive review"):

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
    "selected_lenses": ["conflict","dominance","gap","drift","tr_phonetic","schema"],
    "expand_count": 3,
    "anchors": [],
    "tr_phonetic_enabled": true,
    "anchors_added": false,
    "max_char_limit": <int>,
    "compact_mode_active": <bool>,
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

`max_char_limit` and `compact_mode_active` are reflections of frontmatter values (not user wizard inputs at this stage) — recorded in session.json for replay / audit purposes. Copy them verbatim from `frontmatter.max_char_limit` and `frontmatter.compact_mode`.

All timestamps are ISO 8601 UTC with millisecond precision, e.g. `2026-05-27T14:23:45.123Z`.

### 9.2 — Render the summary view

**Partition findings first.** Before constructing the summary table, split `findings.json.findings[]` into two disjoint sets:

- **DECISION_SET** — every finding EXCEPT TR advisory findings. These are the findings the user sees and decides about.
- **AUTO_FILED_SET** — every TR finding with `lens == "tr_phonetic"` AND `fix_kind == "advisory"` (i.e. `kind: foreign_word` or `kind: abbreviation`). These are auto-filed to the overlay's Pronunciation map without a user decision — the user never sees them in the summary table and is never asked about them.

The summary table renders **DECISION_SET only**. The auto_filed processing (see Phase 9.4 stage 1) operates on **AUTO_FILED_SET**.

Print a single markdown table containing every finding in DECISION_SET. The table uses the **same format as Phase 7's report.md table** (`render_findings_table` helper) — one row per finding with the same six columns. Columns per `report_language`:

- `tr`: `| id | mercek | önem | bölüm / satır | açıklama | düzeltme |`
- `en`: `| id | lens | sev | section / line | rationale | fix |`

Sort by the global Phase 7 order (severity desc → lens group → line asc). For drift findings without a severity, treat `fail` as `high` and `pass` as `low` for sort placement.

Cells are populated VERBATIM from the runner's text via the Phase 7 helpers (`build_section_line_cell`, `render_fix_cell`, `escape_cell`). NO truncation, NO `short_*` derivations. The `section / line` cell renders as `— / —` when both `line` and `section_ref` are null (drift). For TR phonetic findings still in DECISION_SET (`number_readability` / `punctuation`), the `fix` cell is the verbatim `suggested_fix` — `pronunciation_entry` rendering is no longer needed in the table because TR advisory findings are filtered out by the partition.

If `AUTO_FILED_SET` is non-empty, append a single line directly below the summary table (before the decision prompt):

```
_Note: <N> TR pronunciation findings (foreign_word + abbreviation) will be auto-filed to the overlay's Pronunciation map. They are not in the table — no decision needed._
```

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
#
# Note: with auto_filed (below), this routed[] branch should rarely fire in
# practice — TR advisory findings are never in DECISION_SET and so the user
# cannot explicitly target them. The branch survives as a defensive guard in
# case a caller bypasses the partition (e.g. a future API entry point) and
# tries to apply a TR advisory finding directly.
routed = []
for i, (fid, status, raw) in enumerate(decisions_resolved):
    if status != 'applied' or findings_state[fid]['lens'] != 'tr_phonetic':
        continue
    finding = findings_by_id.get(fid, {})
    if finding.get('fix_kind') == 'advisory':
        routed.append(fid)
        decisions_resolved[i] = (fid, 'overlay', raw)

# AUTO_FILED_SET — TR advisory findings (foreign_word + abbreviation) that the
# user never sees in the summary table. They are silently routed to overlay
# without a user decision. The plan records them as auto_filed so Stage 2
# (Phase 9.6 commit) can log a distinct `action: "auto_filed"` line.
#
# These do NOT count toward parse errors / unrecognised segments — the user
# didn't type anything for them; they are background routing.
auto_filed_ids = []
auto_filed_kinds = {'foreign_word': 0, 'abbreviation': 0}
for fid, fmeta in findings_by_id.items():
    if fmeta.get('lens') != 'tr_phonetic':
        continue
    if fmeta.get('fix_kind') != 'advisory':
        continue
    if fid in seen_ids:
        # The user explicitly targeted this id — already handled by the
        # routed[] branch above. Don't double-count.
        continue
    auto_filed_ids.append(fid)
    k = fmeta.get('kind')
    if k in auto_filed_kinds:
        auto_filed_kinds[k] += 1

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
    'auto_filed': len(auto_filed_ids),
    'auto_filed_ids': auto_filed_ids,
    'auto_filed_kinds': auto_filed_kinds,
    'unrecognised': unrecognised,
    'decisions': [
        {'finding': fid, 'lens': findings_state[fid]['lens'], 'action': status, 'raw_segment': raw}
        for (fid, status, raw) in decisions_resolved
    ],
}
print(json.dumps(plan, ensure_ascii=False))
PY
```

**Auto-fill contract (in-memory, pseudo-code summarising the heredoc above):**

```
After parsing the user's decision string into a plan for DECISION_SET findings,
add automatic decisions for AUTO_FILED_SET:

for finding in AUTO_FILED_SET:
    plan[finding.id] = {
        "status": "overlay",
        "reason": "auto_filed_tr_advisory",
        "raw_segment": None  // user didn't type anything for this
    }

These do NOT count toward the parse error / unrecognised segment counts.
They are not user decisions; they are background routing.
```

Pass the user's reply as `PROMPTCHECKER_DECISION_INPUT` (or stdin — whichever is cleaner in the harness). The block above is illustrative; trust `references/dialog-flow.md` as the source of truth for the verb table and grammar edge cases.

### 9.5 — Surface the plan and request confirmation (Stage 1.5)

Render the parsed plan back to the user as a clear prose message and ask for confirmation. Do NOT write anything to disk yet.

```
Plan:
  Applied:    <N1>   (user said `düzelt`)
  Overlay:    <N2>   (user said `yorum bırak`)
  Discussed:  <N3>   (user said `konuşalım`)
  Dismissed:  <N4>   (user said `atla`)
  Auto-filed: <N5>   (TR pronunciation findings — no decision needed)
  Parse errors: <N6> (re-prompt if non-zero)

TR auto-redirect: <E> bulgu (<id list>) overlay'e yönlendirilecek (TR findings never modify the prompt).

Onaylıyor musun? (`evet` / `iptal` / yeni karar dizesi)
```

The `Auto-filed` line is shown ONLY when `plan.auto_filed > 0`. It surfaces the count and the per-kind breakdown (`foreign_word: <a>, abbreviation: <b>`) so the user can verify the auto-file scope matches their expectation. The `TR auto-redirect` line above it stays — it covers the rare case where the user explicitly typed a TR advisory id (which the defensive `routed[]` branch handled). Auto-filed findings are NOT user decisions, so they live on their own line.

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

# Look up per-finding kind for the auto_filed log lines.
findings_path = os.path.join(run_dir, 'findings.json')
findings_by_id = {}
if os.path.exists(findings_path):
    try:
        fj = json.load(open(findings_path, encoding='utf-8'))
        for f in fj.get('findings', []):
            findings_by_id[f['id']] = f
    except Exception:
        pass

with open(out_path, 'a', encoding='utf-8') as f:
    # Auto-filed TR advisory findings — one `auto_filed` line per id. The
    # session.json status for these is "overlay" (same as a user-initiated
    # overlay decision); the distinction lives only in this JSONL action field.
    for fid in plan.get('auto_filed_ids', []):
        fmeta = findings_by_id.get(fid, {})
        emit(f, {
            'ts': now(),
            'finding': fid,
            'lens': 'tr_phonetic',
            'action': 'auto_filed',
            'reason': 'TR advisory finding — pronunciation hints are filed to inline-suggestions.md without user decision',
            'fix_kind': 'advisory',
            'kind': fmeta.get('kind'),
        })
    # Routed entries (one per TR-routed id — the defensive branch in 9.4)
    for fid in plan.get('tr_routed_ids', []):
        emit(f, {
            'ts': now(),
            'finding': fid,
            'lens': findings_state[fid]['lens'],
            'action': 'routed_to_overlay',
            'reason': 'TR phonetic findings never modify the prompt file',
        })
    # Then the actual user decisions
    for d in plan.get('decisions', []):
        emit(f, {
            'ts': now(),
            'finding': d['finding'],
            'lens': d['lens'],
            'action': d['action'],
            'source': 'phase_9_decision_string',
            'raw_segment': d.get('raw_segment', ''),
        })

# Update session.json — auto_filed findings collapse to status: "overlay"
# (same persistent status as user-overlay decisions; only decisions.jsonl
# distinguishes them via the action field).
for fid in plan.get('auto_filed_ids', []):
    if fid in findings_state:
        findings_state[fid]['status'] = 'overlay'
        findings_state[fid]['updated_at'] = now()
for d in plan.get('decisions', []):
    findings_state[d['finding']]['status'] = d['action']
    findings_state[d['finding']]['updated_at'] = now()
session['phase'] = 9
session['updated_at'] = now()
json.dump(session, open(os.path.join(run_dir, 'session.json'), 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
PY
```

Pass the in-memory plan from 9.4 as `PROMPTCHECKER_DECISION_PLAN` (JSON-encoded). The self-check refuses to write any record missing `ts`, `finding`, `lens`, or `action` — these are the spec minimum from `references/overlay-format.md` Section 2.

**Auto-filed contract (commit block above, summarising the JSONL writes):**

```
For each AUTO_FILED_SET finding, append ONE line to decisions.jsonl:
  {"ts": "...", "finding": "<id>", "lens": "tr_phonetic", "action": "auto_filed",
   "reason": "TR advisory finding — pronunciation hints are filed to inline-suggestions.md without user decision",
   "fix_kind": "advisory", "kind": "<foreign_word|abbreviation>"}

session.json.findings_state[<id>].status = "overlay" (same as user-overlay
decisions; the distinction lives in decisions.jsonl audit log only).
```

After the commit succeeds, print a single line:

```
Parsed N decisions: A applied, B overlay, C dismissed, D discussed, E TR-routed to overlay, F auto-filed.
```

The `F auto-filed` segment is appended only when `plan.auto_filed > 0`. Then hand off to Phase 10 in the same turn. Do not wait for further confirmation — the user already confirmed at Stage 1.5.

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

Collect every finding whose status is `overlay` (this includes the TR-routed findings from Phase 9.6's commit and the auto-filed TR advisory findings). Rebuild `$RUN_DIR/inline-suggestions.md` in full per the layout in `references/overlay-format.md`. The file is **rewritten from scratch on every Phase 10 pass** — idempotent regeneration ensures resumed sessions produce consistent overlays.

**Per-finding entries vs. Pronunciation map — exclusion rule for auto-filed findings.** When iterating findings for the "Findings with overlay status" section, check the most recent entry in `decisions.jsonl` for each finding. If the most recent action for that finding is `auto_filed`, **skip the per-finding entry** — the finding appears ONLY in the bottom "Pronunciation map" section. Findings whose most recent action is `overlay` or `revised` (user-initiated overlay) DO render as per-finding entries. Rationale: auto-filed findings represent background routing, not user decisions; surfacing them as per-finding entries forces the reader to scan past noise the user never asked about.

For backward compatibility with sessions produced before this rule existed: treat any TR finding with `lens == "tr_phonetic"` AND `fix_kind == "advisory"` AND `status == "overlay"` as auto-filed for rendering purposes (per-finding entry skip), even when its decisions.jsonl history lacks an `auto_filed` line.

The bottom "Pronunciation map" section still picks up every TR advisory finding via `findings.json.pronunciation_map` — that union is unaffected by the per-finding skip rule. See `references/overlay-format.md` Section 1 for the layout.

Do not append per-finding entries to `decisions.jsonl` for this step — the `overlay` / `auto_filed` decision was already logged in Phase 9.6's commit (or 10.3 if auto-converted from `applied`).

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

**CRITICAL — AskUserQuestion is MANDATORY for each discussed finding.**

When entering the konuşalım sub-flow for a finding, MUST emit
AskUserQuestion with the four options (kabul et / ben revize ediyorum /
yorum bırak / atla). Do NOT substitute prose "what do you want to do?"
questions. Do NOT auto-pick "kabul et" if the user seems patient.

If the user picks "ben revize ediyorum", the FOLLOWING free-text prompt
(asking for the revised suggestion text) IS plain conversational — that
one is intentionally not AskUserQuestion because free-form input is
needed. But the four-option choice itself is always AskUserQuestion.

Same headless fallback as Phase 3.5: if AskUserQuestion is unavailable,
abort the sub-flow for this finding (leave it pending) and surface a
warning. Do not auto-resolve.

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
- Don't substitute prose questions for AskUserQuestion in Phase 3.5 (lens selection) or Phase 10.4 (konuşalım four-option choice). The tool is mandatory at those points; prose is a workflow regression.
- Don't dispatch the four static lenses in a single Agent call. Phase 4 emits FIVE parallel Agent calls (conflict / dominance / gap / schema / tr_phonetic) in one assistant turn. Serial dispatch defeats the parallel design.
- Don't wait for schema or tr_phonetic to finish before starting drift. Drift only needs conflict + dominance + gap outputs; schema and tr are independent.
- Don't write an `applied` line to `decisions.jsonl` for a finding that didn't actually modify the prompt file. The feasibility check must precede the log write — single event per finding outcome. A failed-feasibility finding produces exactly one `routed_to_overlay` line followed by exactly one `overlay` line; an applicable finding produces exactly one `applied` line. Never two events that imply a prompt-file mutation when none occurred.
- Don't sort findings by line number alone — severity-first grouping is mandatory for both findings.json and report.md.
- Don't enable PROMPTCHECKER_TIMING in production runs unless debugging. The overhead is small but the timing.log file grows on every run and clutters the run dir.
- Don't re-compare the prompt file's SHA to `findings.json.prompt_sha256` after the first apply in a Phase 10 pass. The stale-audit guard is computed ONCE in Phase 10's Pre-flight and checks for *pre-audit* drift; intra-pass mutations from successful applies are intended and must not feed back into the comparison. The `new_sha256` field in `decisions.jsonl` is archival only.
- Don't write to `decisions.jsonl` or `session.json` during Phase 9.4 parsing — those writes are gated on explicit user confirmation in Phase 9.6 (Stage 2). The parser emits an in-memory plan only; the commit block runs after `evet`/`yes`/`onayla`/`ok`/`tamam`/`confirm`.
- Don't emit JSONL decision records missing the spec-required keys `ts`, `finding`, `lens`, `action`. The commit block self-checks every record and refuses to write incomplete lines (see `references/overlay-format.md` Section 2).
- Don't force-route every TR finding to overlay. Only TR findings with `fix_kind: "advisory"` (categories `foreign_word` and `abbreviation`) bypass the prompt file. TR findings with `fix_kind: "replace"` (categories `number_readability` and `punctuation`) follow the normal apply flow in Phase 10.3.
- Don't ask the user about TR pronunciation findings (foreign_word + abbreviation) in Phase 9. They auto-file to the overlay's Pronunciation map. Showing them in the summary table or decision prompt is a UX regression — the pronunciation hint is never going to be applied (advisory rule), so surfacing it as a decision wastes the user's attention. They appear ONLY in Phase 8's auto-filed count line and in `inline-suggestions.md`'s bottom Pronunciation map section.
- Don't apply a TODO/Intentional sentinel as if it were a regular structural fix. The sentinel guard in Phase 10.3 (step 4) intercepts them: `TODO:` routes to overlay (`sentinel_todo`), `Intentional —` is dismissed (`sentinel_intentional`). Neither ever reaches the Edit tool.
- Don't overwrite the `## Custom additions` block in `pronunciations.md`. The author owns content between the `<!-- promptchecker:custom-additions:start -->` and `<!-- promptchecker:custom-additions:end -->` markers; the rebuild MUST preserve that block verbatim.
- Don't merge schema findings into findings.json when `schema.json.applicable == false`. Auto-skipped lenses contribute zero findings — the summary just notes the reason. Adding a phantom `Schema: 0` row without applicability context is misleading on flat prompts.
- Don't apply compact mode when `max_char_limit == 0`. Zero explicitly disables the threshold; the body can be arbitrarily large without triggering cheaper policies.
- Don't treat compact mode as a hard abort. The audit STILL runs — it just runs with cheaper-per-lens policies. Severity floors, pair budgets, and drift halving are the contract; never use compact mode as an excuse to skip a lens entirely.
- Don't translate lens-generated content (rationale, suggested_fix, current_excerpt) in Phase 7. Only skill-side template strings translate. A lens runner that wrote an English rationale stays English in a TR report.
- Don't omit `section_ref: null` from findings.json — emit it explicitly (null is a valid value, signalling "line outside any numbered section"). Absent field is ambiguous; explicit null is clear.
- Don't render Current / Suggested / Action labels as separate blocks in Phase 7. Phase 7 emits a single markdown TABLE (id | lens | sev | section / line | rationale | fix) — one row per finding.
- Don't truncate rationale or suggested_fix in the renderer. Runners self-cap at ≤200 chars rationale and ≤150 chars fix (per their "Compact writing" invariants); the renderer uses the runner's text VERBATIM. If a runner exceeded the cap, that's a runner error — surface it as-is.
- Don't render `Satır 1` / `L1` for drift findings whose `line` and `section_ref` are both null. Use the literal `— / —` marker (= `TEMPLATE_STRINGS[lang]["no_line_marker"]`) so behavioural findings are visually distinct from anchored ones.
