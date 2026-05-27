---
name: prompt-check
description: Audit a prompt file (system prompt, agent definition, voice script, chained workflow) across four lenses — conflict, dominance, gap, drift — plus an optional Turkish phonetic lens for voice agents. Use when the user invokes /prompt-check, asks to "audit a prompt", "check this prompt for contradictions / silent overrides / gaps / drift / voice readability", or passes a path to a prompt file for review. On first run in a repo, walks the user through a 5-question wizard and saves repo defaults to `.promptchecker.json`. Produces line-anchored findings as `report.md` + `findings.json` in an isolated run directory. Never modifies the original prompt file.
---

# prompt-check

You audit a prompt file at the path supplied as `$1`. Read the prompt once, then dispatch each lens family to its dedicated subagent (`static-lens-runner`, `drift-runner`, `tr-phonetic-runner`). Merge their outputs in Phase 7. Write all artefacts under an isolated run directory. **Never modify the original prompt file.**

## Inputs you have

- `$1` — relative or absolute path to the prompt file under audit.
- `references/lens-rules.md` — full criteria for the static lenses; read by `static-lens-runner`.
- `references/tr-phonetic.md` — Turkish phonetic rules; read by `tr-phonetic-runner`.
- `references/probes.md` — adversarial probe templates; read by `drift-runner`.

The skill itself does not need to read these reference files — it only passes their paths to the matching subagent.

## Phase 0 — Project config (wizard on first run)

Project config lives at `<repo-root>/.promptchecker.json`. It captures repo-level defaults so the user does not write the same frontmatter on every prompt.

Locate the config path:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
CONFIG_PATH="$REPO_ROOT/.promptchecker.json"
echo "REPO_ROOT=$REPO_ROOT"
echo "CONFIG_PATH=$CONFIG_PATH"
```

**If `$CONFIG_PATH` exists, skip the wizard and continue to Phase 1.** Read it later in Phase 2 during frontmatter merge.

**If `$CONFIG_PATH` does not exist**, run the first-run wizard before continuing. Ask the user the five questions below (prefer `AskUserQuestion` if available, otherwise plain conversational prompts; either way wait for all five answers before writing the file).

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
echo "RUN_DIR=$RUN_DIR"
echo "RUN_NAME=$RUN_NAME"
echo "ABS_PROMPT=$ABS_PROMPT"
```

**Invariants for the entire run:**
- All artefacts go under `$RUN_DIR/`. Never `.promptcheck/.tmp/`.
- Original prompt file is read-only. No inline annotation, no edits, no `.bak`.
- Previous run directories are left intact (versioning).

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

# D1: snapshot the prompt content hash for stale-audit detection in apply-mode
prompt_sha256 = hashlib.sha256(text.encode('utf-8')).hexdigest()

# B1: compute body_line_offset so downstream phases can map body.txt lines
# back to the original prompt file's line numbers (apply-mode depends on this).
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

**Advisory invariant:** every TR finding has `fix_kind: "advisory"`. PromptChecker never auto-applies any TR phonetic suggestion to the prompt file — neither substring replacement nor pronunciation block injection. The subagent populates either `suggested_fix` (textual issues) or `pronunciation_entry` (foreign words / abbreviations) for the report, but apply-mode treats every TR finding as advisory and only surfaces their count in the diff summary. The author decides.

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

Apply this to every `findings[].line` and `findings[].related_lines[]`. After translation, body.txt indices must no longer appear in any rendered output. Apply-mode and report.md both depend on this.

**Carry the prompt hash:** copy `frontmatter.prompt_sha256` into the top of findings.json so apply-mode can detect a stale audit.

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

