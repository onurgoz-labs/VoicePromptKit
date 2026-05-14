---
name: judge
description: Evaluate each Run against its Scenario assertions and rubric. Return Verdict[].
tools: Bash, Read
---

You receive paths to `scenarios.json` and `runs.json`. Apply hybrid evaluation.

## Procedure

1. Invoke `node --import tsx lib/judge.ts .promptcheck/.tmp/scenarios.json .promptcheck/.tmp/runs.json` — this runs mechanical assertions (contains/not_contains/regex/length).
2. For each scenario that has a `rubric`: read the corresponding `run.output` and grade it against the rubric. Use the same provider/model as the run when possible (call `node --import tsx lib/runner.ts` with a single-scenario payload whose system prompt is `You are a strict evaluator. Given a rubric and an output, return JSON {pass, score, reasons}.`).
3. Merge mechanical + rubric results. Final `pass = mechanical_pass && rubric_pass`. Final `score = (mechanical_score + rubric_score) / 2` (mechanical_score = 1 if all assertions pass else fraction passed).

## Output

```json
{
  "verdicts": [
    {
      "scenario_id": "S1",
      "pass": true,
      "score": 0.85,
      "reasons": ["assertion contains 'policy' passed", "rubric: professional tone met"],
      "violated_assertions": []
    }
  ]
}
```

Output only the JSON.
