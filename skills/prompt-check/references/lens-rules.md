# Lens criteria — detailed reference

Read this on first use during a `prompt-check` run. It is the canonical specification for every analysis lens. Where the SKILL.md outline says "apply the criteria in lens-rules.md", look here.

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

## Conflict lens

A conflict exists when obeying one rule **necessarily violates** another in at least one realistic input.

- Cluster more than two rules into a single conflict when they form a transitive contradiction (A vs B vs C).
- Do not invent rules; only reference `rule_ids` extracted above.
- Severity:
  - **high** — direct logical opposites ("always X" vs "never X"), or safety/policy contradictions.
  - **medium** — rules conflict under common inputs but not all inputs.
  - **low** — rules nudge in opposite directions but can be satisfied with care.

If none, emit `{ "conflicts": [] }`. Empty is a legitimate outcome.

Schema:

```json
{ "conflicts": [{ "id": "C1", "rule_ids": ["R3","R8"], "severity": "low|medium|high", "reasoning": "<≤ 400 chars>", "suggested_fix": "<concrete one-sentence rewrite or structural action>", "fix_strategy": "substring | structural" }] }
```

For the `suggested_fix` in the merged findings.json: propose a concrete rewrite that resolves the contradiction (e.g. "Replace R8 with: 'Stay warm and approachable while preserving professional language.'"). If no clean resolution exists, write `suggested_fix: 'TODO: pick one of (A) <option>, (B) <option>'` so the author has a starting point. Empty `suggested_fix` is no longer allowed.

## Dominance lens

A dominance is **not** a conflict: it is the relationship where one rule will silently override another in practice, even without a logical contradiction. The dominated rule still applies in theory, but the dominant rule wins under the model's recency / length / role-override biases.

Mechanisms:

- **position** — later instruction overrides earlier (LLMs are recency-biased).
- **length** — a long, repeated rule overshadows a single-line counter-rule.
- **specificity** — a specific exception beats a general rule (this one is often intentional and benign — flag only when the specific rule is too narrow).
- **recency** — rules near the end of the prompt anchor model behaviour.
- **role-override** — phrases like "ignore previous instructions", "your new role is", "actually you are", "from now on you are".

Rules that **contradict** but where neither dominates are a *conflict*, not a dominance — emit them in the conflict lens instead.

Schema:

```json
{ "dominances": [{ "id": "D1", "dominant_rule_id": "R12", "dominated_rule_id": "R3", "mechanism": "position|length|specificity|recency|role-override", "severity": "low|medium|high", "reasoning": "<≤ 300 chars>", "suggested_fix": "<concrete one-sentence rewrite or structural action>", "fix_strategy": "substring | structural" }] }
```

Severity heuristic for dominance:
- **high** — `role-override` mechanism (the dominant rule is an explicit override pattern), or `recency` on a safety-critical rule.
- **medium** — `position` / `length` where the dominated rule is consequential, or `specificity` where the specific rule is too narrow.
- **low** — `specificity` where the dominant rule is a benign intentional exception.

For `suggested_fix`: **always populate `suggested_fix` with a concrete one-sentence action** — e.g. "Move R3 below R12 and merge their content", "Remove R6 (subsumed by R8)", or "Replace R12 with: \"After the announcement completes, immediately trigger end-call-tool unless an interruption is in progress; in that case, finish the remainder first.\"". If the dominance is intentional/benign, write `suggested_fix: 'Intentional — dismiss this finding'` so the author can see the runner reached that conclusion. Empty `suggested_fix` is no longer allowed.

## Gap lens (strict scope)

You flag only gaps that exist **within the prompt's own rules**, not absent concepts the prompt never addresses. Every gap must cite at least one `related_rule_id` that demonstrates the prompt itself raised the question.

### Two kinds of gap

**1. Undefined edge case (incomplete conditional).**
A rule introduces a conditional ("if X, do A") but the rule set never covers the complementary case ("if not X" or "otherwise").

- ✅ Flag: `R3: "If the customer is upset, prioritise satisfaction"` — no rule covers what to do when the customer is not upset and a policy conflict arises.
- ❌ Don't flag: A prompt with a happy-path persona that never mentions angry users. No conditional → nothing to be incomplete.

**2. Ambiguous term used inside a rule.**
A rule uses a vague evaluative word — `appropriate`, `reasonable`, `professional`, `clear`, `concise`, `polite`, `friendly`, `formal`, `casual`, `comprehensive`, `brief`, `thorough` — without another rule or definition anchoring it.

