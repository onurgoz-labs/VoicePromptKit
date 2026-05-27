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
```

If `python3` is unavailable, fall back to reading the file yourself, splitting on the first two `---` lines, and applying the same merge logic by reasoning. State the fallback in the terminal summary.

## Phase 3 — Rule extraction (inline)

Read `$RUN_DIR/body.txt`. Number lines starting at 1, **including blank lines**, so that `body.txt` line N maps to original-file line `N + frontmatter.body_line_offset - 1`. Extract every atomic rule, instruction, constraint, or directive into a flat list. Apply the criteria in `references/lens-rules.md` section "Rule extraction" — split compound sentences, preserve absolutes ("always", "never"), use the lowest line where the rule begins.

**Line-number contract for every phase that follows (rule-extractor, conflict, dominance, gap, drift, TR phonetic):** all `line` fields you produce are body.txt line numbers (1-indexed, blank lines included). Phase 7 (render) is the single place that translates these to original-file line numbers before writing `findings.json`. **Never** write original-file line numbers from inside a lens — that breaks the contract.

Hold the rules in memory as JSON. Also write `$RUN_DIR/rules.json` with shape:

```json
{ "rules": [{"id":"R1","category":"behavior|format|tone|policy|persona","text":"...","line":12,"source_excerpt":"..."}] }
```

If you extract zero rules, abort with an error written to `$RUN_DIR/error.txt` and surface that to the user.

## Phase 3.5 — Lens-selection wizard (per-run)

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
  "asked_at": "<ISO 8601 UTC>"
}
```

`anchors` is copied verbatim from `frontmatter.anchors` (no per-run question for anchors; they live in frontmatter only).

Phase 9 writes this `user_intent` block into `session.json` at interactive entry. Until then it is held in memory by the skill.

**Dispatch impact:**
- Phase 4 (static lenses): if `conflict`, `dominance`, or `gap` is unselected, instruct `static-lens-runner` to skip those sub-lenses (the runner writes an empty `{"conflicts": []}` etc. for skipped sub-lenses, OR the skill simply does not dispatch when all three are unselected).
- Phase 5 (drift): the existing skip gate (`expand_count == 0` OR no anchors/conflicts/role-overrides) already handles drift opt-out. Additionally, if `drift` is unselected here, skip Phase 5 entirely and write `drift.json` with `skipped_reason: "drift lens deselected by user"`.
- Phase 6 (TR phonetic): only dispatch if `tr_phonetic` is in `user_intent.selected_lenses` AND `frontmatter.tr_phonetic == true` (after the Phase 3.5 confirmation). Otherwise skip — no `tr_phonetic.json` is written, and Phase 7 treats missing files as "lens disabled".

Phase 7 already handles missing per-lens JSON files as "lens disabled" — no change there.

## Phases 4 + 6 — Parallel lens dispatch (with Phase 5 downstream of 4)

Phase 4 (static lenses) and Phase 6 (TR phonetic, when its gate passes) are **independent** — they read different inputs and write different outputs. Dispatch both subagents in a **single message with two concurrent `Agent` calls** so they execute in parallel. Phase 5 (drift) is **downstream of Phase 4** — drift-runner reads `conflicts.json`, `gaps.json`, and `dominances.json` as inputs, so it MUST run after Phase 4's outputs land. The correct order is:

```
   ┌─ static-lens-runner ─┐
   │                      ├─→ drift-runner (Phase 5, conditional)
   └─ tr-phonetic-runner ─┘    (only reads Phase 4's outputs)
   (Phase 4 + Phase 6 fan out together)
```

Concretely: in one assistant turn, emit both Phase 4 and Phase 6 `Agent` tool calls. Await both. Then evaluate Phase 5's gate and dispatch drift-runner if warranted.

## Phase 4 — Static lenses (conflict + dominance + gap)

Dispatch the `static-lens-runner` subagent to apply the three static lenses in one pass. Detection criteria live in `references/lens-rules.md` — the subagent reads that document. The skill itself does no lens analysis.

**Line-number contract:** every `line` field the subagent writes is a body.txt index (1-indexed, blank lines included). Phase 7 is the single place that translates these to original-file line numbers.

Pass inputs and output paths as **separate** top-level fields so the subagent never reads its own future outputs:

