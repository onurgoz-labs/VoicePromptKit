# The six lenses — detailed semantics

`/prompt-check` audits a prompt through six lenses. This page is the full reference; the README carries a one-line-per-lens overview.

| Lens | Looks for | Always on? |
|---|---|---|
| **Conflict** | Rules that logically contradict each other ("always formal" + "be casual and friendly") | yes |
| **Dominance** | Rules that silently override others through position, length, specificity, recency, or role-override patterns ("ignore previous instructions…") | yes |
| **Gap** | Undefined edge cases (incomplete conditionals) and ambiguous terms ("appropriate", "reasonable") that the prompt's own rules raise | yes |
| **Drift** | Behavioural mismatch between the prompt's stated rules and the model's actual output, surfaced by adversarial scenarios | only when anchors / conflicts / role-overrides exist (skipped otherwise) |
| **TR phonetic** | Numbers, abbreviations, foreign words, and pacing problems that break Turkish text-to-speech. Split by category: `foreign_word` + `abbreviation` routed to overlay (voice-design — prompt text never modified); `number_readability` + `punctuation` follow normal apply flow (`düzelt` modifies the prompt). | opt-in via `tr_phonetic: true` frontmatter or project config |
| **Schema** | Section numbering / ordering / heading consistency. Detects gaps (Section 5 → 7), out-of-order subsections (3.3 then 3.2), orphan subsections (5.1 under Section 4), inconsistent heading styles, missing parent sections, and STEP-numbering gaps. | only when the prompt has numbered section headings (auto-skipped on flat prompts) |

## TR phonetic — split by category

The TR lens splits its four detection categories into two routing buckets, so voice-design decisions stay overlay-only while textual corrections follow the normal apply flow.

- **`foreign_word` + `abbreviation`: advisory-only.** Pronunciation hints for `Peugeot → "pöjo"` or `DHL → "de-ha-el"` carry `fix_kind: "advisory"` and always land in the overlay file (`inline-suggestions.md`); the prompt text is never auto-edited, even on `düzelt`. The author hand-merges these into a TTS pronunciation guide block or the voice provider's config. A silent prompt edit (`DHL` becoming `de-ha-el` in the visible script) would corrupt the meaning, so this routing is non-negotiable.
- **`number_readability` + `punctuation`: normal apply flow.** Missing commas, malformed Turkish numbers, monetary spelling (`100 TL → yüz lira`) — these ARE textual fixes. They carry `fix_kind: "replace"`. When the user picks `düzelt` in the Phase 9 dialogue, Phase 10 modifies the prompt file just like a `conflict` or `gap` finding. The user can still route them to overlay via `yorum bırak` per-finding when they want to review by hand.
- **Migration note:** the TR routing rule from earlier versions was over-strict — it forced every TR finding to overlay regardless of category, even when the user explicitly said `düzelt` on a textual fix like a missing comma. v0.4.2 fixes this: only the voice-design categories (`foreign_word`, `abbreviation`) stay advisory.

The lens never translates: `pound → paund` is a phonetic hint; `pound → İngiliz lirası` is forbidden semantic substitution.

## Section-aware findings

For prompts that use numbered section headings (`## SECTION N` + `### N.M`), every finding carries a `section_ref` field pointing to its containing section and subsection. The report.md and inline-suggestions.md surfaces this as a section-aware header instead of the bare line number:

```
### Section 7.2 — L284 [C1 conflict severity=high, R3↔R8] — Tone contradiction...
```

Useful for long prompts (1500+ lines) where the user otherwise has to map line numbers to sections mentally. Findings outside any numbered section (preambles, flat prompts) show the bare line number with no section prefix.

The section index is built deterministically in Phase 3 of the audit (no LLM cost) and propagated to every lens runner via `inputs.section_index`. Schema lens, conflict, dominance, gap, and TR phonetic findings all attach `section_ref` automatically. Drift findings are behavioural and always carry `section_ref: null`.

All six lenses live in [`skills/prompt-check/SKILL.md`](../skills/prompt-check/SKILL.md) and its `references/`.
