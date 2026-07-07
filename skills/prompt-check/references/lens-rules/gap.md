# Gap lens (strict scope)

Read alongside `_shared.md` (which carries the output invariant, fix_strategy, severity heuristics, compact mode, section_ref, compact writing, and language switching every lens depends on).

You flag only gaps that exist **within the prompt's own rules**, not absent concepts the prompt never addresses. Every gap must cite at least one `related_rule_id` that demonstrates the prompt itself raised the question.

## Two kinds of gap

**1. Undefined edge case (incomplete conditional).**
A rule introduces a conditional ("if X, do A") but the rule set never covers the complementary case ("if not X" or "otherwise").

- ✅ Flag: `R3: "If the customer is upset, prioritise satisfaction"` — no rule covers what to do when the customer is not upset and a policy conflict arises.
- ❌ Don't flag: A prompt with a happy-path persona that never mentions angry users. No conditional → nothing to be incomplete.

**2. Ambiguous term used inside a rule.**
A rule uses a vague evaluative word — `appropriate`, `reasonable`, `professional`, `clear`, `concise`, `polite`, `friendly`, `formal`, `casual`, `comprehensive`, `brief`, `thorough` — without another rule or definition anchoring it.

- ✅ Flag: `R5: "Be appropriately formal"` — "appropriate" is undefined; no other rule clarifies the formality scale.
- ❌ Don't flag: A prompt that omits a tone instruction altogether. No ambiguous term → nothing to clarify.

## Out of scope (do not flag)

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
{ "gaps": [{ "id": "G1", "kind": "undefined_edge_case|ambiguous_term", "description": "<one sentence: the conditional that is incomplete, or the term that is undefined>", "related_rule_ids": ["R5"], "severity": "low|medium|high", "suggested_fix": "<concrete one-sentence resolution or structural action>", "fix_strategy": "substring | structural", "section_ref": { "section": "7", "subsection": "7.2", "section_title": "RESPONSE GUIDELINES", "subsection_title": "TONE & STYLE" } }] }
```

For `suggested_fix`: **always populate `suggested_fix` with a concrete one-sentence resolution** — for `undefined_edge_case`, write the missing branch verbatim (e.g. `"Add: 'If the user keeps interrupting after 3 attempts, trigger end-call-tool with a brief apology.'"`). For `ambiguous_term`, anchor the vague word with a specific replacement (e.g. `"Replace 'appropriately formal' with 'address callers by surname and use the formal pronoun form throughout.'"`). Empty `suggested_fix` is no longer allowed — if the runner cannot draft a resolution, it must write `suggested_fix: 'TODO: <one-sentence open question for the author>'` and the human handles it in the konuşalım sub-flow.