- ✅ Flag: `R5: "Be appropriately formal"` — "appropriate" is undefined; no other rule clarifies the formality scale.
- ❌ Don't flag: A prompt that omits a tone instruction altogether. No ambiguous term → nothing to clarify.

### Out of scope (do not flag)

You do **not** speculate about absent concepts. The following are explicitly out of scope:

- "The prompt doesn't say what to do if the request is impossible." — Only flag if a rule references impossibility.
- "Persona is undefined." — Only flag if a rule references the persona/role/scope.
- "For Vapi, the prompt should handle silence/hang-up/multi-speaker." — Don't flag based on prompt type.
- "For agents, the prompt should define tool-use boundaries." — Don't flag based on prompt type.
- "Missing failure mode." — Only flag if a rule references failure handling.

The single rule of thumb: every gap must cite at least one `related_rule_id`. Gaps with no related rule are speculation; drop them.

Severity:
- **high** — the ambiguity or missing branch will affect most realistic inputs.
- **medium** — it will affect some realistic inputs.
- **low** — corner case; would only matter in edge inputs.

Schema:

```json
{ "gaps": [{ "id": "G1", "kind": "undefined_edge_case|ambiguous_term", "description": "<one sentence: the conditional that is incomplete, or the term that is undefined>", "related_rule_ids": ["R5"], "severity": "low|medium|high", "suggested_fix": "<concrete one-sentence resolution or structural action>", "fix_strategy": "substring | structural" }] }
```

For `suggested_fix`: **always populate `suggested_fix` with a concrete one-sentence resolution** — for `undefined_edge_case`, write the missing branch verbatim (e.g. `"Add: 'If the user keeps interrupting after 3 attempts, trigger end-call-tool with a brief apology.'"`). For `ambiguous_term`, anchor the vague word with a specific replacement (e.g. `"Replace 'appropriately formal' with 'address callers by surname and use the formal pronoun form throughout.'"`). Empty `suggested_fix` is no longer allowed — if the runner cannot draft a resolution, it must write `suggested_fix: 'TODO: <one-sentence open question for the author>'` and the human handles it in the konuşalım sub-flow.

## Schema lens

Detects structural issues in prompts that use numbered sections (e.g. system prompts, voice agent scripts, structured Vapi flows). The lens parses the body for ATX heading patterns and reports anomalies in section numbering, ordering, parent-child consistency, and heading style.

**Applicability gate:** the lens auto-skips when the body has NO numbered structural headings. Specifically, the lens runs only when at least one of these is present:

- A line matching `^## SECTION \d+\b` (top-level numbered section, e.g. `## SECTION 0 — GLOBAL ENFORCEMENT`)
- A line matching `^### \d+\.\d+\b` (numbered subsection, e.g. `### 0.1 CHANNEL & LANGUAGE`)

If neither pattern appears, emit `{"applicable": false, "findings": [], "reason": "no numbered section headings detected"}` and exit. Don't fabricate findings on flat prompts — the lens is intentionally silent on prose-only or unnumbered prompts.

### Anomaly categories

The `kind` field on each finding identifies the structural defect detected:

| `kind` | Pattern | Severity heuristic |
|---|---|---|
| `section_gap` | `## SECTION N` then `## SECTION N+2` (or larger jump). Section N+1 is missing entirely. | high (the missing section may indicate a deleted block; downstream cross-references break) |
| `subsection_gap` | `### N.M` then `### N.M+2` (within the same parent section). Subsection N.(M+1) is missing. | medium |
| `out_of_order` | `## SECTION N` after `## SECTION M` where N < M, OR `### N.M` after `### N.K` within the same parent where M < K. | high if section-level, medium if subsection-level |
| `subsection_orphan` | `### A.B` appears under `## SECTION N` where A ≠ N. Subsection number does not match parent section. | high (the most confusing structural bug — readers / downstream tools follow the wrong context) |
| `heading_style_inconsistent` | Some `## SECTION N` headings use ALL CAPS, others use Title Case. Or some `### N.M` lines use `### N.M TITLE` while others use `### N.M Title`. | low |
| `missing_parent` | `### N.M` appears with no preceding `## SECTION N` (e.g. the body opens with `### 5.1` and no `## SECTION 5` ever appears). | high |
| `step_gap` | `STEP N` (uppercase, standalone line or in heading) followed by `STEP N+2` within the same subsection. Common in flow-style instructions. | medium |
| `non_applicable` | Reported only as `applicable: false` at the top of the output — not an individual finding. | n/a |

### Severity heuristics

- **high** — `section_gap` (a whole numbered section is missing), `subsection_orphan` (subsection under wrong parent), `missing_parent` (subsection has no parent), section-level `out_of_order`. These affect document navigation and cross-references.
- **medium** — `subsection_gap`, subsection-level `out_of_order`, `step_gap`.
- **low** — `heading_style_inconsistent`.

