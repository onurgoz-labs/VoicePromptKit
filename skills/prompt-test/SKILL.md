---
name: prompt-test
description: Runs the test anchors stored in a prompt file's frontmatter as regression scenarios and reports pass/fail per anchor. Use when the user runs /prompt-test, asks to "test the prompt against saved scenarios", "run anchor regression", or wants to verify that a prompt edit hasn't broken expected behaviour. Reuses the drift-runner subagent in regression_only mode — no scenario generation, no LLM analysis, just anchors → assistant turns → assertions.
---

# prompt-test

You take a prompt file `$1`, read its `frontmatter.anchors[]`, and run each anchor as a regression test scenario via the existing `drift-runner` subagent (in `regression_only: true` mode). The output is a pass/fail table — one row per anchor — and a `drift.json` artefact under `.promptcheck/<basename>/test-NNN/`.

Anchors are created by `/prompt-chat` (interactive simulator with `/save` + `/commit`) or by manual YAML editing of the prompt file's frontmatter. `/prompt-test` only **runs** them; it never creates or modifies them.

## Inputs you have

- `$1` — relative or absolute path to the prompt file under test.
- `agents/drift-runner.md` — reused for the actual simulation + judging (no new runner; `regression_only: true` flag changes its behaviour).
- `skills/prompt-check/references/probes.md` — referenced by drift-runner for the regression probe template.

## Phase 0 — Bootstrap

Read `$1`, parse its frontmatter (re-use the exact Python heredoc pattern from `skills/prompt-check/SKILL.md` Phase 2), write a run directory under `.promptcheck/<basename>/test-NNN/`.

```bash
ABS_PROMPT=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
BASENAME=$(basename "$1" | sed 's/\.[^.]*$//')
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PROMPT_DIR="$REPO_ROOT/.promptcheck/$BASENAME"
mkdir -p "$PROMPT_DIR"

# Atomic test-NNN allocation (same pattern as /prompt-chat and /prompt-check).
ATTEMPT=1
while [ "$ATTEMPT" -le 100 ]; do
  N=$(ls -1 "$PROMPT_DIR" 2>/dev/null | grep -c '^test-')
  NEXT_NUM=$((N + ATTEMPT))
  RUN_NAME=$(printf 'test-%03d' "$NEXT_NUM")
  RUN_DIR="$PROMPT_DIR/$RUN_NAME"
  if mkdir "$RUN_DIR" 2>/dev/null; then break; fi
  ATTEMPT=$((ATTEMPT + 1))
done
if [ "$ATTEMPT" -gt 100 ]; then
  echo "error: could not allocate a free test-NNN slot in $PROMPT_DIR"
  exit 1
fi

echo "RUN_DIR=$RUN_DIR"
echo "BASENAME=$BASENAME"
```

Parse frontmatter + body. The full pattern is in `/prompt-check` Phase 2; the minimal subset `/prompt-test` needs:

