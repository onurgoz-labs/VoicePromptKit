# PromptChecker

A Claude Code plugin that audits your prompts the way a strict reviewer would: it finds rules that **contradict each other**, rules that **silently override others**, **gaps** the prompt itself raises but never resolves, **behavioural drift** between what the prompt asks and what the model actually does, and — for Turkish voice agents — **phonetic problems** that break text-to-speech.

You run it on any prompt file (system prompt, Claude Code subagent definition, Vapi voice script, chained workflow) and step through an interactive session: pick the lenses you want, review the findings in one summary table, then decide finding-by-finding which to apply, route to an overlay, dismiss, or discuss. Each run leaves behind a human-readable `report.md`, a structured `findings.json`, and a per-decision audit trail.

The original prompt file is **never modified**. Every run lives in its own numbered directory so you can compare audits across edits.

## Install

```
/plugin marketplace add onurgoz/PromptChecker
/plugin install PromptChecker@onurgoz
```

The plugin auto-loads in every Claude Code session after that. No API keys, no SDK installs — the optional drift lens runs inside a single Claude Code subagent.

## Usage

```
/prompt-check path/to/your/prompt.md
```

PromptChecker opens an interactive session. First it asks which lenses you want to apply (multi-select: conflict, dominance, gap, drift, TR phonetic — pre-checked based on your repo defaults). For `drift`, it asks `expand_count`. Then it dispatches the selected lenses as parallel subagents and shows you a single summary table:

| id | lens | severity | line | excerpt | suggestion |
|----|------|----------|------|---------|------------|
| C1 | conflict | high | 15 | Always be formal... | Maintain a professional but warm register. |
| ...

Then it asks: **"Hangilerini ne yapayım?"** You answer free-form:

```
C1, C3 düzelt; G2 yorum bırak; T1..T5 konuşalım; gerisini atla
```

The grammar accepts Turkish and English keywords:

| Keyword | Meaning |
|---|---|
| `düzelt` / `apply` / `fix` | apply the suggestion to the prompt file |
| `yorum bırak` / `overlay` / `comment` | write the suggestion to `inline-suggestions.md` (prompt file untouched) |
| `atla` / `skip` / `dismiss` | log only; no action |
| `konuşalım` / `discuss` / `talk` | open per-finding sub-dialogue |
| `gerisini atla` / `gerisini yorum` / `gerisini düzelt` | wildcard for all unmarked findings |
| `iptal` / `cancel` | exit and leave session at pending (resume later with `/prompt-check-resume`) |

For findings you say "konuşalım" to, PromptChecker enters a per-finding dialogue: you see the full finding, then choose: accept the default suggestion / revise it yourself / route to overlay / dismiss. The dialogue terminates when every discussed finding has a final status.

**TR phonetic exception:** even if you say `düzelt` for a TR finding, PromptChecker routes it to the overlay instead. Phonetic adjustments and pronunciation choices are voice-design decisions the author owns; false positives are common, and a silent prompt edit can poison a Vapi / ElevenLabs script.

Every decision lands in three places under `.promptcheck/<basename>/run-NNN/`:
- `session.json` — current snapshot (what's pending, what's applied, what's overlay, what's dismissed)
- `decisions.jsonl` — append-only audit log (every action ever taken, with timestamps)
- `inline-suggestions.md` — human-readable overlay of every finding routed to overlay

The original prompt file is **only** modified when you explicitly say `düzelt` on a non-TR finding. Even then, a SHA256 stale-audit guard refuses to apply if the prompt was edited between audit and decision — re-run `/prompt-check <prompt>` to refresh.

Mid-session interruption is fine. Run `/prompt-check-resume` later and it re-enters the summary view filtered to findings with status: pending.

## What it looks for — the five lenses

