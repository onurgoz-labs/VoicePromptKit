# Dialog flow — interactive selection templates

Reference document for the `prompt-check` skill's **Phase 9** (interactive selection) and **Phase 10** (action dispatch). Read on first use during a `prompt-check` run when the skill reaches the interactive phases.

This file defines templates, grammar, and routing rules only. It does NOT define the step-by-step procedure (that lives in `SKILL.md`), the overlay file format (that lives in `references/overlay-format.md`), or the decisions.jsonl shape (same).

## 1 — Lens selection (Phase 9 entry)

Before running any analysis, ask the user which lenses to apply. Use `AskUserQuestion` with `multiSelect: true`. Default to all five selected — the user can deselect.

**Primary question:**

```
question: "Which lenses do you want to run on this prompt?"
header:   "Lenses"
multiSelect: true
options:
  - label: "conflict"     description: "Rules that contradict each other under realistic inputs"
  - label: "dominance"    description: "Silent overrides — one rule swallows another (position/length/recency/role)"
  - label: "gap"          description: "Undefined edge cases and ambiguous terms"
  - label: "drift"        description: "Adversarial scenarios run through drift-runner subagent"
  - label: "tr_phonetic"  description: "Turkish phonetic readability — voice/TTS only"
  - label: "schema"       description: "section numbering / ordering / heading consistency"
```

The `schema` lens auto-skips on prompts with no numbered section headings (flat
instruction sets). If you select it and the prompt is flat, the Phase 8
summary reports `Schema: 0 (no numbered section headings detected)` —
no false positives. Useful for structured Vapi flows or system prompts
that use `## SECTION N` / `### N.M` ATX headings.

**Follow-up — drift only.** If `drift` was selected, ask:

```
question: "How many extra drift scenarios beyond anchors + conflict budget? (0 disables drift)"
header:   "expand_count"
multiSelect: false
options:
  - label: "0"   description: "Disable drift entirely"
  - label: "3"   description: "Default — small set, fast"
  - label: "5"   description: "Moderate"
  - label: "10"  description: "Wide coverage"
  - label: "Other" description: "Type your own integer 0–20"
```

If `Other` is chosen, follow up with a free-form prompt: `Type an integer 0–20:` and validate. Out-of-range answers re-prompt; do not crash.

**Follow-up — all runs (advisory).** After lens selection, regardless of which lenses were picked:

```
question: "Bu prompt'a anchor eklemek ister misin? (frontmatter'a yazılır — bu run'ı etkilemez)"
header:   "Anchors"
multiSelect: false
options:
  - label: "Hayır"        description: "Skip — no anchor changes"
  - label: "Evet, sonra"  description: "Remind me in the summary footer"
```

This question is **advisory only** — anchors live in the prompt file's frontmatter, not in `session.json`. If the user says `Evet, sonra`, set `user_intent.anchors_added: false` and add a one-line reminder to the summary footer: `_Reminder: add anchors to <prompt> frontmatter before the next audit._`