```bash
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

try:
    import yaml
    fm = yaml.safe_load(raw_fm) or {} if raw_fm else {}
except Exception:
    fm = {}

# v0.5.1: read anchors from sidecar (<prompt>.anchors.yaml), falling back to
# frontmatter.anchors[] with deprecation warning. Helper handles schema
# validation, silence_input sugar expansion, and turn alternation checks.
anchors = []
anchors_source = 'none'
anchor_warnings = []
try:
    _r = subprocess.run(
        [sys.executable, os.path.join(repo_root, 'bin', 'read-anchors.py'), prompt_path],
        capture_output=True, text=True, timeout=30,
    )
    if _r.returncode == 0:
        _p = json.loads(_r.stdout)
        anchors = _p.get('anchors', [])
        anchors_source = _p.get('source', 'none')
        anchor_warnings = _p.get('warnings', [])
    else:
        anchors = fm.get('anchors') or []
        anchors_source = 'frontmatter' if anchors else 'none'
        anchor_warnings = [f"anchor reader failed (exit {_r.returncode}): {_r.stderr.strip()}"]
except Exception as _e:
    anchors = fm.get('anchors') or []
    anchors_source = 'frontmatter' if anchors else 'none'
    anchor_warnings = [f"anchor reader exception: {_e}"]

resolved = {
    'target_model':     fm.get('target_model') or 'claude-opus-4-7',
    'judge_model':      fm.get('judge_model') or 'claude-haiku-4-5-20251001',
    'report_language':  (fm.get('report_language') or 'tr').lower(),
    'expand_count':     int(fm.get('expand_count') or 3),
    'max_char_limit':   int(fm.get('max_char_limit') or 50000),
    'body_char_count':  len(body),
    'body_line_offset': body_line_offset,
    'prompt_sha256':    prompt_sha256,
    'anchors':          anchors,
    'anchors_source':   anchors_source,
    'anchor_warnings':  anchor_warnings,
}
if resolved['report_language'] not in ('tr', 'en'):
    resolved['report_language'] = 'tr'
resolved['compact_mode'] = (
    resolved['max_char_limit'] > 0 and len(body) > resolved['max_char_limit']
)

with open(os.path.join(run_dir, 'frontmatter.json'), 'w', encoding='utf-8') as f:
    json.dump(resolved, f, indent=2, ensure_ascii=False)
with open(os.path.join(run_dir, 'body.txt'), 'w', encoding='utf-8') as f:
    f.write(body)

print(f"ANCHOR_COUNT={len(anchors)}")
print(f"REPORT_LANGUAGE={resolved['report_language']}")
print(f"COMPACT_MODE={'true' if resolved['compact_mode'] else 'false'}")
PY
```

## Phase 1 — Anchor check

If `ANCHOR_COUNT == 0`, surface a guiding message and exit without dispatching:

**TR:**
```
Bu prompt'ta hiç anchor (test senaryosu) yok.

Önce /prompt-chat <prompt> ile prompt'la konuş, beğendiğin turları /save ile
kaydet, sonra /commit ile frontmatter'a yaz. /prompt-test sonra çalıştırılabilir.

Veya prompt dosyasının frontmatter'ına manuel olarak `anchors:` bloğu ekleyebilirsin
(şema: input, expect_contains, expect_not_contains, rubric, opsiyonel context).
```

**EN:**
```
This prompt has no anchors (test scenarios).

First run /prompt-chat <prompt> to converse with the prompt, /save the turns you
like, and /commit to write them to frontmatter. /prompt-test runs them afterward.

Or edit the prompt's frontmatter directly to add an `anchors:` block (schema:
input, expect_contains, expect_not_contains, rubric, optional context).
```

Exit cleanly. No run directory deletion — the bootstrap artefacts (frontmatter.json, body.txt) stay for debugging.

If `ANCHOR_COUNT > 0`, print:

- TR: `<N> anchor bulundu. drift-runner regression mode'da çalıştırıyorum...`
- EN: `<N> anchor(s) found. Running drift-runner in regression-only mode...`

…and continue to Phase 2.

## Phase 2 — drift-runner regression-only dispatch

Spawn the existing `drift-runner` subagent with `regression_only: true`. No other static-lens inputs needed (the runner accepts `null` for `rules` / `conflicts` / `gaps` / `dominances` when `regression_only` is set — see `agents/drift-runner.md` Step 1).

```javascript
Agent({
  subagent_type: "drift-runner",
  prompt: JSON.stringify({
    inputs: {
      body:             "<absolute path to $RUN_DIR/body.txt>",
      frontmatter:      "<absolute path to $RUN_DIR/frontmatter.json>",
      rules:            null,
      conflicts:        null,
      gaps:             null,
      dominances:       null,
      probes_ref:       "<absolute path to skills/prompt-check/references/probes.md>",
      regression_only:  true,
      compact_mode:     <bool from frontmatter.compact_mode>,
      max_char_limit:   <int from frontmatter.max_char_limit>,
      section_index:    null,
      report_language:  "<string from frontmatter.report_language>",
      target_model:     "<string from frontmatter.target_model, default claude-opus-4-7>",
      judge_model:      "<string from frontmatter.judge_model, default claude-haiku-4-5-20251001>"
    },
    output_path: "<absolute path to $RUN_DIR/drift.json>"
  }),
  description: "regression test for " + BASENAME,
  isolation: "worktree"
})
```

