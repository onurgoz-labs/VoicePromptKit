---
name: prompt-check
description: Audit a prompt file (system prompt, agent definition, voice script, chained workflow) across four lenses — conflict, dominance, gap, drift — plus an optional Turkish phonetic lens for voice agents. Use when the user invokes /prompt-check, asks to "audit a prompt", "check this prompt for contradictions / silent overrides / gaps / drift / voice readability", or passes a path to a prompt file for review. On first run in a repo, walks the user through a 5-question wizard and saves repo defaults to `.promptchecker.json`. Produces line-anchored findings as `report.md` + `findings.json` in an isolated run directory. Never modifies the original prompt file.
---

# prompt-check

You audit a prompt file at the path supplied as `$1`. Work in a single context. Read the prompt once. Analyse all four (or five) lenses inline. Write artefacts under an isolated run directory. **Never modify the original prompt file.**

## Inputs you have

- `$1` — relative or absolute path to the prompt file under audit.
- `references/lens-rules.md` — full criteria for each lens (read on first use).
- `references/tr-phonetic.md` — Turkish phonetic rules (read only if `tr_phonetic: true`).
- `references/probes.md` — adversarial probe templates (read only if drift phase runs).

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
   - `markdown` (default ✓), `findings_json` (default ✓), `json`, `html`.
5. **Drift `expand_count`** (extra scenarios beyond anchors + conflict budget):
   - Integer 0–20. Default `3`. Zero disables drift entirely.

After collecting answers, write `$CONFIG_PATH` as pretty JSON (2-space indent):

```json
{
  "$schema": "https://github.com/onurgoz/PromptChecker/blob/master/schema/config.schema.json",
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
NEXT=$(printf 'run-%03d' "$(( $(ls -1 "$PROMPT_DIR" 2>/dev/null | grep -c '^run-') + 1 ))")
RUN_DIR="$PROMPT_DIR/$NEXT"
mkdir -p "$RUN_DIR"
ln -sfn "$NEXT" "$PROMPT_DIR/latest"
echo "RUN_DIR=$RUN_DIR"
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
import sys, re, json, os
prompt_path, run_dir, config_path = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(prompt_path, encoding='utf-8').read()
m = re.match(r'^---\r?\n(.*?)\r?\n---\r?\n?(.*)$', text, re.DOTALL)
raw_fm, body = (m.group(1), m.group(2)) if m else ('', text)
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

# target_model
resolved['target_model'] = (
    fm.get('target_model')
    or env('PROMPTCHECKER_TARGET_MODEL')
    or project.get('target_model')
    or 'claude-opus-4-7'
)

# output (list)
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

# expand_count (int)
ec = fm.get('expand_count')
if ec is None:
    ec = env('PROMPTCHECKER_EXPAND_COUNT') or project.get('expand_count') or 3
resolved['expand_count'] = int(ec)

# executor
resolved['executor'] = fm.get('executor') or env('PROMPTCHECKER_EXECUTOR') or 'inline'

# anchors (always from frontmatter; never overridden)
resolved['anchors'] = fm.get('anchors') or []

# tr_phonetic (bool)
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

with open(os.path.join(run_dir, 'frontmatter.json'), 'w', encoding='utf-8') as f:
    json.dump(resolved, f, indent=2, ensure_ascii=False)
with open(os.path.join(run_dir, 'body.txt'), 'w', encoding='utf-8') as f:
    f.write(body.lstrip('\n'))
PY
```

If `python3` is unavailable, fall back to reading the file yourself, splitting on the first two `---` lines, and applying the same merge logic by reasoning. State the fallback in the terminal summary.

## Phase 3 — Rule extraction (inline)

Read `$RUN_DIR/body.txt`. Number lines starting at 1 from the first non-empty line. Extract every atomic rule, instruction, constraint, or directive into a flat list. Apply the criteria in `references/lens-rules.md` section "Rule extraction" — split compound sentences, preserve absolutes ("always", "never"), use the lowest line where the rule begins.

Hold the rules in memory as JSON. Also write `$RUN_DIR/rules.json` with shape:

```json
{ "rules": [{"id":"R1","category":"behavior|format|tone|policy|persona","text":"...","line":12,"source_excerpt":"..."}] }
```

If you extract zero rules, abort with an error written to `$RUN_DIR/error.txt` and surface that to the user.

## Phase 4 — Four lenses (single pass, inline)

