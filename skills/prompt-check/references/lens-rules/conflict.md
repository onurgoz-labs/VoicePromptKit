# Conflict lens

Read alongside `_shared.md` (which carries the output invariant, fix_strategy, severity heuristics, compact mode, section_ref, compact writing, and language switching every lens depends on).

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
{ "conflicts": [{ "id": "C1", "rule_ids": ["R3","R8"], "severity": "low|medium|high", "reasoning": "<≤ 400 chars>", "suggested_fix": "<concrete one-sentence rewrite or structural action>", "fix_strategy": "substring | structural", "section_ref": { "section": "7", "subsection": "7.2", "section_title": "RESPONSE GUIDELINES", "subsection_title": "TONE & STYLE" } }] }
```

For the `suggested_fix` in the merged findings.json: propose a concrete rewrite that resolves the contradiction (e.g. "Replace R8 with: 'Stay warm and approachable while preserving professional language.'"). If no clean resolution exists, write `suggested_fix: 'TODO: pick one of (A) <option>, (B) <option>'` so the author has a starting point. Empty `suggested_fix` is no longer allowed.