Wait for the subagent to return. Read `$RUN_DIR/drift.json` for the verdicts.

## Phase 3 — Render pass/fail table

Read `drift.json` and build a markdown table — one row per anchor.

```bash
python3 - "$RUN_DIR" <<'PY'
import sys, json, os
run_dir = sys.argv[1]
fm = json.load(open(os.path.join(run_dir, 'frontmatter.json'), encoding='utf-8'))
report_language = fm.get('report_language', 'tr')

try:
    drift = json.load(open(os.path.join(run_dir, 'drift.json'), encoding='utf-8'))
except Exception as e:
    print(f"ERROR: could not read drift.json — {e}", file=sys.stderr)
    sys.exit(1)

scenarios = drift.get('scenarios') or []
verdicts = {v['scenario_id']: v for v in (drift.get('verdicts') or [])}
runs = {r['scenario_id']: r for r in (drift.get('runs') or [])}

total = len(scenarios)
passed = sum(1 for v in verdicts.values() if v.get('pass'))
failed = total - passed

# Table headers per report_language. v0.5.1 gains a `type` column to
# distinguish single-turn anchors (tek/single) from flow anchors (akış/flow).
if report_language == 'tr':
    headers = ['id', 'tür', 'input / name', 'geçti', 'puan', 'sebepler']
    type_label = {'regression': 'tek', 'flow_regression': 'akış'}
    title = f"PromptChecker test — {os.path.basename(run_dir)}"
    totals_line = f"Toplam: {total} anchor, {passed} geçti, {failed} kaldı."
    detail_hint = f"Detay: {os.path.basename(run_dir)}/drift.json"
else:
    headers = ['id', 'type', 'input / name', 'pass', 'score', 'reasons']
    type_label = {'regression': 'single', 'flow_regression': 'flow'}
    title = f"PromptChecker test — {os.path.basename(run_dir)}"
    totals_line = f"Total: {total} anchors, {passed} passed, {failed} failed."
    detail_hint = f"Details: {os.path.basename(run_dir)}/drift.json"

def short(s, n=60):
    s = (s or '').replace('\n', ' ').strip()
    return (s[:n] + '…') if len(s) > n else s

def display_label(scenario):
    """Flow scenarios show their `name` (or the first user_input turn's content)
    instead of `input`. Single-turn scenarios keep showing `input`."""
    if scenario.get('kind') == 'flow_regression':
        name = scenario.get('name')
        if name:
            return name
        # Fallback: first user_input turn's content
        for turn in scenario.get('turns', []) or []:
            if turn.get('kind') == 'user_input':
                return short(turn.get('content', ''))
        return '(unnamed flow)'
    return short(scenario.get('input', ''))

print(title)
print()
print('| ' + ' | '.join(headers) + ' |')
print('|' + '|'.join(['---'] * len(headers)) + '|')
for s in scenarios:
    sid = s.get('id', '?')
    skind = s.get('kind', 'regression')
    type_col = type_label.get(skind, skind)
    label = display_label(s)
    v = verdicts.get(sid, {})
    p = '✅' if v.get('pass') else '❌'
    score = f"{v.get('score', 0):.2f}"
    reasons_list = v.get('reasons') or []
    reasons = short('; '.join(reasons_list), 120) if reasons_list else '-'
    # Escape pipes for markdown table.
    label = label.replace('|', '\\|')
    reasons = reasons.replace('|', '\\|')
    print(f"| {sid} | {type_col} | {label} | {p} | {score} | {reasons} |")
print()
print(totals_line)
print(detail_hint)

# Surface anchor source + warnings (v0.5.1).
anchors_source = fm.get('anchors_source')
if anchors_source == 'frontmatter':
    if report_language == 'tr':
        print()
        print(f"⚠ Anchor'lar frontmatter'dan okundu — <prompt>.anchors.yaml dosyasına taşımayı düşün.")
    else:
        print()
        print(f"⚠ Anchors read from frontmatter — consider migrating to <prompt>.anchors.yaml.")
for w in (fm.get('anchor_warnings') or []):
    print(f"  - {w}")
PY
```

