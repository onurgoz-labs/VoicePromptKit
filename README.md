# PromptChecker

Claude Code plugin that audits prompts for conflicting instructions, dominating directives, behavioural drift, and gaps.

## Usage

In Claude Code:

```
/prompt-check path/to/prompt.md
```

## Frontmatter contract

```yaml
---
type: system | agent | vapi | task | chain
target_model: claude-opus-4-7
output: [inline, markdown, html, json]
expand_count: 5
anchors:
  - input: "..."
    expect_contains: ["..."]
    rubric: "..."
---
```

## How it works

1. Static lenses extract rules and detect conflicts, dominance, gaps (parallel).
2. Scenario generator builds adversarial tests from rules, anchors, and probe templates.
3. Behaviour runner executes them against Claude (default) or OpenAI/Codex.
4. Judge applies rule assertions + LLM rubric to score each run.
5. Reporters emit inline annotations and/or Markdown/HTML/JSON files.

## Environment

| Var | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Required for Claude provider |
| `OPENAI_API_KEY` | Required for OpenAI/Codex provider |
| `PROMPTCHECK_PROVIDER` | `anthropic` or `openai` — overrides model-based inference |

## Development

```bash
npm install
npm test
npm run typecheck
npm run lint
```
