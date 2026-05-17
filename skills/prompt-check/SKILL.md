---
name: prompt-check
description: Audit a prompt file (system prompt, agent definition, voice script, chained workflow) across four lenses — conflict, dominance, gap, drift — plus an optional Turkish phonetic lens for voice agents. Use when the user invokes /prompt-check, asks to "audit a prompt", "check this prompt for contradictions / silent overrides / gaps / drift / voice readability", or passes a path to a prompt file for review. Produces line-anchored findings as `report.md` + `findings.json` in an isolated run directory. Never modifies the original prompt file.
---

# prompt-check

You audit a prompt file at the path supplied as `$1`. Work in a single context. Read the prompt once. Analyse all four (or five) lenses inline. Write artefacts under an isolated run directory. **Never modify the original prompt file.**

## Inputs you have

- `$1` — relative or absolute path to the prompt file under audit.
- `references/lens-rules.md` — full criteria for each lens (read on first use).
- `references/tr-phonetic.md` — Turkish phonetic rules (read only if `tr_phonetic: true`).
- `references/probes.md` — adversarial probe templates (read only if drift phase runs).

## Phase 0 — Working directory + versioning

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

## Phase 1 — Frontmatter (deterministic, not LLM)

Extract YAML frontmatter with a single Bash call so the result is deterministic and cheap. Write `$RUN_DIR/frontmatter.json` and `$RUN_DIR/body.txt`.

```bash
python3 - "$ABS_PROMPT" "$RUN_DIR" <<'PY'
import sys, re, json, os
prompt_path, run_dir = sys.argv[1], sys.argv[2]
text = open(prompt_path, encoding='utf-8').read()
m = re.match(r'^---\r?\n(.*?)\r?\n---\r?\n?(.*)$', text, re.DOTALL)
raw_fm, body = (m.group(1), m.group(2)) if m else ('', text)
fm = {}
try:
    import yaml
    fm = yaml.safe_load(raw_fm) or {} if raw_fm else {}
except Exception:
    for line in raw_fm.splitlines():
        if ':' in line and not line.lstrip().startswith('-'):
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip()
env = os.environ.get
fm.setdefault('type', None)
fm.setdefault('target_model', env('PROMPTCHECKER_TARGET_MODEL') or 'claude-opus-4-7')
out = fm.get('output')
if not out:
    out = (env('PROMPTCHECKER_OUTPUT') or 'markdown,findings_json').split(',')
fm['output'] = [str(o).strip() for o in (out if isinstance(out, list) else [out])]
fm['expand_count'] = int(fm.get('expand_count') or env('PROMPTCHECKER_EXPAND_COUNT') or 3)
fm.setdefault('executor', env('PROMPTCHECKER_EXECUTOR') or 'inline')
fm.setdefault('anchors', [])
fm['tr_phonetic'] = bool(fm.get('tr_phonetic', False))
with open(os.path.join(run_dir, 'frontmatter.json'), 'w', encoding='utf-8') as f:
    json.dump(fm, f, indent=2, ensure_ascii=False)
with open(os.path.join(run_dir, 'body.txt'), 'w', encoding='utf-8') as f:
    f.write(body.lstrip('\n'))
PY
```

If `python3` is unavailable, fall back to reading the file yourself, splitting on the first two `---` lines, and writing the same two files. State the fallback in the terminal summary.

## Phase 2 — Rule extraction (inline)

Read `$RUN_DIR/body.txt`. Number lines starting at 1 from the first non-empty line. Extract every atomic rule, instruction, constraint, or directive into a flat list. Apply the criteria in `references/lens-rules.md` section "Rule extraction" — split compound sentences, preserve absolutes ("always", "never"), use the lowest line where the rule begins.

Hold the rules in memory as JSON. Also write `$RUN_DIR/rules.json` with shape:

```json
{ "rules": [{"id":"R1","category":"behavior|format|tone|policy|persona","text":"...","line":12,"source_excerpt":"..."}] }
```

If you extract zero rules, abort with an error written to `$RUN_DIR/error.txt` and surface that to the user.

## Phase 3 — Four lenses (single pass, inline)

Apply all four lenses in the same context, in sequence. Each lens reads the rule list (and body where noted) and produces a JSON section. Detection criteria for every lens live in `references/lens-rules.md` — consult that document; do not re-state the criteria here.

