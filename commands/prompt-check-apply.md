---
description: Apply suggested_fix entries from a previous /prompt-check audit. Only non-TR-phonetic findings are auto-applied — TR phonetic suggestions are reported only and never modify the prompt. Refuses if the prompt file has changed since the audit.
argument-hint: [run-id]
allowed-tools: Skill, Read, Write, Bash
---

# /prompt-check-apply

Apply the fixes from a previous PromptChecker audit to the prompt file.

Procedure:
1. Resolve the run directory from `$1`:
   - If `$1` is a path or a `run-NNN` name, use it directly under `.promptcheck/<basename>/`.
   - If `$1` is empty, use `.promptcheck/<basename>/latest`.
2. Invoke the `prompt-check` skill in **apply-mode**. The skill performs the mandatory stale-audit guard (SHA256 comparison) and then runs the replace pass on every non-TR finding with `fix_kind: "replace"` and a non-empty `suggested_fix`.
3. Surface the skill's diff summary verbatim.

**TR phonetic findings are never applied.** They appear in `report.md` and `findings.json` as advisory entries (with concrete `suggested_fix` or `pronunciation_entry` payload), but the author decides whether and how to act on them. Apply-mode surfaces their count in the diff summary so the author knows to read the report.

The skill refuses to apply anything when the prompt's current SHA256 does not match the one stored in `findings.json`. In that case re-run `/prompt-check <prompt-path>` first, then `/prompt-check-apply`.
