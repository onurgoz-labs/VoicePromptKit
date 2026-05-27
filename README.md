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

| id | lens | sev | section | line | summary |
|---|---|---|---|---|---|
| C1 | conflict | high | 7.2 | 284 | Tone contradiction (R3↔R2) → **Maintain a professional but warm register.** |
| S1 | schema | high | 7 | 280 | Section 5 → 7: Section 6 missing → **Insert Section 6 — Placeholder, OR renumber.** |
| ...

The `summary` column combines short_rationale + short_fix in one line for fast scanning. The full text is in findings.json — Phase 10's konuşalım sub-flow shows it unabridged.

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

**TR phonetic — split by category.** For `foreign_word` and `abbreviation` findings, `düzelt` is auto-routed to the overlay (pronunciation hints are voice-design decisions the author owns — a silent prompt edit can poison a Vapi / ElevenLabs script). For `number_readability` and `punctuation` findings, `düzelt` follows the normal apply flow and modifies the prompt — these are textual corrections like missing commas or malformed Turkish numbers.

Every decision lands in three places under `.promptcheck/<basename>/run-NNN/`:
- `session.json` — current snapshot (what's pending, what's applied, what's overlay, what's dismissed)
- `decisions.jsonl` — append-only audit log (every action ever taken, with timestamps)
- `inline-suggestions.md` — human-readable overlay of every finding routed to overlay

The original prompt file is **only** modified when you explicitly say `düzelt` on a non-TR finding. Even then, a SHA256 stale-audit guard refuses to apply if the prompt was edited between audit and decision — re-run `/prompt-check <prompt>` to refresh.

Mid-session interruption is fine. Run `/prompt-check-resume` later and it re-enters the summary view filtered to findings with status: pending.

## What it looks for — the six lenses

| Lens | Looks for | Always on? |
|---|---|---|
| **Conflict** | Rules that logically contradict each other ("always formal" + "be casual and friendly") | yes |
| **Dominance** | Rules that silently override others through position, length, specificity, recency, or role-override patterns ("ignore previous instructions…") | yes |
| **Gap** | Undefined edge cases (incomplete conditionals) and ambiguous terms ("appropriate", "reasonable") that the prompt's own rules raise | yes |
| **Drift** | Behavioural mismatch between the prompt's stated rules and the model's actual output, surfaced by adversarial scenarios | only when anchors / conflicts / role-overrides exist (skipped otherwise) |
| **TR phonetic** | Numbers, abbreviations, foreign words, and pacing problems that break Turkish text-to-speech. Split by category: `foreign_word` + `abbreviation` routed to overlay (voice-design — prompt text never modified); `number_readability` + `punctuation` follow normal apply flow (`düzelt` modifies the prompt). | opt-in via `tr_phonetic: true` frontmatter or project config |
| **Schema** | Section numbering / ordering / heading consistency. Detects gaps (Section 5 → 7), out-of-order subsections (3.3 then 3.2), orphan subsections (5.1 under Section 4), inconsistent heading styles, missing parent sections, and STEP-numbering gaps. | only when the prompt has numbered section headings (auto-skipped on flat prompts) |

### TR phonetic — split by category

The TR lens splits its four detection categories into two routing buckets, so voice-design decisions stay overlay-only while textual corrections follow the normal apply flow.

- **`foreign_word` + `abbreviation`: advisory-only.** Pronunciation hints for `Gaggia → "gacca"` or `DHL → "de-ha-el"` carry `fix_kind: "advisory"` and always land in the overlay file (`inline-suggestions.md`); the prompt text is never auto-edited, even on `düzelt`. The author hand-merges these into a TTS pronunciation guide block or the voice provider's config. A silent prompt edit (`DHL` becoming `de-ha-el` in the visible script) would corrupt the meaning, so this routing is non-negotiable.
- **`number_readability` + `punctuation`: normal apply flow.** Missing commas, malformed Turkish numbers, monetary spelling (`100 TL → yüz lira`) — these ARE textual fixes. They carry `fix_kind: "replace"`. When the user picks `düzelt` in the Phase 9 dialogue, Phase 10 modifies the prompt file just like a `conflict` or `gap` finding. The user can still route them to overlay via `yorum bırak` per-finding when they want to review by hand.
- **Migration note:** the TR routing rule from earlier versions was over-strict — it forced every TR finding to overlay regardless of category, even when the user explicitly said `düzelt` on a textual fix like a missing comma. v0.4.2 fixes this: only the voice-design categories (`foreign_word`, `abbreviation`) stay advisory.