Produce one in-memory analysis object:

```json
{
  "conflicts":  [{"id":"C1","rule_ids":["R3","R8"],"severity":"low|medium|high","reasoning":"..."}],
  "dominances": [{"id":"D1","dominant_rule_id":"R12","dominated_rule_id":"R3","mechanism":"position|length|specificity|recency|role-override","reasoning":"..."}],
  "gaps":       [{"id":"G1","kind":"undefined_edge_case|ambiguous_term","description":"...","related_rule_ids":["R5"],"severity":"low|medium|high"}]
}
```

Write each section to `$RUN_DIR/{conflicts,dominances,gaps}.json` as you complete it. The drift section is filled by Phase 4.

## Phase 4 — Drift (conditional)

**Skip Phase 4 entirely if all of the following are true:**
- `frontmatter.anchors` is empty AND
- `conflicts` is empty AND
- no `dominance.mechanism == "role-override"` exists.

In that case write `$RUN_DIR/drift.json` as `{"scenarios": [], "runs": [], "verdicts": [], "skipped_reason": "no anchors, conflicts, or role-overrides — drift adds no signal"}` and move on.

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

## Phase 5 — Turkish phonetic lens (conditional)

**Run this phase only if `frontmatter.tr_phonetic == true`.** Read `references/tr-phonetic.md` and apply its four detection categories (number readability, abbreviation expansion, foreign-word transliteration, punctuation & pacing) to `body.txt`. Produce findings with the same `{line, kind, severity, current_excerpt, suggested_fix, rationale}` shape used in Phase 6.

Write `$RUN_DIR/tr_phonetic.json`:

```json
{ "findings": [{"id":"T1","line":42,"kind":"number_readability|abbreviation|foreign_word|punctuation","severity":"low|medium|high","current_excerpt":"100 TL","suggested_fix":"yüz lira","rationale":"..."}] }
```

## Phase 6 — Render outputs

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
      "severity": "low|medium|high",
      "line": 42,
      "related_lines": [42, 47],
      "current_excerpt": "<verbatim from body.txt>",
      "suggested_fix": "<concrete edit — may be empty if lens cannot suggest>",
      "rationale": "<one paragraph, ≤ 240 chars>",
      "rule_ids": ["R3","R8"]
    }
  ]
}
```

`findings[]` is sorted by `line` ascending, then by severity descending. `suggested_fix` is the canonical apply target — when the user later asks to fix the prompt, edit the line at `line` so it produces `suggested_fix` instead of `current_excerpt`.

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

## Phase 7 — Terminal summary

After all writes succeed, print exactly this block to the user:

```
PromptChecker complete — <run-NNN>

- Rules: <N> | Conflicts: <N> (<H> high) | Dominances: <N> | Gaps: <N>
- Drift: <skipped|<N> scenarios, <P> passed, <F> failed>
- TR phonetic: <disabled|<N> findings>

Report:   <relative path to $RUN_DIR/report.md>
Findings: <relative path to $RUN_DIR/findings.json>
Previous runs: .promptcheck/<basename>/ (run-001 … run-NNN)

Say "fix these" or "düzelt bunları" and I will apply suggested_fix entries from findings.json.
```

## Apply-mode (when the user asks to fix)

If the user, in the same or a later session, says "fix these" / "düzelt bunları" / equivalent and points at a run directory (or none — then assume `latest`), read `<run-dir>/findings.json`, then for each finding with a non-empty `suggested_fix`:

1. Read the prompt file fresh.
2. Locate the line via `line` number AND `current_excerpt` substring match (both must agree — if not, skip that finding and report it).
3. Apply the replacement.
4. Write the file back.
5. Surface a short diff in the terminal.

Group conflicting suggestions: if two findings target the same line, present a choice rather than silently applying one. Never apply a finding whose `suggested_fix` is empty — those are advisory only.

## Don'ts

- Don't extract frontmatter with an LLM pass; use Bash/Python — it's deterministic and free.
- Don't read the original prompt more than once per phase; pass `body.txt` between steps.
- Don't dispatch a subagent for any lens other than drift. The first four lenses fit comfortably in a single context.
- Don't write outside `$RUN_DIR/`.
- Don't modify the original prompt file in any phase — only Apply-mode does that, and only when the user explicitly asks.
