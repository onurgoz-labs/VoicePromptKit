# Pipeline and architecture

## Pipeline

The plugin runs one orchestrating skill that fans out to three subagents for the lens work. `static-lens-runner` is dispatched on every run; `drift-runner` and `tr-phonetic-runner` are conditional (drift only when anchors / conflicts / role-overrides exist AND `expand_count > 0`; TR only when `tr_phonetic: true`).

**Parallel topology:** Phase 4 fans out FIVE concurrent `Agent` calls in a single assistant turn: conflict, dominance, gap, schema (each via static-lens-runner with `selected_lenses` singleton), and tr-phonetic (via tr-phonetic-runner). Phase 5 (drift) is downstream of conflict + dominance + gap — drift-runner reads those three artefacts as inputs, so it starts as soon as those three land (doesn't wait for schema or tr-phonetic). Phase 7 waits for all six lens outputs before rendering.

Phase 9 and Phase 10 are the interactive layer — they run automatically after Phase 8 in `/prompt-check` and re-enter from `/prompt-check-resume`.

1. **Phase 0** — First-run wizard or load existing `.voicepromptkit.json`.
2. **Phase 1** — Allocate a fresh `run-NNN` directory (atomic; the `latest` pointer is updated only on success).
3. **Phase 2** — Parse frontmatter deterministically and split body. Stores `body_line_offset`, `prompt_sha256`, `body_char_count`, and `compact_mode` (true when body_char_count > max_char_limit AND max_char_limit > 0). Phase 4-6 dispatches propagate these to each runner.
4. **Phase 3** — Extract atomic, line-anchored rules from `body.txt`.
   - **Phase 3.5 — Per-run lens-selection wizard.** VoicePromptKit emits an `AskUserQuestion` widget asking which of the six lenses to apply. Repo defaults from `.voicepromptkit.json` seed which options are pre-checked, but the question itself is MANDATORY — prose substitutes are a contract violation. If the user is in a headless context where AskUserQuestion is unavailable, the audit aborts with a clear error rather than proceeding silently.
5. **Phase 4 — Parallel lens dispatch (5 concurrent Agent calls).** Emits five Agent calls in one turn:
   - `static-lens-runner` × 4 (conflict / dominance / gap / schema, each with singleton `selected_lenses`)
   - `tr-phonetic-runner` × 1 (conditional on user_intent.tr_phonetic_enabled)
   The skill awaits all five before proceeding. Schema lens auto-skips on flat prompts with no numbered headings.
6. **Phase 5 — Drift (downstream of static lenses).** Triggered as soon as conflicts.json + gaps.json + dominances.json land. Runs in parallel with schema and tr-phonetic if those are still working. Conditional: skipped when expand_count == 0 or no anchors/conflicts/role-overrides.
7. **Phase 7 — Render.** Awaits all six lens outputs. Builds findings.json + report.md (line numbers translated back to the original prompt file; `prompt_sha256` carried through). Phase 7 renders findings as a markdown TABLE with columns `id | mercek | önem | bölüm / satır | açıklama | düzeltme` (TR) or `id | lens | sev | section / line | rationale | fix` (EN). Runners self-cap rationale at ≤200 chars and fix at ≤150 chars — render uses full text verbatim, no truncation.
8. **Phase 8** — Update the `latest` pointer (`latest.txt` + POSIX symlink; commit point), print terminal summary.
9. **Phase 9** — Render summary table from `findings.json`. Bootstrap `session.json` (all findings start `pending`). Accept free-form decision string from the user, parse it, apply TR routing rule, append each decision to `decisions.jsonl`.
10. **Phase 10** — Process decisions: dismissed (log only), overlay (rebuild `inline-suggestions.md`), applied (SHA256-guarded prompt edits, with auto-conversion to overlay on stale audit or ambiguous occurrences), discussed (per-finding sub-dialogue with accept / revise / overlay / dismiss). Re-render `session.json` snapshot. Print Phase 10 summary. Phase 10's konuşalım sub-flow (per-finding deep dialogue) MANDATES `AskUserQuestion` for the four-option choice (kabul / revize / overlay / atla). Free-text follow-ups (revised suggestion text) use plain conversational input — that's intentional. The four-option choice itself is always AskUserQuestion.

## Architecture

The plugin is pure Claude Code skill orchestration. No Node, no TypeScript, no `npm install`, no `node_modules`, no `package.json` — four skill files, a handful of references, two commands, three subagent definitions (one always dispatched, two conditional), and three Python helper scripts.

```
.claude-plugin/
├── plugin.json
└── marketplace.json
skills/
├── prompt-check/
│   ├── SKILL.md
│   └── references/
│       ├── lens-rules.md        (entry point for the static-lens criteria)
│       ├── lens-rules/          (per-lens criteria: _shared, conflict, dominance, gap, schema)
│       ├── tr-phonetic.md       (Turkish TTS rules)
│       ├── probes.md            (drift probe templates)
│       ├── dialog-flow.md       (Phase 9 templates + decision grammar)
│       └── overlay-format.md    (inline-suggestions.md + decisions.jsonl spec)
├── prompt-chat/SKILL.md         (live call simulator — bootstrap)
├── prompt-chat-session/SKILL.md (chat session entry — execs the Python runner)
└── prompt-test/SKILL.md         (anchor regression runner)
commands/
├── prompt-check.md
└── prompt-check-resume.md
agents/
├── drift-runner.md              (conditional — adversarial scenarios + judging)
├── static-lens-runner.md        (always dispatched — conflict + dominance + gap + schema)
└── tr-phonetic-runner.md        (conditional — advisory-only TR lens)
bin/
├── prompt-chat-runner.py        (long-lived chat REPL orchestrator)
├── read-anchors.py              (anchor sidecar reader / validator)
└── codex-lens.py                (Codex CLI backend dispatcher — backend: codex)
examples/
├── sample-system.md
├── sample-agent.md
└── sample-vapi.md               (sets tr_phonetic: true)
```

All cross-phase state is exchanged via JSON files under `.voicepromptkit/<basename>/run-NNN/`. The orchestrating skill reads its own writes; each subagent reads paths it is given and writes only the JSON artefacts assigned to it.
