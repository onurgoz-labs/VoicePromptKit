---
description: Resume an unfinished interactive prompt-check session. Reads session.json from a run directory, filters findings by status == "pending", and re-enters the Phase 9 summary view + Phase 10 action dispatch for those findings. Refuses if the prompt file's SHA256 differs from the snapshot taken at audit time.
argument-hint: [run-id]
allowed-tools: Skill, Read, Write, Bash, Agent, AskUserQuestion
---

# /prompt-check-resume

Resume an unfinished interactive VoicePromptKit session — replay the Phase 9 summary view and Phase 10 action dispatch over the findings whose `session.json` status is still `pending`.

## Scope — what "resume" covers (and what it does not)

Resume covers **only pending decisions after a completed analysis**: Phases 1–8 finished, `report.md` + `findings.json` exist, and one or more findings are still `pending` in `session.json`. It replays the interactive review (Phases 9–10) — it does NOT re-run any lens.

An audit that was interrupted **during analysis** (Phases 4–7 — lens dispatch through render) cannot be resumed: there is no complete `findings.json` to review. Re-run `/prompt-check <prompt-path>` instead. This is cheaper than it sounds — every lens that completed before the interruption was mirrored into the content-addressable cache, so the re-run serves those lenses from cache and only re-dispatches the ones that never finished (the Phase 8 summary shows `Cache: N/M lenses served from cache`).

Procedure:

1. **Resolve the run directory from `$1`.**
   - If `$1` is an absolute or relative path to a run directory, use it as-is.
   - If `$1` is a `run-NNN` name, resolve it under `.voicepromptkit/<basename>/<run-NNN>` (the `<basename>` matches the parent directory containing that run).
   - If `$1` is empty, auto-pick the most recent unfinished session. For each prompt directory `.voicepromptkit/<basename>/`, resolve its latest run portably: read the pointer file `<basename>/latest.txt` (it holds the run name → `<basename>/<run-name>/session.json`); if `latest.txt` is absent, fall back to the legacy `<basename>/latest` symlink (`<basename>/latest/session.json`). Among the resolved `session.json` files, pick the one whose mtime is newest **and** still has at least one `findings_state[*].status == "pending"`. If none qualify, surface verbatim and exit:

     ```
     No unfinished sessions. Run /prompt-check <path> to start a new audit.
     ```

2. **Invoke the `prompt-check` skill in resume-mode** with the resolved run directory.
   - **Stale-audit guard (mandatory):** before reading session state, compute `shasum -a 256 <prompt-path>` and compare to `findings.json.prompt_sha256` (snapshotted at audit time). On mismatch, abort with the exact message used by the retired `/prompt-check-apply`:

     ```
     Prompt has changed since this audit (run-NNN). Re-run /prompt-check first.
     ```

   - On match, the skill reads `session.json` from the resolved run directory (schema in `skills/prompt-check/references/dialog-flow.md`).
   - The skill re-renders the Phase 9 summary view, filtered to findings whose `status == "pending"`. Already-applied, overlay, and dismissed entries are shown as context only — not as actionable rows.
   - The skill then enters Phase 10 (action dispatch) over the pending findings.

3. **Audit log is append-only.** Every new action taken in resume-mode appends a fresh entry to `decisions.jsonl` (shape defined in `skills/prompt-check/references/overlay-format.md`). Prior session decisions are not duplicated or re-stated. The skill updates the `session.json` snapshot as it processes each finding so a later resume sees the current status.

4. **Surface the skill's diff summary verbatim** when Phase 10 completes. Do not paraphrase counts or omit warnings.

If the prompt has been edited since the audit (or the user wants to start fresh), they should run `/prompt-check <prompt-path>` — that allocates a new `run-NNN` directory and a new audit snapshot. `/prompt-check-resume` is strictly for continuing an existing run.