| Lens | Looks for | Always on? |
|---|---|---|
| **Conflict** | Rules that logically contradict each other ("always formal" + "be casual and friendly") | yes |
| **Dominance** | Rules that silently override others through position, length, specificity, recency, or role-override patterns ("ignore previous instructions…") | yes |
| **Gap** | Undefined edge cases (incomplete conditionals) and ambiguous terms ("appropriate", "reasonable") that the prompt's own rules raise | yes |
| **Drift** | Behavioural mismatch between the prompt's stated rules and the model's actual output, surfaced by adversarial scenarios | only when anchors / conflicts / role-overrides exist (skipped otherwise) |
| **TR phonetic** | Numbers, abbreviations, foreign words, and pacing problems that break Turkish text-to-speech. TR phonetic findings are advisory-only — always routed to overlay, even on `düzelt`. The original prompt file is never modified by them. | opt-in via `tr_phonetic: true` frontmatter or project config |

### TR phonetic — how fixes are applied

The TR lens produces **three kinds of finding**: `replace` (real typos / punctuation errors that get substituted in place), `pronunciation_hint` (foreign words and risky abbreviations whose **written text stays** — the entry goes into a pronunciation guide block instead), and `advisory` (judgement calls; reported only). The lens never translates: `pound → paund` is a phonetic hint; `pound → İngiliz lirası` is forbidden semantic substitution.

TR phonetic findings are advisory-only — even if you say `düzelt` for one in the interactive session, PromptChecker routes it to the overlay (`inline-suggestions.md`) instead of editing the prompt. Phonetic adjustments and pronunciation choices are voice-design decisions the author owns; false positives are common, and a silent prompt edit can poison a Vapi / ElevenLabs script. The suggested `replace` text and the `pronunciation_entry` are both written into the overlay so you can hand-merge them on your terms.

All five lenses live in `skills/prompt-check/SKILL.md` and its `references/`.

## Output layout

Every run gets its own directory. Older runs are preserved so you can diff audits across prompt edits.

```
.promptcheck/
└── <prompt-basename>/
    ├── run-001/
    │   ├── frontmatter.json
    │   ├── body.txt
    │   ├── rules.json
    │   ├── conflicts.json     (if conflict lens selected)
    │   ├── dominances.json    (if dominance lens selected)
    │   ├── gaps.json          (if gap lens selected)
    │   ├── drift.json         (if drift lens selected, or skip reason)
    │   ├── tr_phonetic.json   (if TR lens selected)
    │   ├── findings.json
    │   ├── report.md
    │   ├── session.json            ← NEW: current decision snapshot
    │   ├── decisions.jsonl         ← NEW: append-only audit log
    │   └── inline-suggestions.md   ← NEW: overlay (re-rendered each pass)
    ├── run-002/
    ├── run-003/
    └── latest -> run-003/
```

### `report.md` — what humans read

```markdown
# PromptChecker Report — mainprompt

- **Prompt:** `/Users/onur/repos/.../mainprompt.md`
- **Run:** `run-003`
- **Generated:** 2026-05-17T19:42:00Z
- **Target model:** claude-opus-4-7

## Summary

| Lens | Total | High | Medium | Low |
|---|---|---|---|---|
| Conflict | 10 | 4 | 4 | 2 |
| Dominance | 7 | 3 | 3 | 1 |
| Gap | 13 | 5 | 6 | 2 |
| Drift | 10 scenarios: 5✓ / 5✗ | — | — | — |
| TR phonetic | 8 | 2 | 5 | 1 |

## Findings

### Conflicts
- **L15** [C1 severity=high, R1↔R2] — Tone contradiction: "always formal" vs "be casual and friendly".
  - **Current:** `Always be formal and use professional language at all times.`
  - **Fix:** `Maintain a professional but warm register.`
…
```

### `findings.json` — what the interactive flow reads

