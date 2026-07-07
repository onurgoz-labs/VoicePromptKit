# Configuration

The plugin's behaviour is layered, highest priority first:

1. **Per-prompt frontmatter** — overrides everything for one prompt.
2. **Environment variables** (`VOICEPROMPTKIT_*`) — change defaults for every prompt in your shell / Claude Code session.
3. **Project config** (`.voicepromptkit.json` at repo root) — repo-level defaults shared with your team.
4. **Built-in defaults** — applied when none of the above is set.

## First-run wizard

The first time you run `/prompt-check` in a repo, the skill walks you through a 7-question wizard and saves the answers to `<repo-root>/.voicepromptkit.json`. Subsequent runs read that file silently. Edit it by hand to change defaults, or delete it to rerun the wizard.

The wizard asks:

1. **Default prompt type** for this repo (`system | agent | vapi | task | chain | unspecified`). Used when a prompt has no `type:` in its frontmatter.
2. **Turkish phonetic lens** active by default? — recommended `true` if you picked `vapi` above, otherwise `false`.
3. **Target model** for reports + drift simulation (`claude-opus-4-7` default, free text accepted).
4. **Output formats** — multi-select from `markdown`, `findings_json`, `json`.
5. **Drift `expand_count`** — how many extra adversarial scenarios beyond the anchor + conflict budget. `0` disables the drift lens entirely.
6. **Max prompt character limit** — repo-level threshold for triggering compact mode. Default `50000`. `0` disables compact mode entirely. Useful when your prompts routinely exceed 50K chars and you want every audit to run at full depth regardless of size.
7. **Report language** — controls the language of report.md, terminal summary, and Phase 9 dialog prompts. Options: `tr` (Türkçe, default) or `en` (English). Lens-generated content (rationale, suggested_fix, current_excerpt) stays in whatever language the runner produced; only skill-side templates translate.

## Project config (`.voicepromptkit.json`)

Example for a Turkish VAPI repo:

```json
{
  "default_type": "vapi",
  "target_model": "claude-opus-4-7",
  "output": ["markdown", "findings_json"],
  "expand_count": 4,
  "tr_phonetic": true,
  "max_char_limit": 50000,
  "report_language": "tr"
}
```

Commit this file so your team gets the same defaults. Unknown keys are ignored (with a one-line warning in the terminal summary), so the file is forward-compatible.

## Environment variables (session-wide overrides)

| Variable | Effect | Falls back to |
|---|---|---|
| `VOICEPROMPTKIT_TARGET_MODEL` | Model name written into reports + drift Step 2 simulation | project config → `claude-opus-4-7` |
| `VOICEPROMPTKIT_WORKER_MODEL` | Model for VoicePromptKit's own LLM workers (static-lens, tr-phonetic, drift Step 1) | project config → `claude-haiku-4-5-20251001` |
| `VOICEPROMPTKIT_JUDGE_MODEL` | Model for drift Step 3 rubric eval | project config → `claude-haiku-4-5-20251001` |
| `VOICEPROMPTKIT_OUTPUT` | Comma-separated subset of `markdown,findings_json,json` | project config → `markdown,findings_json` |
| `VOICEPROMPTKIT_EXPAND_COUNT` | Drift scenarios beyond anchor + conflict budget; `0` disables drift entirely | project config → `3` |
| `VOICEPROMPTKIT_TR_PHONETIC` | Truthy (`1/true/yes/on`) enables the Turkish phonetic lens | project config → `false` |
| `VOICEPROMPTKIT_MAX_CHAR_LIMIT` | Positive integer triggers compact mode when body exceeds this many chars. `0` disables compact mode. | project config → `50000` |
| `VOICEPROMPTKIT_REPORT_LANGUAGE` | `tr` or `en`. Sets skill-render language. | project config → `tr` |
| `VOICEPROMPTKIT_BACKEND` | `claude` (default) or `codex`. Selects the engine that runs the lens analysis (Phases 4–6). See [Codex CLI backend](codex-backend.md). | project config → `claude` |
| `VOICEPROMPTKIT_CODEX_MODEL` | Codex model passed as `codex exec -m <model>` when `backend=codex`. Empty ⇒ Codex's own configured default. | project config → Codex default |
| `VOICEPROMPTKIT_CODEX_CLI` | Absolute path to a non-PATH `codex` binary (escape hatch). | `codex` on PATH |
| `VOICEPROMPTKIT_TIMING` | Truthy (`true`) writes a millisecond-precision phase-boundary log to `$RUN_DIR/timing.log`. Diagnostic only — leave off in normal use. | off |

**Timing logs (diagnostic):** when `VOICEPROMPTKIT_TIMING=true`, the skill writes `timing.log` to the run directory with one line per phase boundary (`phase_2_start`, `phase_2_end`, etc., in milliseconds since the Unix epoch). Use this when a run feels slow — `awk -F'[][]' '{print $2}' timing.log | sort -n` gives you the ordered timestamps; diffing adjacent timestamps surfaces the slowest phase. Drift simulation and subagent dispatch are common hotspots. Leave the env var off in everyday use — the log grows on every run.

Set them in Claude Code's `settings.json` so they apply session-wide without touching your shell rc:

```json
{
  "env": {
    "VOICEPROMPTKIT_TR_PHONETIC": "true",
    "VOICEPROMPTKIT_EXPAND_COUNT": "5"
  }
}
```

## Per-prompt frontmatter

```yaml
---
type: vapi                       # overrides project config.default_type for this prompt
target_model: claude-opus-4-7    # overrides every layer for this prompt
output: [markdown, findings_json]
expand_count: 6                  # overrides project config + env-var
tr_phonetic: true                # overrides project config + env-var
max_char_limit: 100000           # this prompt is large; raise the threshold so compact mode does NOT trigger
report_language: en              # this prompt gets an English report even though repo default is tr
backend: codex                   # run the lens analysis through Codex CLI instead of Claude Code's Agent tool
codex_model: gpt-5-codex         # optional — Codex model for this prompt; omit to use Codex's default
anchors:                         # always per-prompt — never inherited
  - input: "I am furious! Your product is garbage!"
    rubric: "de-escalates; remains professional"
  - input: "Can I get a refund 90 days after purchase?"
    expect_contains: ["policy"]
    rubric: "declines politely, cites the 30-day policy"
---
[your prompt body here]
```

Every field is optional. Most users only override `anchors` (per-prompt regression seeds) and let the repo defaults handle everything else.
