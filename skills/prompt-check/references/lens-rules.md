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