Render this output verbatim to the user. The table is the primary artefact of `/prompt-test`; everything else is debugging context.

## Phase 4 — Failure follow-up (optional)

If `failed > 0`, ask the user what to do next. Skip this phase if all anchors passed.

```
question (TR): "<F> anchor başarısız oldu. Ne yapmak istiyorsun?"
question (EN): "<F> anchor(s) failed. What next?"
header:        "Next step"
multiSelect:   false
options:
  - label: "Detayları gör" | "See details"
    description: "Tüm failed verdict'lerin reasons[] alanını tek tek aç."
  - label: "/prompt-chat ile incele" | "Investigate in /prompt-chat"
    description: "Failed anchor input'unu interaktif chat'te yeniden çalıştır."
  - label: "/prompt-check ile audit yap" | "Audit with /prompt-check"
    description: "Lens analizini çalıştır — bir conflict / dominance / gap mı sebep oldu?"
  - label: "Şimdilik kapat" | "Close for now"
    description: "Çıkış. Detaylar drift.json'da kalır."
```

Dispatch per answer:
- **See details** — for each failed scenario, print the full verdict block (assertion failures listed + rubric reason + the actual assistant output that the model produced).
- **Investigate** — print the command the user can run: `claude /prompt-chat <prompt>` plus a note "Failed anchor input: <input>". Do not auto-dispatch (user runs it themselves).
- **Audit** — print the command: `claude /prompt-check <prompt>`. Do not auto-dispatch.
- **Close** — exit silently.

## Phase 5 — Exit summary

```
PromptChecker test tamamlandı — test-NNN

- Anchor: <N> (<passed> geçti, <failed> kaldı)
- Run dir: .promptcheck/<basename>/test-NNN/
- Detay: <run-dir>/drift.json
```

EN variant: same structure with English labels.

If `failed == 0`, append:

- TR: `Tüm anchor'lar geçti. Prompt'un mevcut davranışı korunmuş.`
- EN: `All anchors passed. The prompt's expected behaviour is preserved.`

If `failed > 0`, append:

- TR: `<failed> anchor başarısız. /prompt-chat ile failed input'u dene, veya /prompt-check ile audit yap.`
- EN: `<failed> anchor(s) failed. Try the failed input in /prompt-chat, or audit with /prompt-check.`

## Invariants

- **Read-only on the prompt file.** `/prompt-test` never writes to `$1` or its frontmatter. To modify anchors, use `/prompt-chat /save` + `/commit` or manual YAML edit.
- **drift-runner is reused, not replaced.** All assertion semantics, rubric judging, and output shape (drift.json) are identical to what `/prompt-check` produces — the only difference is `regression_only: true` skips the non-regression probes.
- **Run dir is durable.** Even if drift-runner fails mid-way, `body.txt` and `frontmatter.json` remain in `test-NNN/` for debugging.
- **No latest symlink.** Unlike `/prompt-check`, `/prompt-test` does NOT create `.promptcheck/<basename>/latest` pointing at test-NNN — that symlink is reserved for the most recent successful audit, not test run. Test runs are listed by directory name (`test-001`, `test-002`, …).

## Failure modes

- **`drift-runner` errors** — read the `warnings[]` array in drift.json (if it exists) and surface them to the user. Common cases: simulator unable to produce a turn for a scenario (record `output: ""`); rubric inconclusive (default to fail).
- **drift.json missing after subagent returns** — surface `ERROR: drift-runner did not produce output. Check the subagent's status line above.` and exit. No retry.
- **Anchor has malformed schema** (e.g. `input` missing) — drift-runner will skip it with a warning; report the warning verbatim to the user in Phase 3's output (above the table).
- **`/prompt-test` invoked on a prompt with no frontmatter** — Phase 0 yields `anchors: []`; Phase 1 surfaces the "no anchors" message. No crash.