```
Agent({
  subagent_type: "static-lens-runner",
  prompt: JSON.stringify({
    inputs: {
      body:           "<absolute path to $RUN_DIR/body.txt>",
      frontmatter:    "<absolute path to $RUN_DIR/frontmatter.json>",
      rules:          "<absolute path to $RUN_DIR/rules.json>",
      lens_rules_ref: "<absolute path to skills/prompt-check/references/lens-rules.md>"
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

`static-lens-runner` writes `$RUN_DIR/conflicts.json`, `$RUN_DIR/dominances.json`, and `$RUN_DIR/gaps.json`. The skill reads them in Phase 7.

## Phase 5 — Drift (conditional)

**Skip Phase 5 if EITHER condition holds:**

A) `expand_count == 0` — the user / project config / env var explicitly disabled drift. This is the hard kill switch. Write `$RUN_DIR/drift.json` with `skipped_reason: "expand_count is 0 — drift disabled"`.

B) ALL of the following are true:
- `frontmatter.anchors` is empty AND
- `conflicts` is empty AND
- no `dominance.mechanism == "role-override"` exists

Write `$RUN_DIR/drift.json` with `skipped_reason: "no anchors, conflicts, or role-overrides — drift adds no signal"`.

In either skip case the file shape is `{"scenarios": [], "runs": [], "verdicts": [], "skipped_reason": "..."}`. Move on to Phase 6.

Otherwise dispatch the `drift-runner` subagent (it is the only subagent this skill uses). Pass inputs and the output path as **separate** top-level fields so the subagent does not accidentally read its own future output:

```
Agent({
  subagent_type: "drift-runner",
  prompt: JSON.stringify({
    inputs: {
      body:        "<absolute path to $RUN_DIR/body.txt>",
      frontmatter: "<absolute path to $RUN_DIR/frontmatter.json>",
      rules:       "<absolute path to $RUN_DIR/rules.json>",
      conflicts:   "<absolute path to $RUN_DIR/conflicts.json>",
      gaps:        "<absolute path to $RUN_DIR/gaps.json>",
      dominances:  "<absolute path to $RUN_DIR/dominances.json>",
      probes_ref:  "<absolute path to skills/prompt-check/references/probes.md>"
    },
    output_path: "<absolute path to $RUN_DIR/drift.json>"
  }),
  description: "drift analysis for " + BASENAME
})
```

`drift-runner` generates scenarios, simulates the model on each, judges outputs, and writes `$RUN_DIR/drift.json` with shape `{scenarios, runs, verdicts}`. The skill never decomposes drift inline because it is the only step whose token cost scales with prompt length.

## Phase 6 — Turkish phonetic lens (conditional)

**Run this phase only if `frontmatter.tr_phonetic == true`.** Otherwise skip to Phase 7 — `tr_phonetic.json` is not written, and Phase 7 treats the absence as "TR lens disabled".

When the gate passes, dispatch the `tr-phonetic-runner` subagent. It seeds `pronunciation_map` from any existing pronunciation guide block in the body, scans for new findings, dedupes against the seed, and writes a single `$RUN_DIR/tr_phonetic.json`. Every rule (skip rules, whitelist, strategy semantics, the "no semantic translation" hard rule, the three `fix_kind` values, the seed block formats and line-range tracking) lives in `references/tr-phonetic.md` — the subagent reads that document; the skill does not repeat the criteria here.

**Line-number contract:** every `line` field (including `seed_block_range.start_line` / `end_line`) the subagent writes is a body.txt index. Phase 7 translates to original-file lines.

**Advisory invariant:** every TR finding has `fix_kind: "advisory"`. PromptChecker never auto-applies any TR phonetic suggestion to the prompt file — neither substring replacement nor pronunciation block injection. The subagent populates either `suggested_fix` (textual issues) or `pronunciation_entry` (foreign words / abbreviations) for the report. Phase 9's TR routing rule guarantees this at the interactive layer: any TR finding decided as `applied` is auto-converted to `overlay` before Phase 10 ever sees it. The author decides whether and how to act on TR findings by hand.

Pass inputs and the output path as **separate** top-level fields:

```
Agent({
  subagent_type: "tr-phonetic-runner",
  prompt: JSON.stringify({
    inputs: {
      body:             "<absolute path to $RUN_DIR/body.txt>",
      frontmatter:      "<absolute path to $RUN_DIR/frontmatter.json>",
      tr_phonetic_ref:  "<absolute path to skills/prompt-check/references/tr-phonetic.md>"
    },
    output_path: "<absolute path to $RUN_DIR/tr_phonetic.json>"
  }),
  description: "TR phonetic lens for " + BASENAME
})
```

`tr-phonetic-runner` writes `$RUN_DIR/tr_phonetic.json` with shape `{ findings[], seed_entries[], warnings[] }`. The skill reads it in Phase 7.

## Phase 7 — Render outputs

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
- `fix_kind: "replace"` → Phase 10's applied step rewrites the line so it produces `suggested_fix` instead of `current_excerpt`. Only emitted by `conflict`, `dominance`, `gap`, `drift` lenses (never TR phonetic).
- `fix_kind: "advisory"` → no automatic apply. **Every TR phonetic finding uses this**, regardless of whether `suggested_fix` or `pronunciation_entry` is populated.

The TR lens never produces `replace` findings — TR suggestions are always reported, never written.

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

## Phase 8 — Terminal summary

After all writes succeed, **update the `latest` symlink** so it points at this run (the run is now durable), then print the summary:

```bash
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
Previous runs: .promptcheck/<basename>/ (run-001 … run-NNN)
Repo defaults: <relative path to .promptchecker.json>

