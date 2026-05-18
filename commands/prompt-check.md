---
description: Audit a prompt file across four lenses (conflict, dominance, gap, drift) plus optional Turkish phonetic lens. On first run in a repo, asks for repo defaults and saves them to .promptchecker.json. Writes line-anchored findings — never modifies the original file.
argument-hint: <prompt-path>
allowed-tools: Skill, Read, Write, Bash, Agent, AskUserQuestion
---

# /prompt-check

Invoke the `prompt-check` skill against the prompt file at `$1`.

Pass `$1` (relative or absolute) as the prompt path. The skill handles working-directory setup, deterministic frontmatter parsing, rule extraction, all four lenses (and optionally the Turkish phonetic lens when `tr_phonetic: true`), drift via the `drift-runner` subagent (only when warranted), report rendering, and the terminal summary. Surface the skill's terminal summary verbatim.

If the user later says "fix these" or "düzelt bunları", read `.promptcheck/<basename>/latest/findings.json` and apply each `suggested_fix` by matching `line` + `current_excerpt` in the prompt file.
