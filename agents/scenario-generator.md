---
name: scenario-generator
description: Produce normal + adversarial + boundary test scenarios from rules, anchors, gaps, and probe templates.
tools: Read
---

You receive: `Rule[]`, `Anchor[]` (user-provided seeds), `Gap[]`, and a path to `templates/probes/` containing five probe template files. Read the templates.

## Output

```json
{
  "scenarios": [
    {
      "id": "S1",
      "kind": "regression | conflict | role-override | boundary | ambiguity | normal",
      "input": "<exact user-facing input to send to the model under test>",
      "assertions": [
        { "kind": "contains | not_contains | regex | length_max | length_min", "value": "..." }
      ],
      "rubric": "<optional natural-language rubric for the LLM judge>",
      "derived_from": "<anchor#, R#, G#, or 'probe:conflict-probe'>"
    }
  ]
}
```

## Generation rules
- For each anchor → produce 1 regression scenario verbatim, copy `expect_contains` / `expect_not_contains` into assertions, copy `rubric`.
- For each Conflict cluster → produce 1 conflict scenario whose input forces the model to pick a side. Specialise the conflict-probe template.
- For each role-override Dominance → produce 1 role-override scenario.
- For each Gap → produce 1 ambiguity or boundary scenario.
- Cap total scenarios at `frontmatter.expand_count + anchors.length + min(2, conflicts.length + gaps.length)`.
- Every scenario MUST have at least one assertion OR a rubric (otherwise judge cannot evaluate).
- Assertions must be machine-checkable; rubrics handle subjective criteria (tone, intent).

Output only the JSON.