Apply all four lenses in the same context, in sequence. Each lens reads the rule list (and body where noted) and produces a JSON section. Detection criteria for every lens live in `references/lens-rules.md` — consult that document; do not re-state the criteria here.

Produce one in-memory analysis object:

```json
{
  "conflicts":  [{"id":"C1","rule_ids":["R3","R8"],"severity":"low|medium|high","reasoning":"..."}],
  "dominances": [{"id":"D1","dominant_rule_id":"R12","dominated_rule_id":"R3","mechanism":"position|length|specificity|recency|role-override","reasoning":"..."}],
  "gaps":       [{"id":"G1","kind":"undefined_edge_case|ambiguous_term","description":"...","related_rule_ids":["R5"],"severity":"low|medium|high"}]
}
```

Write each section to `$RUN_DIR/{conflicts,dominances,gaps}.json` as you complete it. The drift section is filled by Phase 5.

## Phase 5 — Drift (conditional)

**Skip Phase 5 entirely if all of the following are true:**
- `frontmatter.anchors` is empty AND
- `conflicts` is empty AND
- no `dominance.mechanism == "role-override"` exists AND
- `expand_count > 0` (zero explicitly disables drift)

In a skip, write `$RUN_DIR/drift.json` as `{"scenarios": [], "runs": [], "verdicts": [], "skipped_reason": "no anchors, conflicts, or role-overrides — drift adds no signal"}` and move on.

Otherwise dispatch the `drift-runner` subagent (it is the only subagent this skill uses):

```
Agent({
  subagent_type: "drift-runner",
  prompt: JSON.stringify({
    body_path: "<absolute path to $RUN_DIR/body.txt>",
    frontmatter_path: "<absolute path to $RUN_DIR/frontmatter.json>",
    rules_path: "<absolute path to $RUN_DIR/rules.json>",
    conflicts_path: "<absolute path to $RUN_DIR/conflicts.json>",
    gaps_path: "<absolute path to $RUN_DIR/gaps.json>",
    dominances_path: "<absolute path to $RUN_DIR/dominances.json>",
    out_path: "<absolute path to $RUN_DIR/drift.json>",
    probes_ref: "<absolute path to skills/prompt-check/references/probes.md>"
  }),
  description: "drift analysis for " + BASENAME
})
```

`drift-runner` generates scenarios, simulates the model on each, judges outputs, and writes `$RUN_DIR/drift.json` with shape `{scenarios, runs, verdicts}`. The skill never decomposes drift inline because it is the only step whose token cost scales with prompt length.

## Phase 6 — Turkish phonetic lens (conditional)

**Run this phase only if `frontmatter.tr_phonetic == true`.** Read `references/tr-phonetic.md` end-to-end before generating any findings — its skip rules, whitelist, and "no semantic translation" hard rule are mandatory and not repeated here.

Each TR finding declares **one of three `fix_kind` values**:

- `replace` — real textual error (typo, double-space, missing/extra punctuation). `suggested_fix` populated.
- `pronunciation_hint` — foreign word / risky abbreviation / brand whose written text must STAY. `suggested_fix` empty; `pronunciation_entry` populated.
- `advisory` — borderline / judgement call. Reported only; no automatic apply.

Write `$RUN_DIR/tr_phonetic.json`:

```json
{
  "findings": [
    {
      "id": "T1",
      "kind": "number_readability | abbreviation | foreign_word | punctuation",
      "fix_kind": "replace | pronunciation_hint | advisory",
      "severity": "low | medium | high",
      "line": 42,
      "current_excerpt": "...",
      "suggested_fix": "...",
      "pronunciation_entry": {
        "term": "DHL",
        "phonetic": "de-ha-el",
        "alt_translation": null,
        "note": null
      },
      "rationale": "..."
    }
  ]
}
```

Only one of `suggested_fix` / `pronunciation_entry` is populated per finding (the other is `""` or `null`).

**Never** propose semantic translations (e.g. `pound → İngiliz lirası`). Phonetic hints (`pound → paund`) and optional `alt_translation` metadata are allowed; outright vocabulary substitution is not.

## Phase 7 — Render outputs

Read everything you wrote into `$RUN_DIR/` so far. Build a single merged `findings.json` and a human-readable `report.md`. Both are line-anchored so the user can later say "düzelt bunları" and you can apply each fix by line.

### `$RUN_DIR/findings.json`

```json
{
  "prompt_path": "<absolute path>",
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
      "fix_kind": "replace|pronunciation_hint|advisory",
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
      "phonetic": "de-ha-el",
      "alt_translation": null,
      "note": null,
      "source_finding_ids": ["T3"]
    }
  ]
}
```

