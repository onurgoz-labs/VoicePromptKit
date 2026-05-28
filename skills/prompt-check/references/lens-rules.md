# Lens criteria — split into shared + per-lens slices

This file is the **index** for the lens criteria. Each static lens now reads only its own slice plus the shared invariants — instead of the full 425-line monolithic ruleset every previous version loaded.

## Slices

| Slice | File | Read by |
|---|---|---|
| Shared invariants | [`lens-rules/_shared.md`](lens-rules/_shared.md) | every static-lens singleton dispatch |
| Conflict lens | [`lens-rules/conflict.md`](lens-rules/conflict.md) | conflict singleton dispatch |
| Dominance lens | [`lens-rules/dominance.md`](lens-rules/dominance.md) | dominance singleton dispatch |
| Gap lens | [`lens-rules/gap.md`](lens-rules/gap.md) | gap singleton dispatch |
| Schema lens | [`lens-rules/schema.md`](lens-rules/schema.md) | schema singleton dispatch |

## What's in `_shared.md`

Cross-lens content every lens depends on:

- Output invariant (every finding needs `suggested_fix`)
- `fix_strategy` — substring vs structural
- Rule extraction schema (used in Phase 3 of the skill)
- Severity heuristics across lenses
- Compact mode policy table (per-lens trim rules)
- Section reference (`section_ref` field, lookup conventions, rendering rules)
- Render contract (markdown table format, language switching, sort order)

## What's in each lens slice

Only the lens-specific criteria:

- Definition of what the lens detects
- Severity heuristic for that lens
- JSON schema for that lens's output
- `suggested_fix` conventions for that lens
- Lens-specific invariants (e.g. Dominance's "dialog state preservation" rubric and metadata invariant)

## Drift lens

The drift lens does not analyse text — it constructs adversarial scenarios, simulates the prompt under each, and judges the outputs. It runs only when the body has anchors, conflicts, or role-override dominances; otherwise it adds no signal and is skipped.

The `drift-runner` subagent has its own definition (`agents/drift-runner.md`) and writes `drift.json` with shape:

```json
{
  "scenarios": [{ "id": "S1", "kind": "regression|conflict|role-override|boundary|ambiguity|normal", "input": "...", "assertions": [...], "rubric": "...", "derived_from": "anchor#|R#|G#|probe:<name>" }],
  "runs":      [{ "scenario_id": "S1", "output": "...", "model": "...", "provider": "..." }],
  "verdicts":  [{ "scenario_id": "S1", "pass": true, "score": 0.85, "reasons": [...], "violated_assertions": [] }]
}
```

The merged `findings.json` produced by the skill in Phase 7 promotes each **failing** verdict to a finding with `lens: "drift"`, severity inferred from `score` (≤ 0.5 = high, ≤ 0.75 = medium, else low). `suggested_fix` is empty for drift findings — the rationale describes the divergence, but the fix depends on which static finding caused the drift (conflict / dominance / gap).

## Backward compatibility

Callers that pass `lens_rules_ref: <path to lens-rules.md>` (single-file API) still get a valid file — this index — but the file no longer contains the criteria inline. Runners receiving the single-file path should look at this index, then read the slice they need. Newer dispatches pass `lens_rules: {shared, conflict, dominance, gap, schema}` directly and skip the indirection.

See `agents/static-lens-runner.md` for the input schema.
