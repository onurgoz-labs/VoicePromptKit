# PromptChecker

Claude Code plugin that audits prompts through **four lenses**:

| Lens | Looks for | Implementer |
|---|---|---|
| **Conflict** | Rules that logically contradict each other (e.g. "always formal" + "be casual") | `conflict-lens` agent (static) |
| **Dominance** | Rules that silently override others by position, length, specificity, recency, or role-override patterns | `dominance-lens` agent (static) |
| **Gap** | Undefined edge cases, ambiguous terms, missing failure modes | `gap-lens` agent (static) |
| **Drift** | Behavioural drift between the prompt's stated rules and the model's actual output, surfaced via adversarial scenarios run against a real LLM | `scenario-generator` → `behavior-runner` → `prompt-executor` → `judge` pipeline (dynamic) |

The lens registry lives in `lib/lenses.ts`. Extending the toolkit with a 5th lens means adding an entry there and wiring an implementer agent.

## Install

One-time setup from this repo directory:

```
/plugin marketplace add /Users/onur/repos/onurgoz/PromptChecker
/plugin install promptchecker@promptchecker-local
```

After install the plugin auto-loads in every Claude Code session.

For per-session ephemeral install (development):
```
claude --plugin-dir /Users/onur/repos/onurgoz/PromptChecker
```

After local edits during development, run `/plugin marketplace update` + `/reload-plugins` to pick up changes.

## Usage

In Claude Code:

```
/prompt-check path/to/prompt.md
```

The plugin runs entirely inside Claude Code — **no API keys, no SDK installs**. The drift lens dispatches a `prompt-executor` subagent per scenario; Claude Code's own runtime executes them.

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
4. **Drift lens (dynamic pipeline)** — `scenario-generator` builds adversarial probes; `behavior-runner` dispatches `prompt-executor` subagents per scenario in parallel batches; `judge` evaluates outputs with `lib/judge.ts` (mechanical assertions) plus its own LLM reasoning (rubric).
5. **Reporters** — inline annotations on the prompt file, plus optional Markdown / HTML / JSON files under `.promptcheck/`.

## Non-Claude target models (Codex / OpenAI)

When `target_model` is `gpt-*` or `codex-*`, `behavior-runner` routes through an MCP server if you have one installed (e.g. a `codex-mcp` providing a `complete` tool). If no MCP route is available, behavior-runner falls back to the Claude `prompt-executor` and emits a warning in the report — the simulated output is then Claude's best impression of how the target model would respond.

To enable MCP routing, install the relevant MCP server in your Claude Code settings; PromptChecker auto-detects the tool surface.

## Development

```bash
npm install
npm test
npm run typecheck
npm run lint
```

Runtime deps: `js-yaml`, `zod`. No SDK, no network access required for the plugin itself.