Entering interactive review (Phase 9). Use a compact decision string such as
  "C1, C3 düzelt; G2 yorum bırak; T1..T5 konuşalım; gerisini atla"
to choose what to do with each finding. Type "iptal" to leave the session as
pending and resume later with /prompt-check-resume.
```

After printing this block, **do not stop** — automatically transition to Phase 9 in the same turn. Phase 9 + Phase 10 are part of the default `/prompt-check` flow; the audit is not finished until the user either resolves every finding or explicitly cancels with "iptal".

## Phase 9 — Interactive selection

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

### 9.4 — Parse the decision string (deterministic)

The detailed grammar (id-lists, ranges with `..`, wildcards like `gerisini` / `rest`, verb aliases for `düzelt`/`fix`/`apply`, `yorum bırak`/`overlay`/`note`, `atla`/`skip`/`dismiss`, `konuşalım`/`discuss`/`talk`) lives in `references/dialog-flow.md`. Implement the parser as a single Python heredoc so it is deterministic. Skeleton:

```bash
python3 - "$RUN_DIR" <<'PY'
import sys, json, os, datetime, re
run_dir = sys.argv[1]
user_input = os.environ.get('PROMPTCHECKER_DECISION_INPUT', '')

session = json.load(open(os.path.join(run_dir, 'session.json'), encoding='utf-8'))
findings_state = session['findings_state']
known_ids = list(findings_state.keys())

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

# TR routing rule: tr_phonetic + applied → overlay
routed = []
for i, (fid, status, raw) in enumerate(decisions_resolved):
    if status == 'applied' and findings_state[fid]['lens'] == 'tr_phonetic':
        routed.append(fid)
        decisions_resolved[i] = (fid, 'overlay', raw)

# Append to decisions.jsonl: routed entries FIRST (one per routed id), then the actual decisions
out_path = os.path.join(run_dir, 'decisions.jsonl')
with open(out_path, 'a', encoding='utf-8') as f:
    for fid in routed:
        f.write(json.dumps({
            'finding_id': fid,
            'action': 'routed_to_overlay',
            'reason': 'TR phonetic findings never modify the prompt file',
            'at': now(),
        }, ensure_ascii=False) + '\n')
    for fid, status, raw in decisions_resolved:
        f.write(json.dumps({
            'finding_id': fid,
            'action': status,
            'source': 'phase_9_decision_string',
            'raw_segment': raw,
            'at': now(),
        }, ensure_ascii=False) + '\n')

# Update session.json
for fid, status, _ in decisions_resolved:
    findings_state[fid]['status'] = status
    findings_state[fid]['updated_at'] = now()