The lens never translates: `pound → paund` is a phonetic hint; `pound → İngiliz lirası` is forbidden semantic substitution.

### Compact mode for long prompts

When a prompt body exceeds `max_char_limit` (default `50000` chars; configurable via wizard, env var, project config, or per-prompt frontmatter), PromptChecker enters **compact mode** and applies cheaper analysis policies to trade depth for speed:

- **Conflict / Gap lenses:** skip `low` severity findings; keep `medium` and `high`.
- **Dominance lens:** emit only `role-override` and `recency` mechanisms; skip the subtler `position`, `length`, `specificity` effects.
- **Conflict lens pair budget:** pick the 50 most-impactful rules (those with "always", "never", "must", "only", "ignore") and compare only within that set. Caps work at ~1250 comparisons regardless of prompt size.
- **Drift lens:** halve the effective `expand_count` (`max(1, n // 2)`). A 5-scenario drift becomes 2 scenarios. This is the single biggest perf lever for long-prompt audits.
- **Rule extraction (Phase 3):** rule `text` ≤ 100 chars, `source_excerpt` ≤ 120 chars. Trims the payload downstream lenses load.
- **Schema and TR phonetic lenses:** unchanged. Both are heading-level / line-level and cheap regardless of size.

To **disable compact mode entirely**, set `max_char_limit: 0` in your `.promptchecker.json` (or per-prompt frontmatter, or env var). The audit runs at full depth regardless of body size — useful for forensic audits where you want every finding.

To **lower the threshold** (e.g. 25000 chars so compact mode kicks in sooner), set `max_char_limit: 25000` at any layer.

Phase 8's terminal summary reports the body size + threshold + active/inactive state:

```
Body size: 87432 chars [compact mode ACTIVE — exceeds 50000 char threshold]
```

Compact mode is NOT a hard abort — the audit always runs. It only trims which findings are reported and which scenarios drift simulates. The artefact files (`conflicts.json`, `drift.json`, etc.) carry a top-level `compact_mode: true` field + `compact_policy` array so consumers know the policies fired.

### Section-aware findings

For prompts that use numbered section headings (`## SECTION N` + `### N.M`), every finding carries a `section_ref` field pointing to its containing section and subsection. The report.md and inline-suggestions.md surfaces this as a section-aware header instead of the bare line number:

```
### Section 7.2 — L284 [C1 conflict severity=high, R3↔R8] — Tone contradiction...
```

Useful for long prompts (1758 lines like `boyutyayin/mainprompt.md`) where the user otherwise has to map line numbers to sections mentally. Findings outside any numbered section (preambles, flat prompts) show the bare line number with no section prefix.

The section index is built deterministically in Phase 3 of the audit (no LLM cost) and propagated to every lens runner via `inputs.section_index`. Schema lens, conflict, dominance, gap, and TR phonetic findings all attach `section_ref` automatically. Drift findings are behavioural and always carry `section_ref: null`.

All six lenses live in `skills/prompt-check/SKILL.md` and its `references/`.

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
    │   ├── schema.json        (if schema lens selected, with applicability flag)
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
      "fix_kind": "advisory",
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

Each finding declares one of two `fix_kind` values:

- `replace` — substring replacement (`current_excerpt` → `suggested_fix`). Emitted by `conflict`, `dominance`, `gap`, `drift` lenses, and TR phonetic findings with `kind: "number_readability"` or `kind: "punctuation"`.
- `advisory` — reported only, never auto-applied. Emitted by TR phonetic findings with `kind: "foreign_word"` or `kind: "abbreviation"` (the `pronunciation_entry` lands in the overlay for the author to hand-merge into a TTS pronunciation guide).
- `schema` lens emits `fix_kind: "replace"` for every finding (just like conflict / dominance / gap). Most schema findings have `fix_strategy: "structural"` (insert/renumber/reorder requires the Edit tool); `heading_style_inconsistent` is `fix_strategy: "substring"` (clean text replacement of one heading).

