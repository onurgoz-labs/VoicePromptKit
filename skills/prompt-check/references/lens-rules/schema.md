# Schema lens

Read alongside `_shared.md` (which carries the output invariant, fix_strategy, severity heuristics, compact mode, section_ref, and render contract every lens depends on).

Detects structural issues in prompts that use numbered sections (e.g. system prompts, voice agent scripts, structured Vapi flows). The lens parses the body for ATX heading patterns and reports anomalies in section numbering, ordering, parent-child consistency, and heading style.

**Applicability gate:** the lens auto-skips when the body has NO numbered structural headings. Specifically, the lens runs only when at least one of these is present:

- A line matching `^## SECTION \d+\b` (top-level numbered section, e.g. `## SECTION 0 — GLOBAL ENFORCEMENT`)
- A line matching `^### \d+\.\d+\b` (numbered subsection, e.g. `### 0.1 CHANNEL & LANGUAGE`)

If neither pattern appears, emit `{"applicable": false, "findings": [], "reason": "no numbered section headings detected"}` and exit. Don't fabricate findings on flat prompts — the lens is intentionally silent on prose-only or unnumbered prompts.

## Anomaly categories

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

## Severity heuristics

- **high** — `section_gap` (a whole numbered section is missing), `subsection_orphan` (subsection under wrong parent), `missing_parent` (subsection has no parent), section-level `out_of_order`. These affect document navigation and cross-references.
- **medium** — `subsection_gap`, subsection-level `out_of_order`, `step_gap`.
- **low** — `heading_style_inconsistent`.

## suggested_fix conventions

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

## Output schema (schema.json)

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
      "current_excerpt": "## SECTION 7 — RESPONSE GUIDELINES",
      "related_lines": [220, 280],
      "rationale": "Section 5 (line 220) is directly followed by Section 7 (line 280). Section 6 is missing.",
      "suggested_fix": "Insert a 'Section 6 — <Placeholder Title>' heading between line 220 and line 280, OR renumber Section 7 → Section 6 and shift subsequent sections down by 1.",
      "fix_strategy": "structural",
      "rule_ids": [],
      "section_ref": { "section": "7", "subsection": null, "section_title": "RESPONSE GUIDELINES", "subsection_title": null }
    }
  ]
}
```

Schema findings use `lens: "schema"` when merged into `findings.json`. `rule_ids: []` is intentional — the schema lens parses headings directly, not the rule list. It does **not** depend on `rules.json`.

## fix_kind dispatch

All schema findings emit `fix_kind: "replace"` (they are textual corrections to the prompt structure). Phase 10 routes them through the normal apply flow; substring-style heading edits use substring replace, structural reorderings use the Edit tool with a risk warning. Empty `suggested_fix` is invalid (same invariant as conflict/dominance/gap).
