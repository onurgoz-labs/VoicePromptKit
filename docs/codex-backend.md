# Codex CLI backend (v0.10.0)

By default `/prompt-check` runs its lens analysis (Phases 4–6) through Claude Code's `Agent` tool. Set `backend: codex` to run **the same runners through OpenAI's [Codex CLI](https://github.com/openai/codex)** (`codex exec`) instead — useful when you want the background analysis driven by Codex.

Enable it in any layer:

```yaml
# frontmatter (one prompt)        OR   .voicepromptkit.json (repo)       OR   shell / settings.json
backend: codex                          { "backend": "codex" }                VOICEPROMPTKIT_BACKEND=codex
codex_model: gpt-5-codex                { "codex_model": "gpt-5-codex" }      VOICEPROMPTKIT_CODEX_MODEL=gpt-5-codex
```

**Prerequisite:** `codex` must be on your PATH and authenticated (`codex login` once). If `backend=codex` but Codex is not found, the run aborts with a clear error pointing you back to `backend: claude`.

**How it works.** `bin/codex-lens.py` is a thin, deterministic dispatcher. For each lens it would otherwise hand to a subagent, it assembles the runner spec (`agents/<runner>.md`) plus the exact same JSON payload (`{inputs, output_paths}`) the `Agent` call would carry, pipes it to `codex exec` on stdin (`--sandbox workspace-write --cd <repo>`), and verifies the runner wrote valid JSON. **The on-disk artefacts are byte-for-byte the same contract** — `report.md`, `findings.json`, and the interactive review (Phases 9–10) are identical regardless of backend. The interactive review always runs inside Claude Code.

**What differs on Codex:**
- **Model semantics.** `target_model` / `worker_model` / `judge_model` are Claude IDs and do not apply. On Codex the runner's in-context model (the "model under test" for the drift lens) is Codex's own model — set `codex_model` to pick it, or let Codex use its configured default.
- **Sequential, not concurrent.** The five-Agents-in-one-turn fan-out is Claude-only; on Codex the lenses run as sequential `codex exec` subprocesses, so wall-clock is longer. The Phase 8 summary states this so a slow run is not mistaken for a hang.
- **Caching.** `backend` is part of the resolved frontmatter that seeds the content-addressable cache key, so Claude-backend and Codex-backend runs cache independently — switching backends never serves a stale cross-engine artefact.
- **Failure handling.** If a Codex lens fails (missing CLI, non-zero exit, or missing/invalid output), the skill writes the lens's empty placeholder plus a warning and continues, so Phase 7 still renders and the failure surfaces in the summary.

**Escape hatches:** `VOICEPROMPTKIT_CODEX_CLI` points at a non-PATH `codex` binary; `VOICEPROMPTKIT_CODEX_EXEC_FLAGS` appends raw flags to every `codex exec` invocation.

## Running with Claude vs Codex — side by side

You always invoke the same command — `/prompt-check path/to/prompt.md` — from inside Claude Code. The only thing that changes is which engine runs the lens analysis. Both produce the same `report.md` / `findings.json` and the same interactive review.

**Run with Claude (default — nothing to configure):**

```
/prompt-check prompts/agent.md
```

The lenses run as Claude Code subagents (`Agent` tool), fanned out in parallel. This is the zero-setup path; if you never set `backend`, this is what you get.

**Run with Codex (opt in):**

1. Install + authenticate Codex once:
   ```bash
   # macOS
   brew install codex          # or: npm i -g @openai/codex
   codex login                 # one-time auth
   codex --version             # sanity check
   ```
2. Turn the backend on, in whichever scope fits:
   ```bash
   # A) one shell / session — set before launching Claude Code
   export VOICEPROMPTKIT_BACKEND=codex
   export VOICEPROMPTKIT_CODEX_MODEL=gpt-5-codex   # optional; omit for Codex's default

   # B) whole repo — commit to .voicepromptkit.json
   #    { "backend": "codex", "codex_model": "gpt-5-codex" }

   # C) one prompt — add to that prompt's frontmatter
   #    backend: codex
   ```
3. Run the same command:
   ```
   /prompt-check prompts/agent.md
   ```
   The lenses now run as sequential `codex exec` subprocesses via `bin/codex-lens.py`.

**Switching back to Claude** is just removing the override (unset the env var, delete the config key, or drop the frontmatter line) — or set `backend: claude` explicitly. Because `backend` is part of the cache key, the two engines never serve each other's cached results.

| | Claude backend | Codex backend |
|---|---|---|
| Setup | none | install + `codex login`, set `backend: codex` |
| Engine | Claude Code `Agent` subagents | `codex exec` via `bin/codex-lens.py` |
| Concurrency | parallel fan-out (one turn) | sequential (one subprocess per lens) |
| Model under test (drift) | `target_model` (Claude) | Codex's model (`codex_model`) |
| Artefacts | `report.md` + `findings.json` | identical |
| Interactive review | in Claude Code | in Claude Code (always) |
