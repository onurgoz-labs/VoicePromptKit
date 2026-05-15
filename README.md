# PromptChecker

A Claude Code plugin that audits your prompts the way a strict reviewer would: it finds rules that **contradict each other**, rules that **silently override others**, **gaps** the prompt never defines, and **behavioural drift** between what the prompt asks and what the model actually does.

You run it on any prompt file — a system prompt, a Claude Code subagent definition, a Vapi voice script, a chained workflow — and get back inline annotations on the offending lines plus a Markdown / HTML / JSON report.

## What it looks for — the four lenses

| Lens | Looks for |
|---|---|
| **Conflict** | Rules that logically contradict each other (e.g. "always formal" + "be casual and friendly") |
| **Dominance** | Rules that silently override others through position, length, specificity, recency, or role-override patterns ("ignore previous instructions…") |
| **Gap** | Undefined edge cases, ambiguous terms ("appropriate", "reasonable"), missing failure modes |
| **Drift** | Behavioural mismatch between the prompt's stated rules and the model's actual output, surfaced by adversarial scenarios run against a real LLM |

The lens registry is `lib/lenses.ts`. Adding a 5th lens means an entry there plus one implementer agent.

## Install

In Claude Code, one-time setup:

```
/plugin marketplace add onurgoz/PromptChecker
/plugin install PromptChecker@onurgoz
```

The plugin auto-loads in every Claude Code session after that. **No API keys, no SDK installs** — the drift lens dispatches a subagent per scenario and Claude Code's own runtime executes it.

## Usage

```
/prompt-check path/to/your/prompt.md
```

Your prompt file should start with YAML frontmatter so the plugin knows what kind of prompt it is and how to test it:

```yaml
---
type: system          # system | agent | vapi | task | chain
target_model: claude-opus-4-7
output: [inline, markdown]   # any of: inline | markdown | html | json
expand_count: 5       # how many adversarial scenarios to synthesise
anchors:              # optional — your hand-picked test inputs
  - input: "I am furious! Your product is garbage!"
    rubric: "de-escalates; remains professional"
  - input: "Can I get a refund 90 days after purchase?"
    expect_contains: ["policy"]
    rubric: "declines politely, cites the 30-day policy"
---
[your prompt body here]
```

All frontmatter fields are optional; the plugin falls back to sensible defaults if you omit them. With no anchors and `expand_count: 0` you still get the three static lenses (conflict, dominance, gap) — no LLM calls happen.

See [`examples/`](examples/) for sample prompts demonstrating each detection lens.

## What the output looks like

**Inline annotations** are injected directly above the offending lines:

```
<!-- PROMPTCHECK [CONFLICT severity=high] L3↔L4: tone contradiction -->
Always be formal and use professional language at all times.
Be casual and friendly to make customers feel at home.

<!-- PROMPTCHECK [GAP severity=medium] L6: no instruction for partial refunds -->
Never offer refunds outside the 30-day window.
```

**Markdown / HTML reports** land under `.promptcheck/<prompt-name>-<date>.{md,html,json}` and include a summary table, a mermaid conflict graph, a dominance list, and the full drift-test matrix.

## Pipeline

1. Frontmatter is parsed.
2. `rule-extractor` agent splits the prompt into atomic, line-anchored rules.
3. The three **static lenses** (conflict, dominance, gap) run in parallel — pure analysis, no LLM calls beyond the agent reasoning itself.
4. The **drift lens** runs as a dynamic pipeline: `scenario-generator` synthesises adversarial probes from the rules + anchors + probe templates; `behavior-runner` dispatches `prompt-executor` subagents per scenario (in parallel batches); `judge` evaluates outputs using deterministic assertions plus its own LLM reasoning for rubrics.
5. Reporters emit inline annotations and/or files per the frontmatter `output` list.

## Non-Claude target models (Codex / OpenAI / others)

If your `target_model` is `gpt-*` or `codex-*` and you have a Codex / OpenAI MCP server installed in your Claude Code settings, `behavior-runner` automatically routes through it. If no such MCP route is available, the plugin falls back to Claude's `prompt-executor` and adds a warning to the report — the simulated output is then Claude's best impression of how the target model would respond.

The plugin itself ships zero provider integrations; transport is the user's MCP setup.

## Development

```bash
npm install
npm test       # vitest — 18 tests covering frontmatter, judge, reporters, lens registry, e2e
npm run typecheck
npm run lint
```

Runtime dependencies: `js-yaml`, `zod`. Plugin requires no network access at runtime.

## License

MIT
