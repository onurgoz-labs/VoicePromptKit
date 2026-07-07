# Cost tuning

VoicePromptKit exposes four model knobs plus a compact mode for long prompts. Most users never need to touch them — the defaults are already tuned — but everything is documented here. The knobs are set per prompt via frontmatter; `target_model`, `worker_model`, and `judge_model` also have matching env vars and `.voicepromptkit.json` keys — see [Configuration](configuration.md) for the exact names.

## The model knobs

| knob | default | scope |
|---|---|---|
| `target_model` | `claude-opus-4-7` | drift Step 2 simulation (regression — production fidelity) |
| `worker_model` | `claude-haiku-4-5-20251001` | static-lens × 4, tr-phonetic, drift Step 1 + 3 |
| `judge_model` | `claude-haiku-4-5-20251001` | drift Step 3 rubric eval (separately tunable, cross-provider OK) |
| `chat_model` | `claude-haiku-4-5-20251001` | `prompt-chat-runner` persona dispatches (exploration — fast + cheap) |

## Compact mode for long prompts

When a prompt body exceeds `max_char_limit` (default `50000` chars; configurable via wizard, env var, project config, or per-prompt frontmatter), VoicePromptKit enters **compact mode** and applies cheaper analysis policies to trade depth for speed:

- **Conflict / Gap lenses:** skip `low` severity findings; keep `medium` and `high`.
- **Dominance lens:** emit only `role-override` and `recency` mechanisms; skip the subtler `position`, `length`, `specificity` effects.
- **Conflict lens pair budget:** pick the 50 most-impactful rules (those with "always", "never", "must", "only", "ignore") and compare only within that set. Caps work at ~1250 comparisons regardless of prompt size.
- **Drift lens:** halve the effective `expand_count` (`max(1, n // 2)`). A 5-scenario drift becomes 2 scenarios. This is the single biggest perf lever for long-prompt audits.
- **Rule extraction (Phase 3):** rule `text` ≤ 100 chars, `source_excerpt` ≤ 120 chars. Trims the payload downstream lenses load.
- **Schema and TR phonetic lenses:** unchanged. Both are heading-level / line-level and cheap regardless of size.

To **disable compact mode entirely**, set `max_char_limit: 0` in your `.voicepromptkit.json` (or per-prompt frontmatter, or env var). The audit runs at full depth regardless of body size — useful for forensic audits where you want every finding.

To **lower the threshold** (e.g. 25000 chars so compact mode kicks in sooner), set `max_char_limit: 25000` at any layer.

Phase 8's terminal summary reports the body size + threshold + active/inactive state:

```
Body size: 87432 chars [compact mode ACTIVE — exceeds 50000 char threshold]
```

Compact mode is NOT a hard abort — the audit always runs. It only trims which findings are reported and which scenarios drift simulates. The artefact files (`conflicts.json`, `drift.json`, etc.) carry a top-level `compact_mode: true` field + `compact_policy` array so consumers know the policies fired.

## Cost controls — version history (v0.5.2 → v0.5.6)

Six additive cost reductions kick in automatically — most users never need to think about them, but they're documented here for tuning:

- **Prompt caching.** The simulated system prompt (the prompt body) is identical across every scenario in a batch and across every turn within a flow scenario. When the underlying provider supports it (Anthropic API: `cache_control: {type: "ephemeral"}`; OpenAI: automatic), drift-runner and the chat runner (`bin/prompt-chat-runner.py`) attach the directive to the system block. First call populates the cache; later calls in the same batch / flow hit it. Saves ~50-60% on simulation tokens for long-body prompts.
- **Judge model swap.** Rubric evaluation in drift-runner's Step 3 ("did the output behave like X?") is a yes/no judgement task that doesn't need a frontier model. v0.5.2 adds a `judge_model` frontmatter field (and matching env var / project-config keys) defaulting to `claude-haiku-4-5-20251001`. `target_model` still drives simulation — that's where persona faithfulness matters. Override per prompt: `judge_model: claude-opus-4-7` in frontmatter if you want Opus rubric eval for tricky cases.
- **Batched flow rubric eval.** A flow anchor with K assertion steps previously cost K judge LLM calls (one per `assistant_expect` / `end_call_expect`). v0.5.2 collapses these into ONE batched judge call: the judge sees the full transcript + a numbered list of (step, rubric) pairs and returns all per-step verdicts in one JSON document. Simulation stays sequential (multi-turn state matters); judging batches safely because rubric eval is independent per step.
- **`worker_model` for infrastructure subagents (v0.5.3).** Static-lens-runner (conflict, dominance, gap, schema pair comparison) and tr-phonetic-runner (line-level pattern matching) are structured tasks that don't need a frontier model. v0.5.3 adds `worker_model` frontmatter field (default `claude-haiku-4-5-20251001`) which the skill passes to subagent dispatches via the Agent tool's `model` parameter. `target_model` semantics narrows to "model under test" — drift Step 2 simulation uses it (Opus by default, since it simulates the production model). The three knobs:
  - `target_model` (default `claude-opus-4-7`) — production model your prompt will run on. drift Step 2 simulation. (Chat simulation moved to its own `chat_model` knob in v0.5.6 — see below.)
  - `worker_model` (default `claude-haiku-4-5-20251001`) — VoicePromptKit's own LLM workers. static-lens + tr-phonetic + drift Step 1.
  - `judge_model` (default `claude-haiku-4-5-20251001`) — drift Step 3 rubric eval. Tunable separately for tricky judging.

  Single audit on an 840-line prompt with 116 rules:
  - v0.5.2: ~500k tokens (static-lens ×4 each ~74k Opus, tr-phonetic ~53k Opus, drift ~77k mixed).
  - v0.5.3: ~200k tokens (static-lens ×4 ~10k Haiku each, tr-phonetic ~7k Haiku, drift ~77k mixed — drift unchanged since its Step 2 simulation IS the model under test).

Net effect on the canonical sample-vapi flow anchor (4 turns):
- v0.5.1: 4 Opus simulation + 4 Opus judge = 8 Opus calls per anchor.
- v0.5.2: 4 Opus simulation (body cached after turn 1) + 1 Haiku judge = ~4 Opus + 1 Haiku.
- v0.5.3: same as v0.5.2 for flow anchors (drift Step 2 still target_model; Step 1 + 3 already Haiku). The savings come from non-drift lenses — see the audit example above.

- **Bare Claude subprocess + Python orchestrator (v0.5.6).** v0.5.4's "persistent subagent" design did NOT deliver the promised savings — Claude Code's `SendMessage` triggers transcript replay on each call, re-processing body + history every turn (~32k tokens / ~50s observed). v0.5.6 takes a fundamentally different approach:
  - `/prompt-chat-session` skill execs `bin/prompt-chat-runner.py` (a small Python script, stdlib + PyYAML only) which owns the chat REPL.
  - The Python script spawns ONE long-lived `claude` subprocess with `--input-format stream-json --output-format stream-json --include-partial-messages --system-prompt-file <body> --session-id <uuid> --disable-slash-commands --allowedTools "" --permission-mode bypassPermissions`, cwd=`/tmp` (no CLAUDE.md auto-discovery).
  - Each user turn is one JSON-line to subprocess stdin; assistant text streams back via `text_delta` events (low TTFT, user sees the reply as it's typed).
  - Slash commands (/save /history /reset /commit /quit) are Python-side handlers — no subprocess per command, no permission prompt.
  - `/reset` regenerates the session UUID, kills the old subprocess, spawns a fresh one.

  **Measured per-turn cost** (5-line test body, Haiku): ~3-5k tokens / ~3-5s/turn. **v0.5.4 vs v0.5.6: ~90% cost reduction, ~90% latency reduction.**

  The `chat_model` frontmatter knob (default `claude-haiku-4-5-20251001`) selects the model the subprocess uses. Override to Sonnet / Opus in frontmatter for tricky persona testing.