### suggested_fix conventions

For `section_gap` (Section N → N+2, missing N+1):

- `fix_strategy: "structural"`, `suggested_fix`: `"Insert a 'Section N+1 — <Placeholder Title>' heading between Section N and Section N+2, OR renumber Section N+2 → Section N+1 and shift all subsequent sections down by 1."`

For `subsection_gap` (N.M → N.M+2, missing N.M+1):

- `fix_strategy: "structural"`, `suggested_fix`: `"Insert a 'Subsection N.(M+1) — <Placeholder Title>' heading, OR renumber N.(M+2) → N.(M+1) and shift subsequent subsections in this parent section."`

For `out_of_order`:

- `fix_strategy: "structural"`, `suggested_fix`: `"Reorder so that <heading-after> appears before <heading-before>."`

For `subsection_orphan` (e.g. `### 5.1` under `## SECTION 4`):

- `fix_strategy: "structural"`, `suggested_fix`: `"Renumber '### 5.1 <TITLE>' to '### 4.X <TITLE>' (where X is the next free subsection number under Section 4), OR move this subsection under the correct '## SECTION 5' parent."`

For `heading_style_inconsistent`:

- `fix_strategy: "substring"`, `suggested_fix`: a concrete rewrite of one heading to match the dominant style (e.g. `"## SECTION 3 — READER PROFILE MEMORY"` if other sections use ALL CAPS). This is the only schema finding that can produce a clean substring replacement.

For `missing_parent`:

- `fix_strategy: "structural"`, `suggested_fix`: `"Add a '## SECTION N — <Inferred Title>' heading before the first '### N.M' heading."`

For `step_gap`:

- `fix_strategy: "structural"`, `suggested_fix`: `"Add 'STEP N+1 — <Placeholder>' between STEP N and STEP N+2, OR renumber STEP N+2 to STEP N+1."`

### Output schema (schema.json)

```json
{
  "applicable": true,
  "reason": null,
  "findings": [
    {
      "id": "S1",
      "kind": "section_gap | subsection_gap | out_of_order | subsection_orphan | heading_style_inconsistent | missing_parent | step_gap",
      "severity": "low | medium | high",
      "line": 280,
      "current_excerpt": "## SECTION 7 — VALUE FRAMING AXES",
      "related_lines": [220, 280],
      "rationale": "Section 5 (line 220) is directly followed by Section 7 (line 280). Section 6 is missing.",
      "suggested_fix": "Insert a 'Section 6 — <Placeholder Title>' heading between line 220 and line 280, OR renumber Section 7 → Section 6 and shift subsequent sections down by 1.",
      "fix_strategy": "structural",
      "rule_ids": []
    }
  ]
}
```

Schema findings use `lens: "schema"` when merged into `findings.json`. `rule_ids: []` is intentional — the schema lens parses headings directly, not the rule list. It does **not** depend on `rules.json`.

### fix_kind dispatch

All schema findings emit `fix_kind: "replace"` (they are textual corrections to the prompt structure). Phase 10 routes them through the normal apply flow; substring-style heading edits use substring replace, structural reorderings use the Edit tool with a risk warning. Empty `suggested_fix` is invalid (same invariant as conflict/dominance/gap).

## Drift lens (handled by `drift-runner` subagent)

The drift lens does not analyse text — it constructs adversarial scenarios, simulates the prompt under each, and judges the outputs. It runs only when the body has anchors, conflicts, or role-override dominances; otherwise it adds no signal and is skipped.

The `drift-runner` subagent has its own definition (`agents/drift-runner.md`) and writes `drift.json` with shape:

```json
{
  "scenarios": [{ "id": "S1", "kind": "regression|conflict|role-override|boundary|ambiguity|normal", "input": "...", "assertions": [...], "rubric": "...", "derived_from": "anchor#|R#|G#|probe:<name>" }],
  "runs":      [{ "scenario_id": "S1", "output": "...", "model": "...", "provider": "..." }],
  "verdicts":  [{ "scenario_id": "S1", "pass": true, "score": 0.85, "reasons": [...], "violated_assertions": [] }]
}
```

The merged `findings.json` produced by the skill in Phase 6 promotes each **failing** verdict to a finding with `lens: "drift"`, severity inferred from `score` (≤ 0.5 = high, ≤ 0.75 = medium, else low). `suggested_fix` is empty for drift findings — the rationale describes the divergence, but the fix depends on which static finding caused the drift (conflict / dominance / gap).

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