After the summary, you make per-finding decisions in free-form text (see Usage above). The skill carries out each decision in Phase 10.

## Customizing defaults

The plugin's behaviour is layered, highest priority first:

1. **Per-prompt frontmatter** — overrides everything for one prompt.
2. **Environment variables** (`PROMPTCHECKER_*`) — change defaults for every prompt in your shell / Claude Code session.
3. **Project config** (`.promptchecker.json` at repo root) — repo-level defaults shared with your team.
4. **Built-in defaults** — applied when none of the above is set.

### First-run wizard

The first time you run `/prompt-check` in a repo, the skill walks you through a 7-question wizard and saves the answers to `<repo-root>/.promptchecker.json`. Subsequent runs read that file silently. Edit it by hand to change defaults, or delete it to rerun the wizard.

The wizard asks:

1. **Default prompt type** for this repo (`system | agent | vapi | task | chain | unspecified`). Used when a prompt has no `type:` in its frontmatter.
2. **Turkish phonetic lens** active by default? — recommended `true` if you picked `vapi` above, otherwise `false`.
3. **Target model** for reports + drift simulation (`claude-opus-4-7` default, free text accepted).
4. **Output formats** — multi-select from `markdown`, `findings_json`, `json`.
5. **Drift `expand_count`** — how many extra adversarial scenarios beyond the anchor + conflict budget. `0` disables the drift lens entirely.
6. **Max prompt character limit** — repo-level threshold for triggering compact mode. Default `50000`. `0` disables compact mode entirely. Useful when your prompts routinely exceed 50K chars and you want every audit to run at full depth regardless of size.
7. **Report language** — controls the language of report.md, terminal summary, and Phase 9 dialog prompts. Options: `tr` (Türkçe, default) or `en` (English). Lens-generated content (rationale, suggested_fix, current_excerpt) stays in whatever language the runner produced; only skill-side templates translate.

### Project config (`.promptchecker.json`)

Example for a Turkish VAPI repo:

