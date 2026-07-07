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
2. Env var (`VOICEPROMPTKIT_MAX_CHAR_LIMIT=0` in Claude Code settings)
3. Project config (`.voicepromptkit.json` `max_char_limit`)
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
  "section_title": "RESPONSE GUIDELINES",
  "subsection_title": "TONE & STYLE"
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

## Compact writing — runner-side invariant

Every runner MUST write `rationale` / `reasoning` / `description` ≤ 200 characters and `suggested_fix` ≤ 150 characters. One sentence preferred. No preamble ("This finding indicates..."). Direct identification + reason + actionable fix. For structural fixes with embedded "Suggested: '...'" replacement text, keep the replacement under 50 chars or omit it (point to the line instead).

The render layer assumes runners obey this invariant. If a runner emits oversized text, the render still uses it verbatim (no truncation) but the report becomes ugly — runners are responsible for the contract.

Example (bad vs good):

BAD (304 chars, multi-clause):
   "R78 declares `sms_retry_count` default 0, max 1 (line 326). R80 declares STATE 15 'Max retries: 2 (for SMS resend)' (line 590). The same SMS-resend counter cannot have both a max of 1 and a max of 2 — runtime behavior will diverge depending on which rule the agent honours."

GOOD (155 chars):
   "R78 sets sms_retry_count max=1 (line 326) but R80 says 'Max retries: 2' (line 590); the same counter can't be both."

Self-correction: if you find yourself writing > 200 chars of rationale or > 150 chars of fix, you're either being verbose (rewrite) or trying to bundle multiple findings (split into separate ones).

## Language switching — canonical example pair

Every prose field a runner emits (`reasoning`, `description`, `rationale`, `suggested_fix`, `pronunciation_entry.note`) MUST be written in `inputs.report_language`. Rule IDs (`R12`, `R80`), line numbers, severity tokens (`high|medium|low`), and structural artefacts (section numbers like `1.3`, finding IDs like `C1`, `D1`, `G1`) stay neutral — they are not prose.

Canonical example (conflict lens; the same TR/EN switch applies to every static lens):

TR (`report_language: "tr"`):
```json
{
  "id": "C1",
  "rule_ids": ["R78", "R80"],
  "severity": "high",
  "reasoning": "R78 sms_retry_count max=1 (satır 326) ile R80 'Max retries: 2' (satır 590) çelişiyor; aynı sayaç hem 1 hem 2 olamaz.",
  "suggested_fix": "R80'i (satır 590) 'Max retries: 1' yap VEYA R78'i (satır 326) 'Max: 2' yap — tek değerde birleştir.",
  "fix_strategy": "structural"
}
```

EN (`report_language: "en"`):
```json
{
  "id": "C1",
  "rule_ids": ["R78", "R80"],
  "severity": "high",
  "reasoning": "R78 sets sms_retry_count max=1 (line 326) but R80 says 'Max retries: 2' (line 590); the same counter can't be both.",
  "suggested_fix": "Change R80 (line 590) to 'Max retries: 1' OR raise R78 (line 326) to 'Max: 2' — pick one canonical value.",
  "fix_strategy": "structural"
}
```
