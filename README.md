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

The four lenses are documented in `agents/prompt-check-orchestrator.md` (top of file). Adding a 5th lens means a new agent definition plus one row in the orchestrator's lens table.

## Install

In Claude Code, one-time setup:

```
/plugin marketplace add onurgoz/PromptChecker
/plugin install PromptChecker@onurgoz
```

The plugin auto-loads in every Claude Code session after that. **No API keys, no SDK installs** — the drift lens dispatches a subagent per scenario and Claude Code's own runtime executes it.

## Usage

Just point it at any prompt file:

```
/prompt-check path/to/your/prompt.md
```

That's it. No frontmatter required, no flags. The plugin uses sensible defaults: tests against `claude-opus-4-7`, generates 5 adversarial scenarios, writes inline annotations to the prompt file. See [`examples/`](examples/) for sample prompts demonstrating each detection lens.

## What the output looks like

**Inline annotations** are injected directly above the offending lines:

```
<!-- PROMPTCHECK [CONFLICT severity=high] L3↔L4: tone contradiction -->
Always be formal and use professional language at all times.
Be casual and friendly to make customers feel at home.

<!-- PROMPTCHECK [GAP severity=medium] L6: no instruction for partial refunds -->
Never offer refunds outside the 30-day window.
```

**Reports** land under `.promptcheck/<prompt-name>-<date>.{md,html,json}` (whichever formats you configured) and include a summary table, a mermaid conflict graph, a dominance list, and the full drift-test matrix.

## Customizing defaults

The plugin's behaviour is layered, highest priority first:

1. **Per-prompt frontmatter** (optional, see below) — overrides everything for one prompt.
2. **Environment variables** (shell-wide) — change the defaults for every prompt in your session.
3. **Built-in defaults** — applied when neither of the above is set.

### Environment variables

| Variable | Effect | Default |
|---|---|---|
| `PROMPTCHECKER_TARGET_MODEL` | Model name written into reports | `claude-opus-4-7` |
| `PROMPTCHECKER_OUTPUT` | Comma-separated subset of `inline,markdown,html,json` | `inline` |
| `PROMPTCHECKER_EXPAND_COUNT` | How many adversarial scenarios the drift lens generates | `5` |
| `PROMPTCHECKER_EXECUTOR` | Drift-lens executor name (see "Executor selection") | `prompt-executor` |

**Recommended — set them in Claude Code's `settings.json`** so they apply to every Claude Code session without touching your shell rc. Edit `~/.claude/settings.json` (user-wide) or your project's `.claude/settings.json` (project wins on conflict) and add an `env` block:

```json
{
  "env": {
    "PROMPTCHECKER_TARGET_MODEL": "claude-opus-4-7",
    "PROMPTCHECKER_OUTPUT": "inline,markdown",
    "PROMPTCHECKER_EXPAND_COUNT": "8",
    "PROMPTCHECKER_EXECUTOR": "prompt-executor"
  }
}
```

Claude Code injects these into every subprocess the plugin spawns, so the PromptChecker CLI scripts see them via `process.env`. Alternatively, exporting them in your shell profile (`~/.zshrc`, `~/.bash_profile`) works the same way.

### Per-prompt frontmatter (advanced)

If you want to test a specific prompt against a different model, or hand-write anchor scenarios, add YAML frontmatter at the top of the prompt file:

```yaml
---
type: system          # system | agent | vapi | task | chain
target_model: claude-opus-4-7
output: [inline, markdown]
expand_count: 5
executor: prompt-executor
anchors:
  - input: "I am furious! Your product is garbage!"
    rubric: "de-escalates; remains professional"
  - input: "Can I get a refund 90 days after purchase?"
    expect_contains: ["policy"]
    rubric: "declines politely, cites the 30-day policy"
---
[your prompt body here]
```

Every field is optional. Most users never write frontmatter at all; the few who care about per-prompt anchors or alternate target models add only the fields they need.

## Pipeline

1. Frontmatter (if present) is parsed and layered over env defaults.
2. `rule-extractor` agent splits the prompt into atomic, line-anchored rules.
3. The three **static lenses** (conflict, dominance, gap) run in parallel — pure analysis.
4. The **drift lens** runs as a dynamic pipeline: `scenario-generator` synthesises adversarial probes from rules + anchors + probe templates; `behavior-runner` dispatches `prompt-executor` subagents per scenario (in parallel batches); `judge` evaluates outputs using deterministic assertions plus its own LLM reasoning for rubrics.
5. Reporters emit inline annotations and/or files per the resolved `output` list.

## Executor selection (Claude subagent vs. Codex / OpenAI MCP)

The drift lens needs a way to invoke a model on each scenario. The plugin uses an explicit chain — no auto-routing.

Resolution order (highest first):

1. Per-prompt frontmatter `executor: <name>`
2. `PROMPTCHECKER_EXECUTOR=<name>` env var
3. Built-in default `prompt-executor`

| Executor name | What it does |
|---|---|
| `prompt-executor` *(default)* | Dispatches a Claude subagent that simulates the prompt under test. No external dependency. |
| `mcp-codex`, `mcp-openai`, … | Calls an MCP tool you've installed in Claude Code that fronts the target provider. You own the MCP server; the plugin discovers `mcp__<server>__*` tools at runtime. |

If a non-default executor is selected but the MCP server isn't installed, `behavior-runner` falls back to `prompt-executor` and adds a warning to the report. The plugin ships zero provider integrations; transport is your MCP setup.

## Architecture

The plugin is **pure Claude Code agent orchestration**. There is no Node, no TypeScript, no `npm install`, no `node_modules`, no `package.json` — just markdown agent definitions, slash-command files, and probe templates.

```
.claude-plugin/
├── plugin.json           # plugin manifest
└── marketplace.json      # local-marketplace descriptor
commands/
└── prompt-check.md       # slash command → orchestrator
agents/
├── prompt-check-orchestrator.md   # top-level coordinator
├── rule-extractor.md
├── conflict-lens.md      # static lens
├── dominance-lens.md     # static lens
├── gap-lens.md           # static lens (strict, prompt-internal only)
├── scenario-generator.md # drift lens — step 1
├── behavior-runner.md    # drift lens — step 2 (dispatches prompt-executor)
├── prompt-executor.md    # faithful simulator
├── judge.md              # drift lens — step 3 (mechanical + rubric)
└── reporter.md           # renders inline/markdown/html/json
templates/probes/         # adversarial probe templates
examples/                 # sample prompts demonstrating each lens
```

All cross-phase state is exchanged via JSON files under `.promptcheck/.tmp/`. Agents read/write those with the Read/Write tools; nothing leaves the Claude Code runtime.

## License

MIT