```json
{
  "prompt_path": "/abs/path/mainprompt.md",
  "prompt_sha256": "a3f1...c7",
  "run_id": "run-003",
  "generated_at": "2026-05-17T19:42:00Z",
  "summary": { "rules": 50, "conflicts": {"total": 10, "high": 4}, ... },
  "findings": [
    {
      "id": "C1",
      "lens": "conflict",
      "fix_kind": "replace",
      "severity": "high",
      "line": 15,
      "related_lines": [15, 16],
      "current_excerpt": "Always be formal and use professional language at all times.",
      "suggested_fix": "Maintain a professional but warm register.",
      "pronunciation_entry": null,
      "rationale": "Tone contradiction with R2 on line 16.",
      "rule_ids": ["R1","R2"]
    },
    {
      "id": "T3",
      "lens": "tr_phonetic",
      "fix_kind": "pronunciation_hint",
      "severity": "high",
      "line": 858,
      "current_excerpt": "şehir dışı gönderilerde ise DHL kullanıyoruz.",
      "suggested_fix": "",
      "pronunciation_entry": {
        "term": "DHL",
        "strategy": "pronounce",
        "phonetic": "de-ha-el",
        "alt_translation": null,
        "note": null
      },
      "rationale": "TTS reads DHL as English D-H-L.",
      "rule_ids": []
    }
  ],
  "pronunciation_map": [
    { "term": "DHL", "strategy": "pronounce", "phonetic": "de-ha-el", "alt_translation": null, "note": null, "source": "finding", "source_finding_ids": ["T3"] }
  ]
}
```

Each finding declares one of three `fix_kind` values:

- `replace` — substring replacement (`current_excerpt` → `suggested_fix`).
- `pronunciation_hint` — written text untouched; `pronunciation_entry` (with `strategy: pronounce | rephrase | follow_with_translation`) is added to the managed pronunciation guide block in the prompt.
- `advisory` — judgement call; reported only, never auto-applied.

After the summary, you make per-finding decisions in free-form text (see Usage above). The skill carries out each decision in Phase 10.

## Customizing defaults

The plugin's behaviour is layered, highest priority first:

1. **Per-prompt frontmatter** — overrides everything for one prompt.
2. **Environment variables** (`PROMPTCHECKER_*`) — change defaults for every prompt in your shell / Claude Code session.
3. **Project config** (`.promptchecker.json` at repo root) — repo-level defaults shared with your team.
4. **Built-in defaults** — applied when none of the above is set.

### First-run wizard

The first time you run `/prompt-check` in a repo, the skill walks you through a 5-question wizard and saves the answers to `<repo-root>/.promptchecker.json`. Subsequent runs read that file silently. Edit it by hand to change defaults, or delete it to rerun the wizard.

The wizard asks:

1. **Default prompt type** for this repo (`system | agent | vapi | task | chain | unspecified`). Used when a prompt has no `type:` in its frontmatter.
2. **Turkish phonetic lens** active by default? — recommended `true` if you picked `vapi` above, otherwise `false`.
3. **Target model** for reports + drift simulation (`claude-opus-4-7` default, free text accepted).
4. **Output formats** — multi-select from `markdown`, `findings_json`, `json`.
5. **Drift `expand_count`** — how many extra adversarial scenarios beyond the anchor + conflict budget. `0` disables the drift lens entirely.

### Project config (`.promptchecker.json`)

Example for a Turkish VAPI repo:

```json
{
  "$schema": "https://github.com/onurgoz/PromptChecker/blob/master/schema/config.schema.json",
  "default_type": "vapi",
  "target_model": "claude-opus-4-7",
  "output": ["markdown", "findings_json"],
  "expand_count": 4,
  "tr_phonetic": true
}
```

Commit this file so your team gets the same defaults. Unknown keys are ignored (with a one-line warning in the terminal summary), so the file is forward-compatible.

### Environment variables (session-wide overrides)

| Variable | Effect | Falls back to |
|---|---|---|
| `PROMPTCHECKER_TARGET_MODEL` | Model name written into reports | project config → `claude-opus-4-7` |
| `PROMPTCHECKER_OUTPUT` | Comma-separated subset of `markdown,findings_json,json` | project config → `markdown,findings_json` |
| `PROMPTCHECKER_EXPAND_COUNT` | Drift scenarios beyond anchor + conflict budget; `0` disables drift entirely | project config → `3` |
| `PROMPTCHECKER_TR_PHONETIC` | Truthy (`1/true/yes/on`) enables the Turkish phonetic lens | project config → `false` |

Set them in Claude Code's `settings.json` so they apply session-wide without touching your shell rc:

```json
{
  "env": {
    "PROMPTCHECKER_TR_PHONETIC": "true",
    "PROMPTCHECKER_EXPAND_COUNT": "5"
  }
}
```

