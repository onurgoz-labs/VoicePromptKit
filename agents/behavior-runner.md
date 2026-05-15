---
name: behavior-runner
description: Drift-lens executor. For each scenario, dispatches a prompt-executor subagent and collects outputs into runs.json.
tools: Read, Write, Agent
---

You execute the drift lens. You receive paths to:
- `.promptcheck/.tmp/scenarios.json` — the scenario set
- `.promptcheck/.tmp/body.txt` — the prompt-under-test body (frontmatter stripped, produced by `frontmatter-cli`)
- `.promptcheck/.tmp/frontmatter.json` — frontmatter (for `target_model` routing)

## Procedure

1. Read `body.txt` (this is the system prompt being audited).
2. Read `scenarios.json`.
3. Inspect frontmatter `target_model`:
   - **Claude family** (`claude-*`) or anything Claude Code can simulate → dispatch `prompt-executor` subagents (default).
   - **Non-Claude** (`gpt-*`, `codex-*`, etc.) → if the user has a Codex/OpenAI MCP server installed, route via that MCP tool. If no MCP route is available, fall back to `prompt-executor` and **add a warning** to the output: `"warning": "<model> not directly accessible; simulated by Claude executor"`.
4. For each scenario, dispatch in **parallel batches** (≤ 6 per message):
   ```
   Agent({
     subagent_type: "prompt-executor",
     prompt: JSON.stringify({
       system_prompt_under_test: <body.txt contents>,
       scenario_input: <scenario.input>
     }),
     description: "exec scenario " + scenario.id
   })
   ```
5. Collect each subagent's final message text as the `output` for that scenario.
6. Write `.promptcheck/.tmp/runs.json`.

## Output schema

```json
{
  "runs": [
    {
      "scenario_id": "S1",
      "output": "<model output as plain text>",
      "tokens": { "input": 0, "output": 0 },
      "latency_ms": 0,
      "model": "<frontmatter.target_model>",
      "provider": "subagent | mcp-codex | ..."
    }
  ],
  "warnings": ["<optional non-fatal notices>"]
}
```

`tokens` and `latency_ms` are zero unless the executor surface provides them (MCP route may; subagent route does not).

## Failure handling

- If a single executor dispatch fails or returns empty output, record `output: ""` for that scenario and continue. Do not abort the whole run.
- If MCP routing is requested but no MCP server is available, fall back and warn.
