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

That's it. No frontmatter required, no flags. The plugin uses sensible defaults: tests against `claude-opus-4-7`, generates up to 3 adversarial scenarios, writes a markdown report and a findings JSON to `.promptcheck/<basename>/run-NNN/`.

After the run, say **"fix these"** (or **"düzelt bunları"**) in the same Claude session and Claude will read the findings, match each line + excerpt in the prompt file, and apply the suggested fixes. No copy-paste, no second session.

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
  "run_id": "run-003",
  "generated_at": "2026-05-17T19:42:00Z",
  "summary": { "rules": 50, "conflicts": {"total": 10, "high": 4}, ... },
  "findings": [
    {
      "id": "C1",
      "lens": "conflict",
      "severity": "high",
      "line": 15,
      "related_lines": [15, 16],
      "current_excerpt": "Always be formal and use professional language at all times.",
      "suggested_fix": "Maintain a professional but warm register.",
      "rationale": "Tone contradiction with R2 on line 16.",
      "rule_ids": ["R1","R2"]
    }
  ]
}
```

When you say "fix these", Claude reads this file, locates each finding by `line` + `current_excerpt` (both must agree), applies the `suggested_fix`, and shows you a diff. Findings with empty `suggested_fix` are advisory and never auto-applied.

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
4. **Output formats** — multi-select from `markdown`, `findings_json`, `json`, `html`.
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
| `PROMPTCHECKER_OUTPUT` | Comma-separated subset of `markdown,findings_json,json,html` | project config → `markdown,findings_json` |
| `PROMPTCHECKER_EXPAND_COUNT` | Drift scenarios beyond anchor + conflict budget | project config → `3` |
| `PROMPTCHECKER_TR_PHONETIC` | Truthy (`1/true/yes/on`) enables the Turkish phonetic lens | project config → `false` |
| `PROMPTCHECKER_EXECUTOR` | Reserved for future executor variants; current value is `inline` | `inline` |

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

1. **Phase 0** — Compute `run-NNN` directory and update `latest` symlink.
2. **Phase 1** — Parse frontmatter deterministically (Python one-liner, with a no-PyYAML fallback) and split body.
3. **Phase 2** — Extract atomic, line-anchored rules.
4. **Phase 3** — Apply conflict, dominance, gap lenses inline.
5. **Phase 4** — If warranted, dispatch `drift-runner` to generate scenarios, simulate the prompt, judge outputs.
6. **Phase 5** — If `tr_phonetic: true`, apply Turkish phonetic lens inline.
7. **Phase 6** — Render `report.md` + `findings.json` (plus optional `report.json` / `report.html`).
8. **Phase 7** — Print a terminal summary with paths and the apply-mode hint.

## Architecture

The plugin is pure Claude Code skill orchestration. No Node, no TypeScript, no `npm install`, no `node_modules`, no `package.json` — just one skill file, three references, and one optional subagent definition.

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
└── drift-runner.md             (only subagent; only dispatched when needed)
examples/
├── sample-system.md
├── sample-agent.md
└── sample-vapi.md              (dogfeeds tr_phonetic: true)
```

All cross-phase state is exchanged via JSON files under `.promptcheck/<basename>/run-NNN/`. The skill reads its own writes; the `drift-runner` subagent reads paths it is given and writes exactly one file.

## License

MIT