`findings[]` is sorted by `line` ascending, then by severity descending. For each finding:
- `fix_kind: "replace"` → apply-mode edits the line so it produces `suggested_fix` instead of `current_excerpt`.
- `fix_kind: "pronunciation_hint"` → apply-mode inserts the entry into a pronunciation guide block in the prompt; the original line stays untouched.
- `fix_kind: "advisory"` → no automatic apply.

For non-TR lenses (`conflict`, `dominance`, `gap`, `drift`), `fix_kind` is always `"replace"` (when `suggested_fix` is non-empty) or `"advisory"` (when empty). Only the TR lens uses `pronunciation_hint`.

`pronunciation_map` is the deduplicated merge of every `pronunciation_entry` across TR findings, ready for apply-mode to inject.

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

`report.md` is the canonical user-facing artefact. If `frontmatter.output` contains `findings_json` but not `markdown`, still write `report.md` — it costs nothing and is the doc humans read. If `output` contains `json`, write the merged report as `$RUN_DIR/report.json` (same shape as findings.json plus a `body_lines` field with the numbered body). If `output` contains `html`, write `report.html` as a real HTML render (use proper `<table>`, `<h2>`, etc.) — not `<pre>`-wrapped markdown.

**`inline` output is no longer supported.** If a frontmatter or env var requests it, emit a one-line warning in the terminal summary ("`inline` mode removed in v0.2 — see report.md") and proceed with `markdown` instead.

## Phase 8 — Terminal summary

After all writes succeed, print exactly this block to the user:

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

If the user, in the same or a later session, says "fix these" / "düzelt bunları" / equivalent and points at a run directory (or none — then assume `latest`), read `<run-dir>/findings.json`, then do two passes in order:

### Pass 1 — `replace` findings (line-level substitutions)

For each finding with `fix_kind == "replace"` and non-empty `suggested_fix`:

1. Read the prompt file fresh.
2. Locate the line via `line` number AND `current_excerpt` substring match (both must agree — if not, skip and report).
3. Apply the substring replacement.
4. Write the file back.

Group conflicting suggestions: if two findings target the same line, present a choice rather than silently applying one. Never apply a finding whose `suggested_fix` is empty — those are advisory only.

### Pass 2 — `pronunciation_map` injection (idempotent block)

If `findings.json.pronunciation_map` is non-empty, write or update a single pronunciation guide block in the prompt:

Block format:

```
<!-- promptchecker:pronunciation-guide:start -->
## Okunuş rehberi (TTS)
- `DHL` → "de-ha-el"
- `D&R` → "de ve er"
- `Hebrew` → "hebru" (alternatif: İbrani)
- `iPhone` → "ay-fon"
<!-- promptchecker:pronunciation-guide:end -->
```

Insertion priority (first match wins):

1. **Existing marker block** — if the prompt already contains `<!-- promptchecker:pronunciation-guide:start --> … <!-- end -->`, replace its body with the current map. This is the idempotent re-run path.
2. **Existing section heading** — if the prompt has a section titled `Pronunciation guide`, `Okunuş rehberi`, `Telaffuz`, or `TTS PRONUNCIATION NOTES` (heading or all-caps line), append the map under it (with markers) and report which section was extended.
3. **Fallback** — insert the block immediately after the frontmatter (or at the top of the body if no frontmatter).

Never inject the block inside YAML frontmatter, fenced code blocks, quoted transcripts, or markdown tables. After writing, surface to the user: "Pronunciation guide updated: N entries (added M, kept K)."

### Diff surface

After both passes, show a short summary in the terminal:
- Replace pass: `N findings applied, M skipped (line/excerpt mismatch), 0 conflicts`.
- Pronunciation pass: `N entries in guide (M new this run)`.

If both passes are empty, say so explicitly — do not pretend to have done work.

## Don'ts

- Don't extract frontmatter with an LLM pass; use Bash/Python — it's deterministic and free.
- Don't read the original prompt more than once per phase; pass `body.txt` between steps.
- Don't dispatch a subagent for any lens other than drift. The first four lenses fit comfortably in a single context.
- Don't write outside `$RUN_DIR/` (except for `.promptchecker.json` in Phase 0, with the user's explicit consent through the wizard).
- Don't modify the original prompt file in any phase — only Apply-mode does that, and only when the user explicitly asks.
- Don't run the wizard if `.promptchecker.json` already exists. The user owns that file.
