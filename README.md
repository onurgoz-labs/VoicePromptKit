# PromptChecker

Claude Code plugin that audits prompts through **four lenses**:

| Lens | Looks for | Implementer |
|---|---|---|
| **Conflict** | Rules that logically contradict each other (e.g. "always formal" + "be casual") | `conflict-lens` agent (static) |
| **Dominance** | Rules that silently override others by position, length, specificity, recency, or role-override patterns | `dominance-lens` agent (static) |
| **Gap** | Undefined edge cases, ambiguous terms, missing failure modes | `gap-lens` agent (static) |
| **Drift** | Behavioural drift between the prompt's stated rules and the model's actual output, surfaced via adversarial scenarios run against a real LLM | `scenario-generator` → `behavior-runner` → `judge` pipeline (dynamic) |

The lens registry lives in `lib/lenses.ts`. Extending the toolkit with a 5th lens means adding an entry there and wiring an implementer agent.

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

## Pipeline

1. **Parse frontmatter** (`lib/frontmatter-cli.ts`) — type, target model, anchors, output formats.
2. **Extract rules** (`rule-extractor` agent) — atomic, line-anchored, categorised obligations.
3. **Static lenses run in parallel** — conflict + dominance + gap all consume the rule list.
4. **Drift lens (dynamic pipeline)** — scenarios are generated, run against Claude (default) or OpenAI/Codex, judged with rule assertions + LLM rubric.
5. **Reporters** — inline annotations on the prompt file, plus optional Markdown / HTML / JSON files under `.promptcheck/`.

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