**Report language (wizard's 7th question).** After the 6 lens-related questions, the wizard asks a 7th question: `report_language` (`tr` / `en`, default `tr`). This controls the language of skill-rendered text in report.md, the Phase 8 terminal summary, and Phase 9 dialog prompts. Lens-generated content (rationale, suggested_fix, current_excerpt) stays in whatever language the runner produced — only the skill's template strings translate. The full TEMPLATE_STRINGS dictionary lives in SKILL.md Phase 7; this file just acknowledges the field exists and its scope (template-only translation).

## 2 — Summary view rendering (Phase 9, after audit completes)

After Phase 7 produces `findings.json`, the skill renders a single markdown table covering every finding from every selected lens. This is the user's one-shot view of the audit.

**Sort order:** `line` ascending, then `severity` descending (`high` → `medium` → `low`). Stable sort — preserve `findings.json` order on ties.

**Columns:**

| Column | Source | Truncation |
|---|---|---|
| `id` | `findings[].id` | none |
| `lens` | `findings[].lens` | none |
| `severity` | `findings[].severity` | none |
| `section` | `findings[].section_ref.subsection` if set, else `findings[].section_ref.section` if set, else `—` | none |
| `line` | `findings[].line` (original-file) | none |
| `excerpt` | `findings[].current_excerpt` | 60 chars + `…` if longer |
| `suggestion` | `findings[].suggested_fix` if present else `findings[].rationale` | 80 chars + `…` if longer |

The `section` column anchors findings in their containing prompt section. Useful when a 1758-line prompt has dozens of findings — the user can quickly group them by section/subsection mentally. Findings with no section context show `—`.

**Rendered shape:**

```markdown
## Findings — run-NNN

| id | lens | sev | section | line | excerpt | suggestion |
|----|------|-----|---------|------|---------|------------|
| C1 | conflict | high | 7.2 | 284 | Always be formal... | Maintain a professional but warm register. |
| S1 | schema | high | 7 | 280 | ## SECTION 7 — VALUE FRAMING | Insert Section 6 — <Placeholder> between line 220 and 280, OR renumber Section 7 → 6 and shift subsequent sections down. |
| G2 | gap | medium | — | 27 | … | … |
| T3 | tr_phonetic | low | 1.3 | 17 | Kurban Bayramı... | Kurban Bayramı tatili, ... |

_Note: 3 TR pronunciation findings (foreign_word + abbreviation) will be auto-filed to the overlay's Pronunciation map. They are not in the table — no decision needed._

Hangilerini ne yapayım? Örnek:
  C1, C3 düzelt; G2 yorum bırak; T1..T5 konuşalım; gerisini atla

Verbs: düzelt | yorum bırak | atla | konuşalım  (alias: apply | overlay | dismiss | discuss)
Special: gerisini atla | gerisini yorum | hepsini düzelt | hepsini yorum bırak | hepsini atla | iptal
```

> **Note:** TR findings with `kind == "foreign_word"` or `"abbreviation"` (`fix_kind: "advisory"`) are NOT in this table — they auto-file to the overlay's Pronunciation map without a user decision. Only TR findings with `kind == "number_readability"` or `"punctuation"` (`fix_kind: "replace"`) appear here. The `T1` row above is an example of a TR `number_readability` finding (`100 TL` → `yüz lira`), which is `fix_kind: "replace"` and therefore still in the table.
>
> The auto-filed banner directly below the table is rendered ONLY when the AUTO_FILED_SET is non-empty (i.e. the audit found one or more TR `foreign_word` / `abbreviation` findings). Otherwise it is omitted.

> The `S`-prefix on schema finding ids (`S1`, `S2`, …) is intentional — distinct from `C` / `D` / `G` / `T` to make grep / filter easy.

After the table, render the prompt verbatim and accept free-form text on the next user turn.

If `findings[]` is empty, skip the prompt entirely and surface `No findings — nothing to decide.` Still create an empty `session.json` and `decisions.jsonl` for audit symmetry.

## 3 — Free-form decision parsing grammar

The user's reply is a single free-form string. Parse it into a list of `(finding_ids, verb)` decisions.

### 3.1 — Tokens

**Verbs (case-insensitive, all map to the same status):**

| Turkish | English aliases | Resulting status |
|---|---|---|
| `düzelt`, `uygula` | `apply`, `fix` | `applied` (TR `foreign_word` / `abbreviation` auto-routed to `overlay` — see §4) |
| `yorum bırak`, `not düş`, `kenara koy` | `overlay`, `comment` | `overlay` |
| `atla`, `geç`, `bırak` | `skip`, `dismiss` | `dismissed` |
| `konuşalım`, `tartış`, `inceleyelim` | `discuss`, `talk` | `discussed` (transient — triggers sub-flow in §5) |

The parser is LLM-driven, not regex-driven. Natural variants (`fix bunları`, `şunu düzelt`, `bunları yorum bırakalım`) MUST be accepted as long as the verb intent is clear. When the verb is ambiguous, re-prompt the user with: `Bu kararı net anlayamadım: <segment>. Açar mısın?` rather than silently dropping the segment.

Verb tokens are language-agnostic — both TR and EN synonyms parse identically in either `report_language`. The verb table above is the canonical reference; `report_language: en` does NOT swap the verbs to English-only. `düzelt`, `yorum bırak`, `atla`, `konuşalım`, `iptal` remain the parser's canonical keywords regardless of the report language setting.

**Special tokens:**

| Token | Meaning |
|---|---|
| `gerisini atla` / `rest skip` / `dismiss rest` | All findings still `pending` → `dismissed` |
| `gerisini yorum` / `gerisini yorum bırak` / `rest overlay` | All findings still `pending` → `overlay` |
| `gerisini düzelt` / `rest apply` / `apply rest` | All findings still `pending` → `applied` (TR `foreign_word` / `abbreviation` auto-routed to `overlay` — see §4) |
| `hepsini düzelt` / `hepsini apply` / `tümü düzelt` / `tümünü düzelt` / `all apply` / `apply all` / `fix all` | Every finding → `applied` (TR `foreign_word` / `abbreviation` auto-routed to `overlay` — see §4), overriding any earlier per-id decision in the same string |
| `hepsini yorum bırak` / `tümü overlay` / `all overlay` / `comment all` | Every finding → `overlay`, overriding any earlier per-id decision |
| `hepsini atla` / `tümü atla` / `all skip` / `skip all` / `dismiss all` | Every finding → `dismissed`, overriding any earlier per-id decision |
| `hepsini konuşalım` / `tümünü konuşalım` / `discuss all` | Every finding → `discussed` (rare; mainly for edge cases) |
| `iptal` / `cancel` | Abort; leave every finding at `pending`; do not run Phase 10 |

**`gerisini X` vs `hepsini X`.** `gerisini X` applies the verb only to findings the user did NOT explicitly mention earlier in the decision string. `hepsini X` applies the verb to ALL findings, overriding any earlier per-id decision. Concretely, in `C1 yorum bırak; hepsini düzelt`, C1 ends up as `applied` (the `hepsini` clause overrides the earlier `yorum bırak` for C1). If the user wants to preserve earlier decisions, they should use `gerisini düzelt` instead.

**ID forms inside a segment:**

| Form | Example | Meaning |
|---|---|---|
| single | `C1` | one finding |
| comma-list | `C1, C3, C7` | three findings |
| range | `T1..T5` | inclusive range across same lens prefix (T1, T2, T3, T4, T5) |
| wildcard | `gerisini` (rest) | every finding still `pending` at the moment this segment is evaluated |
| wildcard-all | `hepsini` / `tümü` / `tümünü` / `all` | every finding regardless of earlier decisions (overrides per-id decisions in the same string) |

### 3.2 — Segment grammar

- Decisions are separated by `;` (semicolon). Whitespace around `;` is ignored.
- Each segment is `<id-list> <verb>` OR `<verb> <id-list>`. Both orders are valid: `C1 düzelt` and `düzelt C1` mean the same thing.
- Verbs of multiple words (`yorum bırak`, `gerisini atla`) match as a single token — the parser must look ahead.
- Segments are evaluated **left to right**. `gerisini` always means "the rest at this point", so order matters: `C1 düzelt; gerisini atla` applies C1 then dismisses the others.
- `hepsini` / `tümü` / `all` is the only token that overrides earlier per-id decisions in the same string. Evaluate it last so it wins: `C1 yorum bırak; hepsini düzelt` ends with C1 as `applied`.

### 3.3 — Error handling (per-segment, never abort the batch)

| Condition | Action |
|---|---|
| Unknown id (e.g. `Z9`) | Skip that id, append a warning to the parser report: `unknown finding id: Z9 — skipped`. Continue with the rest of the batch. |
| Unrecognised verb | Skip the whole segment, append: `unrecognised verb "<text>" for <id-list> — retry that segment`. Continue. |
| Range with mismatched prefixes (`C1..T3`) | Skip, append: `range C1..T3 mixes lenses — split into two segments`. Continue. |
| Range with descending bounds (`T5..T1`) | Skip, append: `range T5..T1 is descending — write it as T1..T5`. Continue. |
| `iptal` appears anywhere in the string | Abort immediately. No decisions are applied. Surface: `Cancelled — every finding still pending. Re-render the summary with /prompt-check-resume <run-id>.` |
| Empty input | Treat as `iptal`. |

After parsing, surface a one-paragraph plan **before** Phase 10 dispatches:

```
Plan:
  applied  → C1, C3, T4, T5  (T4..T5: TR number_readability / punctuation — normal apply)
  overlay  → G2, T1, T2, T3  (T1..T3 auto-routed: TR foreign_word / abbreviation)
  dismissed → C2, D1, D2, G1
  discussed → (none)
Warnings: unknown finding id: Z9 — skipped
Proceed? (yes/no)
```

Wait for explicit confirmation before any side-effect. If the user says no, return to the free-form decision prompt with the same findings table.

Schema findings (`S1`, `S2`, ...) follow the normal apply / overlay / dismiss / discuss flow — no special routing. Most schema fixes are structural (renumber / reorder / insert), so when the user picks `düzelt`, Phase 10 surfaces the risk warning before using the Edit tool. The exception is `heading_style_inconsistent` which is substring-style and applies cleanly.

## 4 — TR routing rule (per-category)

TR phonetic findings split into two routing buckets based on `kind`:

- **`foreign_word` / `abbreviation` (`fix_kind: "advisory"`) — auto-filed.** These findings are NEVER shown in the Phase 9 summary table, the plan prompt, or any decision view. They are silently routed to the overlay's Pronunciation map section without user input. Wildcard verbs (`hepsini düzelt`, `gerisini X`, `hepsini yorum bırak`, etc.) **ignore them entirely** — they are not part of the decision set. Pronunciation hints (DHL → "de-ha-el", Gaggia → "gacca") are voice-design decisions the author owns, and the user has already indicated they never want to be asked about them; surfacing them as a decision is a UX regression.
- **`number_readability` / `punctuation` (`fix_kind: "replace"`) — normal flow.** These findings appear in the summary like any other finding. Wildcard verbs apply to them. `düzelt` modifies the prompt file (with substring replace) just like a `conflict` or `gap` finding; `yorum bırak` routes to overlay; `atla` dismisses; `konuşalım` enters the sub-flow.

`konuşalım` is unavailable for auto-filed TR findings — they never enter the decision set, so the verb has nothing to attach to. See §5 below for the corner-case handling when the user explicitly references an auto-filed finding's id.

The auto-file rule for `foreign_word` / `abbreviation` is non-negotiable. There is no opt-out flag, no frontmatter switch, no env var, no override verb. The prompt file is the author's curated text; pronunciation belongs in the overlay; and the decision flow surfaces only findings the user can actually act on.

**Wildcard visibility.** When a wildcard verb (`hepsini düzelt`, `gerisini düzelt`, `apply all`, etc.) is parsed:

- `foreign_word` / `abbreviation` (`fix_kind: "advisory"`) → skipped by the wildcard. The auto-filed banner already announced these findings below the summary table; the wildcard's effect simply does not extend to them.
- `number_readability` / `punctuation` (`fix_kind: "replace"`) → normal flow. The verb is applied just like to any non-TR finding (apply / overlay / dismiss / discuss).

This split is mandatory — the previous "silent redirect" behaviour caused user confusion (cf. real runtime: user said `hepsini düzelt`, plugin applied 0, user surprised). With auto-file, TR advisory findings never enter the decision set, so wildcards no longer need a special-case redirect message for them; the auto-filed banner is the single explicit surface.

**Per-lens routing summary.**

| Lens | Routing |
|---|---|
| conflict / dominance / gap | normal apply flow |
| drift | findings are read-only (no apply); user routes to overlay or dismiss |
| tr_phonetic foreign_word / abbreviation | auto-filed (hidden from summary) |
| tr_phonetic number_readability / punctuation | normal apply flow |
| schema | normal apply flow; most fixes structural (Edit tool with risk warning) |

## 5 — "Konuşalım" sub-flow (Phase 10, for `status: discussed`)

When one or more findings reach `status: discussed`, enter a sub-loop **after** the plain `düzelt/overlay/atla` decisions have been applied (so the user sees a clean slate).

Process discussed findings in `id` order (lens prefix groups together: C1, C2, …, D1, …, G1, …, T1, …). For each:

### 5.1 — Display the finding in full

```
─── Discussion: T2 ───
Lens:       tr_phonetic
Severity:   medium
Line:       73
Excerpt:    "D&R'den geçtim"
Rationale:  Brand contains an ampersand; TTS will read "and" in English.
Suggestion: D&R → "de ve er"
```

### 5.2 — Ask via AskUserQuestion (4 options)

```
question: "T2 — what would you like to do?"
header:   "T2 discussion"
multiSelect: false
options:
  - label: "kabul et"          description: "Apply the default suggestion (or overlay if TR)"
  - label: "ben revize ediyorum" description: "I'll type a replacement"
  - label: "yorum bırak"       description: "Write to overlay only"
  - label: "atla"              description: "Skip this finding"
```

### 5.3 — Branch on the answer

| Answer | Action |
|---|---|
| `kabul et` | Apply `suggested_fix` (or overlay if TR per §4). Log `action: "discussed"` then `action: "applied"` (or `"overlay"`) to decisions.jsonl. |
| `ben revize ediyorum` | Open a free-form prompt: `Type the replacement text:`. Read the user's next message verbatim. Then ask via AskUserQuestion: `Apply this to the prompt file, or write it to the overlay?` (two options: `düzelt` / `yorum bırak`; TR `foreign_word` / `abbreviation` findings have only `yorum bırak` per §4 — TR `number_readability` / `punctuation` findings offer both). Log `action: "discussed"` → `action: "revised"` with `from`/`to` fields → final `action: "applied"` or `"overlay"`. |
| `yorum bırak` | Write to overlay. Log `action: "discussed"` → `action: "overlay"`. |
| `atla` | No file changes. Log `action: "discussed"` → `action: "dismissed"`. |

### 5.4 — Iteration contract

- One sub-dialogue per discussed finding, sequentially. Never batch.
- After each finding finishes, surface a one-line confirmation: `T2 → overlay (revised).` and move on.
- After the last discussed finding, surface the final summary (counts per action) and the path to `decisions.jsonl`.

If the user types `iptal` at any point inside the sub-flow, abort the remaining discussions; leave already-processed findings at their final status and the rest at `discussed` (so `/prompt-check-resume` can pick them up).

### 5.5 — Auto-filed TR findings are not eligible for `konuşalım`

Auto-filed TR findings (`foreign_word` + `abbreviation`, `fix_kind: "advisory"`) never enter the decision set, so they cannot reach this sub-flow under normal parsing. If the user explicitly references an auto-filed finding's id in their decision string (e.g. `T1 konuşalım`), DO NOT enter the konuşalım sub-flow for it and DO NOT re-route it through any other verb. Instead, surface a one-line clarifying message:

```
T1 is a TR pronunciation finding — auto-filed to the Pronunciation map. To
revise the pronunciation hint, edit the prompt's pronunciation guide block
manually.
```

The other segments in the same decision string continue to parse normally. The auto-filed finding stays in AUTO_FILED_SET; its `auto_filed` log line will be appended in Phase 9.6 as planned. No `discussed` line is written for it, no sub-flow runs.

## 6 — Session bootstrap shape

At Phase 9 entry (right after lens selection, before running any lens), the skill writes `$RUN_DIR/session.json`. It is the durable record of user intent and per-finding status; resume relies on it.

```json
{
  "schema_version": 1,
  "run_id": "run-NNN",
  "started_at": "<ISO 8601 UTC>",
  "user_intent": {
    "selected_lenses": ["conflict", "gap", "tr_phonetic"],
    "expand_count": 3,
    "anchors_added": false
  },
  "findings_state": {
    "C1": { "status": "pending", "last_ts": "<ISO 8601 UTC>" },
    "C2": { "status": "pending", "last_ts": "<ISO 8601 UTC>" },
    "G1": { "status": "pending", "last_ts": "<ISO 8601 UTC>" },
    "T1": { "status": "pending", "last_ts": "<ISO 8601 UTC>" }
  }
}
```

**Status taxonomy:**

| Status | Persistent? | Meaning |
|---|---|---|
| `pending` | yes | not yet decided; eligible for resume |
| `applied` | yes | `suggested_fix` written to prompt file |
| `overlay` | yes | written to `inline-suggestions.md` |
| `dismissed` | yes | user said `atla`; no side-effect |
| `discussed` | transient | inside the sub-flow; never persisted as final |
| `revised` | transient | sub-flow recorded a user-typed replacement; followed by `applied` or `overlay` |

When the skill records a transient status, it immediately writes the next persistent status in the same Phase 10 tick. `session.json` on disk only ever contains persistent statuses at rest. Transient statuses appear only in `decisions.jsonl` for full audit trail.

`findings_state` is keyed by finding id. The skill updates the entry's `status` and `last_ts` after each decision is dispatched.

## 7 — Resume contract

`/prompt-check-resume [run-id]` (sister command, defined elsewhere) opens an existing `session.json`:

1. Resolve the run directory: explicit `run-id` argument > `latest` symlink.
2. Read `session.json` and `findings.json`.
3. Filter `findings_state` for entries with `status == "pending"`.
4. Re-render the summary table from §2, restricted to those rows.
5. Re-prompt with the same free-form decision string (§3).
6. Phase 10 runs against the filtered set only — already-applied / overlay / dismissed findings are not re-touched.

The session.json schema is forward-compatible: unknown keys are preserved on rewrite. If `schema_version` is greater than the running skill version, abort with: `Session schema version N is newer than this skill (M) supports — upgrade PromptChecker first.`

If no `pending` findings remain, surface: `No pending decisions in run-NNN. Nothing to resume.` and exit cleanly.