```json
{
  "default_type": "vapi",
  "target_model": "claude-opus-4-7",
  "output": ["markdown", "findings_json"],
  "expand_count": 4,
  "tr_phonetic": true,
  "max_char_limit": 50000,
  "report_language": "tr"
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
| `PROMPTCHECKER_MAX_CHAR_LIMIT` | Positive integer triggers compact mode when body exceeds this many chars. `0` disables compact mode. | project config → `50000` |
| `PROMPTCHECKER_REPORT_LANGUAGE` | `tr` or `en`. Sets skill-render language. | project config → `tr` |
| `PROMPTCHECKER_TIMING` | Truthy (`true`) writes a millisecond-precision phase-boundary log to `$RUN_DIR/timing.log`. Diagnostic only — leave off in normal use. | off |

**Timing logs (diagnostic):** when `PROMPTCHECKER_TIMING=true`, the skill writes `timing.log` to the run directory with one line per phase boundary (`phase_2_start`, `phase_2_end`, etc., in milliseconds since the Unix epoch). Use this when a run feels slow — `awk -F'[][]' '{print $2}' timing.log | sort -n` gives you the ordered timestamps; diffing adjacent timestamps surfaces the slowest phase. Drift simulation and subagent dispatch are common hotspots. Leave the env var off in everyday use — the log grows on every run.

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
max_char_limit: 100000           # this prompt is large; raise the threshold so compact mode does NOT trigger
report_language: en              # this prompt gets an English report even though repo default is tr
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

The plugin runs one orchestrating skill that fans out to three subagents for the lens work. `static-lens-runner` is dispatched on every run; `drift-runner` and `tr-phonetic-runner` are conditional (drift only when anchors / conflicts / role-overrides exist AND `expand_count > 0`; TR only when `tr_phonetic: true`).

**Parallel topology:** Phase 4 fans out FIVE concurrent `Agent` calls in a single assistant turn: conflict, dominance, gap, schema (each via static-lens-runner with `selected_lenses` singleton), and tr-phonetic (via tr-phonetic-runner). Phase 5 (drift) is downstream of conflict + dominance + gap — drift-runner reads those three artefacts as inputs, so it starts as soon as those three land (doesn't wait for schema or tr-phonetic). Phase 7 waits for all six lens outputs before rendering.

Phase 9 and Phase 10 are the interactive layer — they run automatically after Phase 8 in `/prompt-check` and re-enter from `/prompt-check-resume`.

1. **Phase 0** — First-run wizard or load existing `.promptchecker.json`.
2. **Phase 1** — Allocate a fresh `run-NNN` directory (atomic; `latest` symlink is updated only on success).
3. **Phase 2** — Parse frontmatter deterministically and split body. Stores `body_line_offset`, `prompt_sha256`, `body_char_count`, and `compact_mode` (true when body_char_count > max_char_limit AND max_char_limit > 0). Phase 4-6 dispatches propagate these to each runner.
4. **Phase 3** — Extract atomic, line-anchored rules from `body.txt`.
   - **Phase 3.5 — Per-run lens-selection wizard.** PromptChecker emits an `AskUserQuestion` widget asking which of the six lenses to apply. Repo defaults from `.promptchecker.json` seed which options are pre-checked, but the question itself is MANDATORY — prose substitutes are a contract violation. If the user is in a headless context where AskUserQuestion is unavailable, the audit aborts with a clear error rather than proceeding silently.
5. **Phase 4 — Parallel lens dispatch (5 concurrent Agent calls).** Emits five Agent calls in one turn:
   - `static-lens-runner` × 4 (conflict / dominance / gap / schema, each with singleton `selected_lenses`)
   - `tr-phonetic-runner` × 1 (conditional on user_intent.tr_phonetic_enabled)
   The skill awaits all five before proceeding. Schema lens auto-skips on flat prompts with no numbered headings.
6. **Phase 5 — Drift (downstream of static lenses).** Triggered as soon as conflicts.json + gaps.json + dominances.json land. Runs in parallel with schema and tr-phonetic if those are still working. Conditional: skipped when expand_count == 0 or no anchors/conflicts/role-overrides.
7. **Phase 7 — Render.** Awaits all six lens outputs. Builds findings.json + report.md (line numbers translated back to the original prompt file; `prompt_sha256` carried through). Each finding renders as ONE LINE in report.md / inline-suggestions.md / Phase 9 summary table: `**Section 7.2 — L284** [C1 conflict, high] — short rationale → **short fix**`. The full rationale + suggested_fix stay verbatim in findings.json — truncation is render-only.
8. **Phase 8** — Update `latest` symlink (commit point), print terminal summary.
9. **Phase 9** — Render summary table from `findings.json`. Bootstrap `session.json` (all findings start `pending`). Accept free-form decision string from the user, parse it, apply TR routing rule, append each decision to `decisions.jsonl`.
10. **Phase 10** — Process decisions: dismissed (log only), overlay (rebuild `inline-suggestions.md`), applied (SHA256-guarded prompt edits, with auto-conversion to overlay on stale audit or ambiguous occurrences), discussed (per-finding sub-dialogue with accept / revise / overlay / dismiss). Re-render `session.json` snapshot. Print Phase 10 summary. Phase 10's konuşalım sub-flow (per-finding deep dialogue) MANDATES `AskUserQuestion` for the four-option choice (kabul / revize / overlay / atla). Free-text follow-ups (revised suggestion text) use plain conversational input — that's intentional. The four-option choice itself is always AskUserQuestion.

## Architecture

The plugin is pure Claude Code skill orchestration. No Node, no TypeScript, no `npm install`, no `node_modules`, no `package.json` — one skill file, five references, two commands, and three subagent definitions (one always dispatched, two conditional).

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
├── drift-runner.md             (conditional — adversarial scenarios + judging)
├── static-lens-runner.md       (always dispatched — conflict + dominance + gap + schema)
└── tr-phonetic-runner.md       (conditional — advisory-only TR lens)
examples/
├── sample-system.md
├── sample-agent.md
└── sample-vapi.md              (dogfeeds tr_phonetic: true)
```

All cross-phase state is exchanged via JSON files under `.promptcheck/<basename>/run-NNN/`. The orchestrating skill reads its own writes; each subagent reads paths it is given and writes only the JSON artefacts assigned to it.

## License

MIT — see [LICENSE](./LICENSE) for the full text.