### Per-prompt frontmatter

```yaml
---
type: vapi                       # overrides project config.default_type for this prompt
target_model: claude-opus-4-7    # overrides every layer for this prompt
output: [markdown, findings_json]
expand_count: 6                  # overrides project config + env-var
tr_phonetic: true                # overrides project config + env-var
anchors:                         # always per-prompt — never inherited
  - input: "I am furious! Your product is garbage!"
    rubric: "de-escalates; remains professional"
  - input: "Can I get a refund 90 days after purchase?"
    expect_contains: ["policy"]
    rubric: "declines politely, cites the 30-day policy"
---
[your prompt body here]
```

Every field is optional. Most users only override `anchors` (per-prompt regression seeds) and let the repo defaults handle everything else.

## Pipeline

The plugin runs one skill end-to-end inside a single Claude context — no chain of round-tripping subagents. Only the drift lens dispatches a subagent (`drift-runner`), and only when there are anchors, conflicts, or role-override dominances; otherwise drift is skipped entirely.

Phase 9 and Phase 10 are the interactive layer — they run automatically after Phase 8 in `/prompt-check` and re-enter from `/prompt-check-resume`.

1. **Phase 0** — First-run wizard or load existing `.promptchecker.json`.
2. **Phase 1** — Allocate a fresh `run-NNN` directory (atomic; `latest` symlink is updated only on success).
3. **Phase 2** — Parse frontmatter deterministically and split body. Stores `body_line_offset` and `prompt_sha256` for line-mapping and stale-audit checks.
4. **Phase 3** — Extract atomic, line-anchored rules from `body.txt`.
5. **Phase 4** — Apply conflict, dominance, gap lenses inline.
6. **Phase 5** — If warranted (anchors / conflicts / role-overrides present AND `expand_count > 0`), dispatch `drift-runner` for adversarial scenarios + judging.
7. **Phase 6** — If `tr_phonetic: true`, seed `pronunciation_map` from existing pronunciation blocks, then scan body for new TR findings.
8. **Phase 7** — Render `report.md` + `findings.json` (line numbers translated back to the original prompt file; `prompt_sha256` carried through).
9. **Phase 8** — Update `latest` symlink (commit point), print terminal summary.
10. **Phase 9** — Render summary table from `findings.json`. Bootstrap `session.json` (all findings start `pending`). Accept free-form decision string from the user, parse it, apply TR routing rule, append each decision to `decisions.jsonl`.
11. **Phase 10** — Process decisions: dismissed (log only), overlay (rebuild `inline-suggestions.md`), applied (SHA256-guarded prompt edits, with auto-conversion to overlay on stale audit or ambiguous occurrences), discussed (per-finding sub-dialogue with accept / revise / overlay / dismiss). Re-render `session.json` snapshot. Print Phase 10 summary.

## Architecture

The plugin is pure Claude Code skill orchestration. No Node, no TypeScript, no `npm install`, no `node_modules`, no `package.json` — one skill file, five references, two commands, and three subagent definitions.

```
.claude-plugin/
├── plugin.json
└── marketplace.json
skills/
└── prompt-check/
    ├── SKILL.md
    └── references/
        ├── lens-rules.md       (conflict/dominance/gap/drift criteria)
        ├── tr-phonetic.md      (Turkish TTS rules)
        ├── probes.md           (drift probe templates)
        ├── dialog-flow.md      (NEW — Phase 9 templates + decision grammar)
        └── overlay-format.md   (NEW — inline-suggestions.md + decisions.jsonl spec)
commands/
├── prompt-check.md
└── prompt-check-resume.md      (NEW — `/prompt-check-apply` retired)
agents/
├── drift-runner.md
├── static-lens-runner.md
└── tr-phonetic-runner.md
examples/
├── sample-system.md
├── sample-agent.md
└── sample-vapi.md              (dogfeeds tr_phonetic: true)
```

All cross-phase state is exchanged via JSON files under `.promptcheck/<basename>/run-NNN/`. The skill reads its own writes; the `drift-runner` subagent reads paths it is given and writes exactly one file.

## License

MIT
