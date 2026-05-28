# Lens criteria — shared invariants

Cross-lens content every static-lens slice depends on. Read alongside the slice for whichever lens you are running (`conflict.md`, `dominance.md`, `gap.md`, `schema.md`).

> **Output invariant — every finding must carry a concrete suggested_fix.** Conflict, Dominance, and Gap lenses MUST populate `suggested_fix` with either: (a) a one-sentence rewrite ready to apply, (b) a one-sentence structural action ("Move R3 below R12"), (c) the literal string `TODO: <open question>` when no clean resolution exists, or (d) the literal string `Intentional — dismiss this finding` when the runner judged the situation benign. Null / empty `suggested_fix` is invalid output.

## fix_strategy — substring vs structural

Every finding from the conflict, dominance, and gap lenses MUST also carry a `fix_strategy` field with one of two values:

- **`substring`** — the `suggested_fix` is a literal text that REPLACES `current_excerpt`. Phase 10 will perform a substring-level replace. Use this when the fix is a clean rewrite of the same span (e.g. `current_excerpt: "always formal"` → `suggested_fix: "professional but warm"`).
- **`structural`** — the `suggested_fix` is an instruction in natural language describing a structural change (e.g. "Add a clause after R3: '...'", "Move R7 below R12", "Rewrite the trigger sentence to remove the contradiction. Suggested: '...'", "Remove R6 (subsumed by R8)"). Phase 10 will use the Edit tool (or equivalent semantic edit) and surface a **risk warning** to the user before applying — manual review recommended.

