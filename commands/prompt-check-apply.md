---
description: Apply suggested_fix entries (and inject the pronunciation guide) from a previous /prompt-check audit. Refuses if the prompt file has changed since the audit.
argument-hint: [run-id]
allowed-tools: Skill, Read, Write, Bash
---

# /prompt-check-apply

Apply the fixes from a previous PromptChecker audit to the prompt file.

Procedure:
1. Resolve the run directory from `$1`:
   - If `$1` is a path or a `run-NNN` name, use it directly under `.promptcheck/<basename>/`.
   - If `$1` is empty, use `.promptcheck/<basename>/latest`.
2. Invoke the `prompt-check` skill in **apply-mode**. The skill performs the mandatory stale-audit guard (SHA256 comparison) and then runs Pass 1 (replace findings) followed by Pass 2 (pronunciation guide injection / migration).
3. Surface the skill's diff summary verbatim.

The skill refuses to apply anything when the prompt's current SHA256 does not match the one stored in `findings.json`. In that case re-run `/prompt-check <prompt-path>` first, then `/prompt-check-apply`.
