---
description: Audit a prompt file interactively. Asks which lenses to apply, runs them in parallel, presents a summary, and lets you decide per-finding what to do (apply / overlay / dismiss / discuss). All decisions are logged to decisions.jsonl. The original prompt file is only modified when you explicitly choose "düzelt" on an apply-eligible finding (non-TR, or TR number_readability / punctuation).
argument-hint: <prompt-path>
allowed-tools: Skill, Read, Write, Bash, Agent, AskUserQuestion
---

# /prompt-check

Invoke the `prompt-check` skill in **interactive mode** against the prompt file at `$1`.

## What this command does

This is the only entry point for auditing a prompt. The skill drives the whole conversation:

1. **Phase 0 — Wizard (first run only).** If `.voicepromptkit.json` does not exist at the repo root, the skill walks the user through the 7-question setup wizard and saves repo defaults. Surface every wizard question verbatim.
2. **Phase 3.5 — Lens selection.** The skill asks via `AskUserQuestion` which lenses to apply (multi-select: `conflict`, `dominance`, `gap`, `drift`, `tr_phonetic`, `schema`). If `drift` is selected, it asks for `expand_count`. It also offers (advisory) to add anchors to the `<prompt>.anchors.yaml` sidecar. See `skills/prompt-check/references/dialog-flow.md` for the exact templates.
3. **Phases 1–8 — Audit.** The skill runs deterministic frontmatter extraction, rule extraction, the selected lenses (drift via the `drift-runner` subagent when warranted), and renders `report.md` + `findings.json`.
4. **Phase 9 (cont.) — Summary view.** The skill renders all findings in a single sortable table and asks for a **free-form decision string** (e.g. `C1, C3 düzelt; G2 yorum bırak; T1..T5 konuşalım; gerisini atla`). Surface the table and the prompt verbatim.
5. **Phase 10 — Action dispatch.** The skill parses the decision string and processes each finding:
   - `düzelt` / `apply` → applies `suggested_fix` to the prompt file (subject to the TR-routing rule below).
   - `yorum bırak` / `overlay` → writes a comment into the `inline-suggestions.md` overlay; original file untouched.
   - `atla` / `dismiss` → logged only.
   - `konuşalım` / `discuss` → enters the per-finding sub-dialogue (4 options: accept / revise / overlay / skip). Surface every sub-question verbatim.
   - Every decision is appended to `decisions.jsonl` in the run directory.

## Hard rules

- **TR routing (per-category).** TR phonetic findings split by category. `foreign_word` / `abbreviation` (advisory) are auto-routed to the overlay even when the user says `düzelt` — the prompt is never modified by these. `number_readability` / `punctuation` follow the normal apply flow and DO modify the prompt on `düzelt`. The skill announces any redirect explicitly per finding.
- **No batch mode.** Batch execution has been retired. There is no flag, no fallback, no shortcut around the interactive flow. If the user asks for non-interactive operation, explain that interactive selection is now mandatory and point at the per-finding `atla` verb for fast dismissal.
- **Resume.** If the user wants to resume an unfinished session, point them at `/prompt-check-resume [run-id]` (sister command). This command does not resume.
- **Surface verbatim.** Every `AskUserQuestion` payload, summary table, decision prompt, and "konuşalım" sub-dialogue emitted by the skill MUST reach the user unchanged. Do not summarise, paraphrase, or batch them.

## Argument

- `$1` — relative or absolute path to the prompt file under audit. The skill resolves it to an absolute path during Phase 1.