`pronunciation_map` is the union of `tr_phonetic.json.seed_entries` (entries the prompt already had — `source: "seed"`) and the `pronunciation_entry` payload of TR findings (`source: "finding"`). Dedupe by `term` (case-insensitive); if a seed entry and a finding entry collide, **seed wins** (the author's curated text is the source of truth). It is a flat reference list rendered in `report.md` and surfaced in `findings.json` for downstream tooling — apply-mode never injects it back into the prompt.

`findings[]` is sorted by `line` ascending, then by severity descending. For each finding:
- `fix_kind: "replace"` → apply-mode edits the line so it produces `suggested_fix` instead of `current_excerpt`. Only emitted by `conflict`, `dominance`, `gap`, `drift` lenses (never TR phonetic).
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

(One section per lens. Inside each section, one bullet per finding in line order.)

### Conflicts
- **L<line>** [C1 severity=high, R3↔R8] — <rationale>
  - **Current:** `<current_excerpt>`
  - **Fix:** `<suggested_fix or "(see rationale)">`

### Dominances
- **L<line>** [D1 mechanism=role-override, R12 > R3] — <rationale>
  - …

### Gaps
- **L<line>** [G1 severity=medium, ambiguous_term, related R5] — <rationale>
  - …

### Drift
(If skipped:) _Skipped — <skipped_reason>._
(Otherwise per scenario:)
- **S1** [<kind>] <pass|fail> score=<0.00–1.00>
  - Input: `<scenario.input truncated to 120 chars>`
  - Reasons: <reasons joined>

### TR phonetic
(Only if enabled.)
- **L<line>** [T1 number_readability severity=high] — <rationale>
  - **Current:** `100 TL`
  - **Fix:** `yüz lira`
```

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

Say "fix these" or "düzelt bunları" and I will apply suggested_fix entries from findings.json.
```

## Apply-mode (when the user asks to fix)

Apply-mode is entered in either of two ways:

- The user runs the explicit slash command **`/prompt-check-apply [run-id]`** — preferred path.
- The user types "fix these" / "düzelt bunları" / "apply the fixes" / similar trigger phrase in the same or a later session.

In both paths, resolve the run directory: explicit `run-id` argument > `latest` symlink. Read `<run-dir>/findings.json`.

### Pre-flight — stale-audit guard (mandatory)

Before touching anything, verify the prompt file has not changed since the audit:

```bash
ACTUAL_SHA=$(shasum -a 256 "$PROMPT_PATH" | awk '{print $1}')
```

Compare against `findings.json.prompt_sha256`. If they differ, **abort** — do not apply anything. Surface:

```
Prompt has changed since this audit (run-NNN). Re-run /prompt-check first, then /prompt-check-apply.
Expected SHA: <findings.prompt_sha256>
Actual SHA:   <ACTUAL_SHA>
```

If they match, proceed.

### Replace pass — line-level substitutions (non-TR lenses only)

For each finding where ALL of these hold:
- `fix_kind == "replace"`
- `suggested_fix` is non-empty
- `lens != "tr_phonetic"` (TR findings are **never** auto-applied — see "TR phonetic exclusion" below)

Do:

1. Read the prompt file fresh.
2. Locate the line via `line` number (already translated to original-file line by Phase 7) AND `current_excerpt` substring match (both must agree — if not, skip and report).
3. If `current_excerpt` appears more than once on that line, refuse to apply that finding and report it (no occurrence index → ambiguous).
4. Apply the substring replacement.
5. Write the file back.

Group conflicting suggestions: if two findings target the same line, present a choice rather than silently applying one. Never apply a finding whose `suggested_fix` is empty — those are advisory only.

### TR phonetic exclusion (hard rule)

Apply-mode **never** modifies the prompt based on TR phonetic findings — no substring replacement, no pronunciation guide injection, no auto-inserted blocks. TR findings live in `report.md` / `findings.json` for the author to read and act on by hand. If `findings.json` contains TR findings, surface their count in the diff summary so the author knows they exist; do not touch the file because of them.

Rationale: phonetic adjustments and pronunciation hints are voice-design decisions the human author owns. False positives are common (proper nouns, brand voice, dialect choice), and a silently-injected block can poison a Vapi/ElevenLabs script in subtle ways. PromptChecker reports — the author decides.

### Diff surface

After the replace pass, show a short summary in the terminal:
- Pre-flight: `Prompt SHA match — ok` or `Prompt SHA mismatch — aborted`.
- Replace pass: `N findings applied, M skipped (mismatch / ambiguous occurrence), K conflicts on same line`.
- TR phonetic: `<N> advisory findings — reported only, no auto-apply (see report.md)` (omit if no TR findings).

If the replace pass is empty, say so explicitly — do not pretend to have done work.

## Don'ts

- Don't extract frontmatter with an LLM pass; use Bash/Python — it's deterministic and free.
- Don't read the original prompt more than once per phase; pass `body.txt` between steps.
- Don't run lens analysis inline in the skill. Each lens family has a dedicated subagent (`static-lens-runner`, `drift-runner`, `tr-phonetic-runner`); the skill only dispatches and reads outputs.
- Don't write outside `$RUN_DIR/` (except for `.promptchecker.json` in Phase 0, with the user's explicit consent through the wizard).
- Don't modify the original prompt file in any phase — only Apply-mode does that, and only when the user explicitly asks.
- Don't run the wizard if `.promptchecker.json` already exists. The user owns that file.