**How to choose:**
- If the entire `current_excerpt` can be replaced verbatim with a self-contained text → `substring`.
- If the fix requires inserting new content, moving rules, deleting rules, or rewording across multiple sentences → `structural`.
- TODO / Intentional sentinel suggestions ("TODO: ...", "Intentional — dismiss this finding") → `structural` (Phase 10 routes them to overlay or skip; they're never applied directly).

**Mapping to fix_kind:** `fix_strategy` is orthogonal to `fix_kind`. A finding can be `fix_kind: "replace" + fix_strategy: "substring"` (apply via substring-replace), `fix_kind: "replace" + fix_strategy: "structural"` (apply via Edit tool + risk warning), or `fix_kind: "advisory" + fix_strategy: "substring"` (overlay, but if user manually applies, substring-replace is the natural method).

## Rule extraction

You analyse the body text and extract every rule, instruction, constraint, or directive into a flat list of atomic rules.

- One rule = one atomic obligation. Split compound sentences: "be polite and concise" → two rules.
- Preserve absolute claims verbatim: "always", "never", "only", "must", "ignore".
- `line` = lowest line number in `body.txt` where the rule begins.
- `id` is `R1, R2, R3 …` in source order.
- `category`:
  - **behavior** — what the model should do (actions, workflow, decisions).
  - **format** — how output is shaped (length, structure, JSON, markdown).
  - **tone** — register, friendliness, formality.
  - **policy** — refusal rules, safety, legal, scope.
  - **persona** — who the model is, role, identity.
- If the prompt contains examples, extract the rule the example illustrates, not the example itself.
- If a section is unstructured prose, still split into atomic obligations.

Schema:

```json
{
  "rules": [
    { "id": "R1", "category": "behavior|format|tone|policy|persona", "text": "<atomic, paraphrased to one sentence>", "line": 12, "source_excerpt": "<exact line or sub-clause that produced this rule, ≤ 200 chars>" }
  ]
}
```

## Severity heuristics across lenses

When unsure, ask: "How many realistic inputs trigger this?"

- **high**: affects most realistic inputs, or causes safety/policy violation.
- **medium**: affects some realistic inputs.
- **low**: corner case, would only matter in edge inputs.

## Compact mode

Active when the prompt body length exceeds `frontmatter.max_char_limit` (default 50000 chars; set to 0 to disable). The skill computes the body length in Phase 2 and propagates `compact_mode: true | false` to every lens runner via the dispatch contract.

The policies below trade audit DEPTH for SPEED. Every finding KIND remains possible — compact mode does not invent or remove categories — it trims which severities and pair-comparisons are evaluated.

### Per-lens policy

| Lens | Compact-mode change |
|---|---|
| **Conflict** | Severity floor: `medium`. Emit `high` and `medium` only; skip `low`. Pair budget: pick the 50 most-impactful rules (those containing "always", "never", "must", "only", "ignore") and compare only within that set. Cap at ~1250 pair comparisons regardless of N. |
| **Dominance** | Mechanism restriction: emit only `role-override` and `recency`. Skip `position`, `length`, `specificity` (subtle effects requiring pair-comparison cost). Severity floor: `medium`. |
| **Gap** | Severity floor: `medium`. Skip `low`-severity gaps (corner cases). Keep `undefined_edge_case` and `ambiguous_term` at medium/high. |
| **Schema** | No change. Heading parsing is cheap regardless of body size. |
| **Drift** | Halve `effective_expand_count = max(1, raw_expand_count // 2)`. Scenario count maps linearly to LLM simulation cost — this is the single biggest perf lever for drift on large prompts. |
| **TR phonetic** | No change. Line-level pattern matching is already cheap. |

### Rule extraction (Phase 3)

In compact mode, keep each rule's `text` field to ≤ 100 characters and `source_excerpt` to ≤ 120 characters. This trims the rule-list payload that downstream lenses load. Atomic-rule semantics unchanged — the policy is about VERBOSITY, not correctness.

### Output annotation

Every static-lens output file (`conflicts.json`, `dominances.json`, `gaps.json`, `schema.json`) gains a top-level `compact_mode: true` field when policy fired, plus a `compact_policy` array listing which trim policies applied (e.g. `["severity_floor_medium", "pair_budget_50"]`). `drift.json` gains `compact_mode: true` + `compact_policy: ["expand_count_halved"]`. `tr_phonetic.json` gains `compact_mode: true` only (no policy fired — informational).

### Override hierarchy for max_char_limit

Same priority chain as other repo defaults (most specific wins):
1. Per-prompt frontmatter (`max_char_limit: 100000` in the YAML header)
2. Env var (`PROMPTCHECKER_MAX_CHAR_LIMIT=0` in Claude Code settings)
3. Project config (`.promptchecker.json` `max_char_limit`)
4. Built-in default: `50000`

Set `max_char_limit: 0` at any layer to disable compact mode entirely — the audit runs at full depth regardless of body size.

### Not a hard abort

Compact mode does NOT abort the audit on oversize prompts. It runs THROUGH them with cheaper policies. To enforce a hard limit (refuse to audit prompts over N chars), you would add a separate frontmatter field (`hard_limit:`) — that is not part of v0.4.7 and not implemented.

## Section reference (every finding)

Every finding emitted by every lens (conflict, dominance, gap, schema, drift, tr_phonetic) MUST carry a `section_ref` field. This is a structured pointer to the `## SECTION N` (and `### N.M` if applicable) heading that contains the finding's `line`. Built deterministically in Phase 3 of the skill (see `section_index.json`); used by Phase 7 to render section-aware finding headers ("Section 7.2 — L284" instead of bare "L284").

### Field shape

```json
"section_ref": {
  "section": "7",
  "subsection": "7.2",
  "section_title": "VALUE FRAMING AXES",
  "subsection_title": "MÜBADELE VALUE HIERARCHY"
}
```

OR

```json
"section_ref": null
```

The `null` value signals "the finding's line is outside any numbered section" (e.g. a preamble line, a line between sections, or any line in a prompt with no numbered headings at all).

### Per-lens conventions

| Lens | section_ref source |
|---|---|
| Conflict / Dominance / Gap | Look up `finding.line` in `section_index.ranges`. Static lenses always operate on rule-anchored lines, so most findings have a real section_ref. |
| Schema | Same lookup. For `section_gap` or `missing_parent` findings flagging a heading line, the ref points to the section the HEADING introduces (e.g. a finding on line 280 = `## SECTION 7` carries `section_ref.section: "7"`). |
| Drift | Behavioural, scenario-level. `line: null` AND `section_ref: null` always. Drift findings describe model behaviour, not positional issues. |
| TR phonetic | Same lookup. Both auto-filed (foreign_word/abbreviation) and apply-eligible (number_readability/punctuation) findings carry section_ref. |

### Backward compatibility

Findings from runs before v0.4.8 do not have the `section_ref` field. Renderers and downstream tools MUST handle:
- Field present and not null → use it.
- Field present and null → fall back to bare line marker.
- Field absent → also fall back to bare line marker (treat as null).

### Rendering rules (Phase 7 and overlay)

When `section_ref.subsection` is not null:
- TR: `Bölüm 7.2 — Satır 284`
- EN: `Section 7.2 — L284`

When `section_ref.section` is not null but `subsection` is null:
- TR: `Bölüm 7 — Satır 284`
- EN: `Section 7 — L284`

When `section_ref` is null:
- TR: `Satır 284`
- EN: `L284`

Section title is NOT included in the inline header (would make the line too long); it appears once in the section-level summary at the top of report.md / inline-suggestions.md.

## Render contract — table format + compact writing

Every finding emitted by every lens (conflict, dominance, gap, schema, drift, tr_phonetic) is rendered downstream as a row in a markdown TABLE. Phase 7 of the skill produces `report.md` with one table per severity bucket (or one combined table sorted by severity); the same table format appears in Phase 9's summary view shown to the user.

### Table columns

| Language | id | lens | severity | section/line | rationale | fix |
|---|---|---|---|---|---|---|
| TR | `id` | `mercek` | `önem` | `bölüm / satır` | `açıklama` | `düzeltme` |
| EN | `id` | `lens` | `sev` | `section / line` | `rationale` | `fix` |

### Column content rules

- **id**: finding id verbatim (`C1`, `D3`, `G2`, `S1`, `T1`, `drift-S1`).
- **lens / mercek**: translated lens name. Lookup table:

  | English | Türkçe |
  |---|---|
  | conflict | çelişki |
  | dominance | baskınlık |
  | gap | boşluk |
  | schema | şema |
  | drift | davranışsal sapma |
  | tr phonetic | türkçe fonetik |

- **severity / önem**: translated severity. Lookup table:

  | English | Türkçe |
  |---|---|
  | high | yüksek |
  | medium | orta |
  | low | düşük |

  For drift findings, severity is inferred from the verdict score: `score ≤ 0.5` → high, `score ≤ 0.75` → medium, else low.

- **section/line**: composite cell derived from `section_ref` + `line`:

  | section_ref state | line state | TR render | EN render |
  |---|---|---|---|
  | subsection set | line set | `Bölüm 7.2 / Satır 284` | `Section 7.2 / L284` |
  | section only | line set | `Bölüm 7 / Satır 284` | `Section 7 / L284` |
  | null | line set | `— / Satır 284` | `— / L284` |
  | null | null (drift only) | `— / —` | `— / —` |

- **rationale / açıklama**: the runner-emitted `rationale` field verbatim — NO TRUNCATION. Runners self-cap at ≤200 chars per the compact writing invariant; render uses full text. Multi-sentence rationales are rare (compact writing prefers one sentence) but acceptable when needed.

- **fix / düzeltme**: the runner-emitted `suggested_fix` field. Renders directly. Sentinel suggestions:
  - `TODO: <text>` → renders as italic `_TODO: <text>_`
  - `Intentional — dismiss this finding` → renders as italic `_Intentional — atla_` (TR) / `_Intentional — dismiss_` (EN)
  - Drift findings with no fix: `(geçti — düzeltme yok)` (TR) / `(passed — no fix)` (EN)
  - Empty/null fix for other lenses: `_(see rationale)_` / `_(bkz. açıklama)_`

### Sort order (unchanged from v0.4.9)

Severity descending (high → medium → low) → lens group → line ascending. Tied (same severity, same lens, same line): order by finding id.

### Compact writing — runner-side invariant (also documented per-lens-runner)

Every runner MUST write `rationale` ≤ 200 characters and `suggested_fix` ≤ 150 characters. One sentence preferred. No preamble ("This finding indicates..."). Direct identification + reason + actionable fix.

The render layer assumes runners obey this invariant. If a runner emits oversized text, the render still uses it verbatim (no truncation) but the report becomes ugly — runners are responsible for the contract.

### No truncation in render

v0.4.9 had `≤120 chars rationale, ≤100 chars fix` truncation at the render layer with ellipsis. v0.4.10 REMOVES this. The new contract: runner self-caps; render uses full text. Truncation cut sentences mid-clause and made reports unreadable; pushing the constraint to the runner ensures the output is meaningful within the limit.

### Canonical example — TR

```markdown
| id | mercek | önem | bölüm / satır | açıklama | düzeltme |
|---|---|---|---|---|---|
| C2 | çelişki | yüksek | Bölüm 5 / Satır 326 | R78 sms_retry_count max=1 ile R80 max=2 çelişiyor; aynı sayaç iki değer alamaz. | R80'i (satır 590) max=1 yap VEYA R78'i max=2 yap — tek değerde birleştir. |
| G5 | boşluk | orta | Bölüm 0.1 / Satır 7 | R4 "step instructions require it" tanımsız; hangi step'lerin 35 kelimeye gittiği belirsiz. | R4'ü "Verbatim mandatory scripts muaftır, kısa tutulamaz" diye netleştir. |
| drift-S1 | davranışsal sapma | düşük | — / — | regression senaryosu geçti (0.93). | (geçti — düzeltme yok) |
```

### Canonical example — EN

```markdown
| id | lens | sev | section / line | rationale | fix |
|---|---|---|---|---|---|
| C2 | conflict | high | Section 5 / L326 | R78 sets sms_retry_count max=1 but R80 says max=2; same counter cannot be both. | Change R80 (line 590) to max=1 OR R78 to max=2 — pick one canonical value. |
```
