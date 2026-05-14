---
description: Audit a prompt file for conflicts, dominance, drift, and gaps.
argument-hint: <prompt-path>
allowed-tools: Agent
---

# /prompt-check

Run the PromptChecker pipeline against the prompt file at `$1`.

Dispatch the `prompt-check-orchestrator` agent. Pass it the absolute path to `$1`. Wait for completion and surface its final summary.
