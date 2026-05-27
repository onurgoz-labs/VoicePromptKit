# PromptChecker

A Claude Code plugin that audits your prompts the way a strict reviewer would: it finds rules that **contradict each other**, rules that **silently override others**, **gaps** the prompt itself raises but never resolves, **behavioural drift** between what the prompt asks and what the model actually does, and — for Turkish voice agents — **phonetic problems** that break text-to-speech.

You run it on any prompt file (system prompt, Claude Code subagent definition, Vapi voice script, chained workflow) and get back two artefacts per audit run: a human-readable `report.md` and a structured `findings.json` that Claude can apply back as edits when you say "fix these".

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

That's it. No frontmatter required, no flags. The plugin uses sensible defaults: tests against `claude-opus-4-7`, writes a markdown report and a findings JSON to `.promptcheck/<basename>/run-NNN/`. The drift lens generates as many adversarial scenarios as `expand_count + anchor_count + min(2, conflicts + gaps)` (default `expand_count` is 3, so a prompt with no anchors and a handful of conflicts ends up with ~5).

After the run, either say **"fix these"** / **"düzelt bunları"** in the same Claude session, or run **`/prompt-check-apply [run-id]`** explicitly. Claude reads the findings, verifies the prompt's SHA256 still matches the snapshot taken at audit time, then applies the fixes line-by-line. If the prompt has been edited since the audit, apply-mode refuses and asks you to re-run `/prompt-check`.

## What it looks for — the five lenses

| Lens | Looks for | Always on? |
|---|---|---|
| **Conflict** | Rules that logically contradict each other ("always formal" + "be casual and friendly") | yes |
| **Dominance** | Rules that silently override others through position, length, specificity, recency, or role-override patterns ("ignore previous instructions…") | yes |
| **Gap** | Undefined edge cases (incomplete conditionals) and ambiguous terms ("appropriate", "reasonable") that the prompt's own rules raise | yes |
| **Drift** | Behavioural mismatch between the prompt's stated rules and the model's actual output, surfaced by adversarial scenarios | only when anchors / conflicts / role-overrides exist (skipped otherwise) |
| **TR phonetic** | Numbers, abbreviations, foreign words, and pacing problems that break Turkish text-to-speech | opt-in via `tr_phonetic: true` frontmatter or project config |

### TR phonetic — how fixes are applied

The TR lens produces **three kinds of finding**: `replace` (real typos / punctuation errors that get substituted in place), `pronunciation_hint` (foreign words and risky abbreviations whose **written text stays** — the entry goes into a pronunciation guide block instead), and `advisory` (judgement calls; reported only). The lens never translates: `pound → paund` is a phonetic hint; `pound → İngiliz lirası` is forbidden semantic substitution.

When you say "fix these", apply-mode runs two passes: replace pass for line-level substitutions, then a pronunciation-guide injection that updates an idempotent block in your prompt (between `<!-- promptchecker:pronunciation-guide:start -->` and `<!-- end -->`). Re-runs update the same block instead of duplicating it. If your prompt already has a `TTS PRONUNCIATION NOTES` / `Okunuş rehberi` / `Telaffuz` section, the block extends that section rather than creating a new one.

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
    │   ├── conflicts.json
    │   ├── dominances.json
    │   ├── gaps.json
    │   ├── drift.json          (or {"skipped_reason": ...})
    │   ├── tr_phonetic.json    (only when tr_phonetic enabled)
    │   ├── findings.json       ← merged, line-anchored, used by "fix these"
    │   └── report.md           ← human-readable summary
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

### `findings.json` — what Claude applies

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

When you say "fix these", Claude runs **two passes**: Pass 1 applies every `replace` finding (line + excerpt must agree; ambiguous occurrences are skipped); Pass 2 writes or updates a single marker-delimited pronunciation block at the top of the prompt (or migrates an existing legacy `TTS PRONUNCIATION NOTES` / `Okunuş rehberi` block into managed format, preserving every entry). Findings with empty `suggested_fix` and `fix_kind: advisory` are never auto-applied.

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

The plugin runs one orchestrating skill that fans out to three subagents for the lens work. `static-lens-runner` is dispatched on every run; `drift-runner` and `tr-phonetic-runner` are conditional (drift only when anchors / conflicts / role-overrides exist AND `expand_count > 0`; TR only when `tr_phonetic: true`). Phases 4-6 happen in parallel where possible — the skill dispatches all eligible subagents in one fan-out and awaits all results before Phase 7.

1. **Phase 0** — First-run wizard or load existing `.promptchecker.json`.
2. **Phase 1** — Allocate a fresh `run-NNN` directory (atomic; `latest` symlink is updated only on success).
3. **Phase 2** — Parse frontmatter deterministically and split body. Stores `body_line_offset` and `prompt_sha256` for line-mapping and stale-audit checks.
4. **Phase 3** — Extract atomic, line-anchored rules from `body.txt`.
5. **Phase 4** — Dispatch `static-lens-runner` (subagent) which applies conflict + dominance + gap lenses and writes the three JSON outputs (`conflicts.json`, `dominances.json`, `gaps.json`).
6. **Phase 5** — In parallel with Phase 4, if warranted (anchors / conflicts / role-overrides present AND `expand_count > 0`), dispatch `drift-runner` (subagent) for adversarial scenarios + judging.
7. **Phase 6** — In parallel with Phases 4-5, if `tr_phonetic: true`, dispatch `tr-phonetic-runner` (subagent) which seeds from existing pronunciation blocks and scans the body for new advisory findings.
8. **Phase 7** — Render `report.md` + `findings.json` (line numbers translated back to the original prompt file; `prompt_sha256` carried through).
9. **Phase 8** — Update `latest` symlink (commit point), print terminal summary.

## Architecture

The plugin is pure Claude Code skill orchestration. No Node, no TypeScript, no `npm install`, no `node_modules`, no `package.json` — just one skill file, three references, and three subagent definitions (one always dispatched, two conditional).

```
.claude-plugin/
├── plugin.json
└── marketplace.json
commands/
└── prompt-check.md
skills/
└── prompt-check/
    ├── SKILL.md
    └── references/
        ├── lens-rules.md       (conflict/dominance/gap/drift criteria)
        ├── tr-phonetic.md      (Turkish TTS rules)
        └── probes.md           (drift probe templates)
agents/
├── drift-runner.md             (conditional — adversarial scenarios + judging)
├── static-lens-runner.md       (always dispatched — conflict + dominance + gap)
└── tr-phonetic-runner.md       (conditional — advisory-only TR lens)
examples/
├── sample-system.md
├── sample-agent.md
└── sample-vapi.md              (dogfeeds tr_phonetic: true)
```

All cross-phase state is exchanged via JSON files under `.promptcheck/<basename>/run-NNN/`. The orchestrating skill reads its own writes; each subagent reads paths it is given and writes only the JSON artefacts assigned to it.

## License

MIT