session['phase'] = 9
session['updated_at'] = now()
json.dump(session, open(os.path.join(run_dir, 'session.json'), 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

# Outcome counters for the terminal line
buckets = {'applied':0,'overlay':0,'dismissed':0,'discussed':0}
for fid, status, _ in decisions_resolved:
    buckets[status] = buckets.get(status, 0) + 1

print(json.dumps({
    'parsed': len(decisions_resolved),
    'applied': buckets['applied'],
    'overlay': buckets['overlay'],
    'dismissed': buckets['dismissed'],
    'discussed': buckets['discussed'],
    'tr_routed': len(routed),
    'unrecognised': unrecognised,
}, ensure_ascii=False))
PY
```

Pass the user's reply as `PROMPTCHECKER_DECISION_INPUT` (or stdin — whichever is cleaner in the harness). The block above is illustrative; trust `references/dialog-flow.md` as the source of truth for the verb table and grammar edge cases.

### 9.5 — Report parse outcome

Print one line:

```
Parsed N decisions: A applied, B overlay, C dismissed, D discussed, E TR-routed to overlay, F unrecognised.
```

If `unrecognised` is non-empty, list the unparsed segments and re-prompt — loop back to 9.3. Do not advance to Phase 10 until every segment is either parsed or the user types `iptal`.

If after parsing every pending finding still has `status: "pending"`, ask the user one clarifying turn (using the template in `references/dialog-flow.md`) before falling through to Phase 10. Pending findings at Phase 10 entry are treated as `dismissed` only when the user explicitly says `gerisini atla` (or equivalent).

### 9.6 — Hand off to Phase 10

Transition automatically. Do not wait for further confirmation unless `decisions_resolved` is empty.

## Phase 10 — Action dispatch

Phase 10 reads `session.json` and `decisions.jsonl` from Phase 9 and executes each decided action. The order below is fixed — see `references/overlay-format.md` Section 4 for the rationale.

**Do not read decisions.jsonl or session.json while a Phase 10 sub-step is still writing to them.** Each sub-step reads its inputs once at entry, performs its writes, then yields to the next sub-step.

### 10.1 — Dismissed

For each finding with `findings_state[fid].status == "dismissed"`: log only, no I/O. Count for the closing summary. Do not append anything to `decisions.jsonl` here — the Phase 9 entry already records the decision.

### 10.2 — Overlay

Collect every finding whose status is `overlay` (this includes the TR-routed findings from Phase 9.4). Rebuild `$RUN_DIR/inline-suggestions.md` in full per the layout in `references/overlay-format.md`. The file is **rewritten from scratch on every Phase 10 pass** — idempotent regeneration ensures resumed sessions produce consistent overlays.

Do not append per-finding entries to `decisions.jsonl` for this step — the `overlay` decision was already logged in Phase 9.4 (or 10.3 if auto-converted from `applied`).

### 10.3 — Applied (feasibility-first, single-event logging)

**Core invariant:** `decisions.jsonl` records ONE event per finding outcome in this sub-step. The `applied` action is written **only when the prompt file was genuinely modified**. If a finding cannot be applied for any reason, it produces a single `routed_to_overlay` entry followed by a single `overlay` entry — never a misleading `applied` entry first.

Note: Phase 9 already wrote `routed_to_overlay` lines for TR-routed `applied` decisions before they arrived at Phase 10 as `overlay`. The TR-routing path therefore never enters 10.3 — it is handled by 10.2 directly. The TR feasibility check below remains as a defensive guard in case a future caller skips Phase 9's TR routing.

**Pre-flight (mandatory):** compute the current SHA256 of the prompt file once for the whole sub-step:

```bash
ACTUAL_SHA=$(shasum -a 256 "$PROMPT_PATH" | awk '{print $1}')
```

For each finding with `findings_state[fid].status == "applied"`, run a **feasibility check first** (read-only — no writes to `decisions.jsonl`, no writes to the prompt file).

Possible feasibility outcomes (one per finding):
- `tr_routed` — TR foreign_word/abbreviation, never modifies the prompt file
- `no_concrete_fix` — empty or null `suggested_fix` (should never happen with v0.4.1+, but defensive)
- `sha_mismatch` — stale audit; SHA256 of prompt file changed since audit snapshot
- `ambiguous` — `current_excerpt` appears zero or multiple times on `finding.line`
- `structural_declined` — user declined the risk warning for a `fix_strategy: "structural"` finding (see dispatch below)
- `applicable` — passes all feasibility checks, proceed to `fix_strategy` dispatch

Determine the outcome per finding from the following ordered rules:

1. If `finding.lens == "tr_phonetic"` → outcome `tr_routed`, reason `"TR phonetic findings never modify the prompt file"`.
2. Else if `finding.suggested_fix` is `null` or an empty string → outcome `no_concrete_fix`, reason `"no concrete suggested_fix — manual author revision required"`.
3. Else if `ACTUAL_SHA != session.json.prompt_sha256_at_audit` (which mirrors `findings.json.prompt_sha256`) → outcome `sha_mismatch`, reason `"stale audit — prompt SHA256 mismatch"`.
4. Else read the prompt file fresh and index to `finding.line` (already an original-file line; Phase 7 translated it). Count occurrences of `current_excerpt` on that line:
   - Zero matches → outcome `ambiguous`, reason `"ambiguous occurrence — substring matches multiple positions"` (treat zero-match identically to multi-match; the substring is no longer locatable on the named line — overlay is the safe fallback).
   - More than one match → outcome `ambiguous`, same reason as above.
   - Exactly one match → outcome `applicable`.

**Single-event write per finding** based on the outcome:

- **`applicable`** — perform the dispatch on `fix_strategy`:

  For each finding marked `applicable` (passed all earlier feasibility checks), dispatch on `fix_strategy`:

  - **`fix_strategy: "substring"` (or absent, for backward compatibility):**
    Perform the substring replacement: read the prompt file, locate `line` + `current_excerpt`, replace with `suggested_fix`. Write the file back. Log: `{"action": "applied", "target": "prompt_file", "tool": "substring_replace", "fix_strategy": "substring", "from": "<current_excerpt>", "to": "<suggested_fix>", "line": N}`.

  - **`fix_strategy: "structural"`:**
    Surface a risk warning to the user BEFORE applying: "⚠ Structural change for <finding-id>: the suggestion is an action description, not a literal substring. I will use the Edit tool to apply: <suggested_fix>. Confirm? (y/n)". If the user declines, the outcome flips to `structural_declined` — route to overlay instead with note "user declined structural apply", and append a single `routed_to_overlay` line followed by a single `overlay` line to `decisions.jsonl` (no `applied` line). If the user accepts, apply via the Edit tool (semantic edit reflecting the intent of `suggested_fix`), then log: `{"action": "applied", "target": "prompt_file", "tool": "edit", "fix_strategy": "structural", "intent": "<suggested_fix>", "line": N, "risk_acknowledged": true}`.

    Implementation note: when the user said `hepsini düzelt` (wildcard), the risk-warning prompt is shown ONCE per structural finding, not bundled. Each structural application is its own decision point.

  After a successful write (either substring_replace or edit), append the corresponding entry to `decisions.jsonl`. For backward-compatible substring writes the full line shape is:

  ```json
  {"ts":"<now>","finding":"<id>","lens":"<lens>","action":"applied","target":"prompt_file","tool":"substring_replace","fix_strategy":"substring","from":"<current_excerpt>","to":"<suggested_fix>","line":<N>,"source":"phase_9_decision_string","raw_segment":"<user-segment>","original_sha256":"<ACTUAL_SHA>","new_sha256":"<post-write SHA>"}
  ```

  For structural writes:

  ```json
  {"ts":"<now>","finding":"<id>","lens":"<lens>","action":"applied","target":"prompt_file","tool":"edit","fix_strategy":"structural","intent":"<suggested_fix>","line":<N>,"risk_acknowledged":true,"source":"phase_9_decision_string","raw_segment":"<user-segment>","original_sha256":"<ACTUAL_SHA>","new_sha256":"<post-write SHA>"}
  ```

  Recompute `ACTUAL_SHA` immediately after the write so the next finding in this pass sees the updated hash (otherwise subsequent applied findings within the same pass would all trip rule 3).

- **`tr_routed` / `no_concrete_fix` / `sha_mismatch` / `ambiguous` / `structural_declined`** — do NOT modify the prompt file. Update `findings_state[fid].status = "overlay"` in memory. Append ONE `routed_to_overlay` line then ONE `overlay` line to `decisions.jsonl`:

  ```json
  {"ts":"<now>","finding":"<id>","lens":"<lens>","action":"routed_to_overlay","reason":"<reason from outcome>","source":"phase_9_decision_string","raw_segment":"<user-segment>"}
  {"ts":"<now>","finding":"<id>","lens":"<lens>","action":"overlay"}
  ```

  Add the finding to the overlay set processed in 10.2.

**No `applied` line is ever written for a finding whose outcome was anything other than `applicable` + successful dispatch.** The feasibility check (and, for structural, the user's risk acknowledgement) precedes the log write — full stop.

After the applied pass completes, recompute `current_prompt_sha256 = shasum -a 256 "$PROMPT_PATH"` and store it in memory for downstream queries. **Do not modify `findings.json.prompt_sha256`** — that field is the audit snapshot and stays frozen.

If any auto-conversion happened in this sub-step (any outcome other than `applicable` + applied), re-run 10.2 once so `inline-suggestions.md` includes the newly routed overlay findings.

Track the per-outcome counts for the Phase 10.5 closing summary:
- `applied_count` — outcome was `applicable` and the write succeeded (any `fix_strategy`)
- `structural_applied_count` — subset of `applied_count` where `fix_strategy: "structural"` (user acknowledged the risk warning and the Edit tool fired)
- `tr_routed_count` — outcome `tr_routed`
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
4. **TR routing rule still applies inside 10.4:** if the finding has `lens == "tr_phonetic"` and the user picks `applied` (or `revised → applied`), auto-convert to `overlay` with a `routed_to_overlay` entry in `decisions.jsonl` preceding the `overlay` entry.
5. **Record the final action** in `decisions.jsonl` (`applied` / `overlay` / `dismissed`). Update `findings_state[fid].status` to that terminal status. Do not leave `discussed` or `revised` as the final status — they are transient.
6. **Loop** until every `discussed` finding has been resolved to one of the four terminal states.

If during 10.4 a SHA mismatch is detected (the user took a long pause and an external tool changed the file), apply the same drift handling as 10.3: auto-convert pending `applied` decisions in 10.4 to `overlay`, log the routing, continue.

### 10.5 — Phase 10 closing summary

Print a single block. The counts mirror the per-outcome accounting from 10.3 and the user-driven outcomes from 10.2 / 10.4 — each finding is counted exactly once.

```
Interactive review complete — <run-NNN>

- Applied:        <N> (each one actually wrote to <prompt-path>; of which <S> structural via Edit tool)
- Auto-routed:    <X> (TR: <a>, no-fix: <b>, stale-audit: <c>, ambiguous: <d>, structural-declined: <e>)
- Manually overlay: <Y> (user chose "yorum bırak" directly)
- Dismissed:      <Z>
- Revised:        <W> (of which: A applied, B overlay)

Overlay file: <relative path to $RUN_DIR/inline-suggestions.md>  (if any overlays exist)
Decisions log: <relative path to $RUN_DIR/decisions.jsonl>
Session state: <relative path to $RUN_DIR/session.json>
```

- `Applied` = `applied_count` from 10.3 (the number of `applied` lines in `decisions.jsonl` for this pass; never inflated by failed-feasibility findings). The `of which <S> structural via Edit tool` split surfaces `structural_applied_count` so the user can see how many writes went through the risk-warned Edit path versus plain substring replacement.
- `Auto-routed` = `tr_routed_count + no_fix_count + stale_audit_count + ambiguous_count + structural_declined_count` from 10.3. Always break down by the five reasons so the user knows why each one was redirected.
- `Manually overlay` = findings whose Phase 9 decision was `overlay` (or `konuşalım → overlay`). Does NOT include the auto-routed bucket — those are reported separately to avoid double-counting.
- `Revised` = findings that went through the `konuşalım → revised` path in 10.4. The `A applied, B overlay` split reflects the terminal action chosen for each revised entry.

Update `session.json.phase = "complete"` and `session.json.updated_at` on exit. If any findings remain `pending` at this point (user did not address them and did not type `gerisini atla`), set `session.json.phase = "paused"` instead and remind the user they can resume with `/prompt-check-resume <run-NNN>`.

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
