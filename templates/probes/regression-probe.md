# Regression probe template

**When to use:** User has provided anchors in frontmatter. Each anchor becomes a regression probe verbatim.

**Procedure:**
- Copy the anchor `input` directly to `scenario.input`.
- Copy `expect_contains` → `assertions: [{kind: 'contains', value: ...}]`.
- Copy `expect_not_contains` → `assertions: [{kind: 'not_contains', value: ...}]`.
- Copy `rubric` → `scenario.rubric`.

Regression probes are the only deterministic probes — they exist to catch behavioural drift across prompt edits.
