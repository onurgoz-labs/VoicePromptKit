---
name: behavior-runner
description: Execute scenarios against the target model via the lib/runner.ts CLI. Aggregate Run[] results.
tools: Bash, Read
---

You receive: `scenarios.json` path, the prompt-under-test file path, and frontmatter (model, provider override if any).

## Procedure

1. Write a payload JSON file at `.promptcheck/.tmp/run-payload.json` containing:
   ```json
   {
     "promptPath": "...",
     "model": "...",
     "providerOverride": "anthropic | openai | null",
     "scenarios": [/* full Scenario[] */],
     "cacheSystemPrompt": true
   }
   ```
2. Invoke: `node --import tsx lib/runner.ts .promptcheck/.tmp/run-payload.json`
3. The runner writes `.promptcheck/.tmp/runs.json` and prints the same JSON to stdout.
4. Read and return the JSON exactly as your output.

## Output

```json
{
  "runs": [
    {
      "scenario_id": "S1",
      "output": "<model output>",
      "tokens": { "input": 100, "output": 40 },
      "latency_ms": 1200,
      "model": "claude-opus-4-7",
      "provider": "anthropic"
    }
  ]
}
```

If the runner exits non-zero, output `{"runs": [], "error": "<stderr>"}`.
