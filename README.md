# VoicePromptKit

**VoicePromptKit** is a Turkish-first Claude Code toolkit for engineering Vapi voice-agent prompts — three commands sharing one workflow: audit (`/prompt-check`), a live call simulator (`/prompt-chat`), and regression testing (`/prompt-test`).

`/prompt-check` audits your prompt the way a strict reviewer would: it finds rules that **contradict each other**, rules that **silently override others**, **gaps** the prompt itself raises but never resolves, **behavioural drift** between what the prompt asks and what the model actually does, and — for Turkish voice agents — **phonetic problems** that break text-to-speech.

You run it on any prompt file (system prompt, Claude Code subagent definition, Vapi voice script, chained workflow) and step through an interactive session: pick the lenses you want, review the findings in one summary table, then decide finding-by-finding which to apply, route to an overlay, dismiss, or discuss. Each run leaves behind a human-readable `report.md`, a structured `findings.json`, and a per-decision audit trail.

The original prompt file is **only modified when you explicitly apply a fix** (`düzelt`) — audits otherwise leave it untouched. Every run lives in its own numbered directory so you can compare audits across edits.

## Install

Run these two commands in Claude Code, in order. First add the marketplace:

```
/plugin marketplace add onurgoz-labs/VoicePromptKit
```

Then install the plugin:

```
/plugin install VoicePromptKit@onurgoz-labs
```

The plugin auto-loads in every Claude Code session after that. No API keys, no SDK installs — the optional drift lens runs inside a single Claude Code subagent.

**Optional:** to run the lens analysis through OpenAI's Codex CLI instead of Claude Code subagents, install [`codex`](https://github.com/openai/codex), run `codex login`, and set `backend: codex`. See [Codex CLI backend](#codex-cli-backend-v0100).

## Usage

```
/prompt-check path/to/your/prompt.md
```

VoicePromptKit opens an interactive session. First it asks which lenses you want to apply (multi-select: conflict, dominance, gap, drift, TR phonetic, schema — pre-checked based on your repo defaults). For `drift`, it asks `expand_count`. Then it dispatches the selected lenses as parallel subagents. After parallel lens dispatch completes, you see a summary table:

| id | mercek | önem | bölüm / satır | açıklama | düzeltme |
|---|---|---|---|---|---|
| C2 | çelişki | yüksek | Bölüm 5 / Satır 326 | R78 ile R80 sms_retry_count değerleri çelişiyor | R80'i max=1 yap VEYA R78'i max=2 yap |
| G5 | boşluk | orta | Bölüm 0.1 / Satır 7 | R4 "step instructions require it" tanımsız | R4'ü "verbatim scripts muaftır" diye netleştir |
| drift-S1 | davranışsal sapma | düşük | — / — | regression senaryosu geçti (0.93) | (geçti — düzeltme yok) |

_The same table with `report_language: "en"`:_

| id | lens | sev | section / line | rationale | fix |
|---|---|---|---|---|---|
| C2 | conflict | high | Section 5 / L326 | R78 and R80 disagree on sms_retry_count | Set R80 max=1 OR R78 max=2 |
| G5 | gap | medium | Section 0.1 / L7 | R4 "step instructions require it" is undefined | Clarify R4: "verbatim scripts are exempt" |
| drift-S1 | drift | low | — / — | regression scenario passed (0.93) | (passed — no fix) |

Set `report_language: "en"` in `.voicepromptkit.json` for English columns (`lens | sev | section / line | rationale | fix`).

The table is the primary output — both in `report.md` and Phase 9. Runners write rationale + fix directly in your chosen language (≤200 chars rationale, ≤150 chars fix — compact by design). No truncation; what you see is what the lens wrote.

Then it asks: **"Hangilerini ne yapayım?"** (English: *"What should I do with each?"*) You answer free-form:

```
C1, C3 düzelt; G2 yorum bırak; T1..T5 konuşalım; gerisini atla
```

_(English: "apply C1 and C3; overlay G2; let's discuss T1 through T5; skip the rest.")_

The grammar accepts Turkish and English keywords:

| Keyword | Meaning |
|---|---|
| `düzelt` / `apply` / `fix` | apply the suggestion to the prompt file |
| `yorum bırak` / `overlay` / `comment` | write the suggestion to `inline-suggestions.md` (prompt file untouched) |
| `atla` / `skip` / `dismiss` | log only; no action |
| `konuşalım` / `discuss` / `talk` | open per-finding sub-dialogue |
| `gerisini atla` / `gerisini yorum` / `gerisini düzelt` | wildcard for all unmarked findings |
| `iptal` / `cancel` | exit and leave session at pending (resume later with `/prompt-check-resume`) |

For findings you say "konuşalım" to, VoicePromptKit enters a per-finding dialogue: you see the full finding, then choose: accept the default suggestion / revise it yourself / route to overlay / dismiss. The dialogue terminates when every discussed finding has a final status.

**TR phonetic — split by category.** For `foreign_word` and `abbreviation` findings, `düzelt` is auto-routed to the overlay (pronunciation hints are voice-design decisions the author owns — a silent prompt edit can poison a Vapi / ElevenLabs script). For `number_readability` and `punctuation` findings, `düzelt` follows the normal apply flow and modifies the prompt — these are textual corrections like missing commas or malformed Turkish numbers.

Every decision lands in three places under `.voicepromptkit/<basename>/run-NNN/`:
- `session.json` — current snapshot (what's pending, what's applied, what's overlay, what's dismissed)
- `decisions.jsonl` — append-only audit log (every action ever taken, with timestamps)
- `inline-suggestions.md` — human-readable overlay of every finding routed to overlay

The original prompt file is **only** modified when you explicitly say `düzelt` on an apply-eligible finding — any non-TR finding, plus TR `number_readability` / `punctuation`. TR `foreign_word` / `abbreviation` always route to the overlay instead. Even then, a SHA256 stale-audit guard refuses to apply if the prompt was edited between audit and decision — re-run `/prompt-check <prompt>` to refresh.

Mid-session interruption is fine. Run `/prompt-check-resume` later and it re-enters the summary view filtered to findings with status: pending.

## What it looks for — the six lenses

| Lens | Looks for | Always on? |
|---|---|---|
| **Conflict** | Rules that logically contradict each other ("always formal" + "be casual and friendly") | yes |
| **Dominance** | Rules that silently override others through position, length, specificity, recency, or role-override patterns ("ignore previous instructions…") | yes |
| **Gap** | Undefined edge cases (incomplete conditionals) and ambiguous terms ("appropriate", "reasonable") that the prompt's own rules raise | yes |
| **Drift** | Behavioural mismatch between the prompt's stated rules and the model's actual output, surfaced by adversarial scenarios | only when anchors / conflicts / role-overrides exist (skipped otherwise) |
| **TR phonetic** | Numbers, abbreviations, foreign words, and pacing problems that break Turkish text-to-speech. Split by category: `foreign_word` + `abbreviation` routed to overlay (voice-design — prompt text never modified); `number_readability` + `punctuation` follow normal apply flow (`düzelt` modifies the prompt). | opt-in via `tr_phonetic: true` frontmatter or project config |
| **Schema** | Section numbering / ordering / heading consistency. Detects gaps (Section 5 → 7), out-of-order subsections (3.3 then 3.2), orphan subsections (5.1 under Section 4), inconsistent heading styles, missing parent sections, and STEP-numbering gaps. | only when the prompt has numbered section headings (auto-skipped on flat prompts) |

### TR phonetic — split by category

The TR lens splits its four detection categories into two routing buckets, so voice-design decisions stay overlay-only while textual corrections follow the normal apply flow.

- **`foreign_word` + `abbreviation`: advisory-only.** Pronunciation hints for `Peugeot → "pöjo"` or `DHL → "de-ha-el"` carry `fix_kind: "advisory"` and always land in the overlay file (`inline-suggestions.md`); the prompt text is never auto-edited, even on `düzelt`. The author hand-merges these into a TTS pronunciation guide block or the voice provider's config. A silent prompt edit (`DHL` becoming `de-ha-el` in the visible script) would corrupt the meaning, so this routing is non-negotiable.
- **`number_readability` + `punctuation`: normal apply flow.** Missing commas, malformed Turkish numbers, monetary spelling (`100 TL → yüz lira`) — these ARE textual fixes. They carry `fix_kind: "replace"`. When the user picks `düzelt` in the Phase 9 dialogue, Phase 10 modifies the prompt file just like a `conflict` or `gap` finding. The user can still route them to overlay via `yorum bırak` per-finding when they want to review by hand.
- **Migration note:** the TR routing rule from earlier versions was over-strict — it forced every TR finding to overlay regardless of category, even when the user explicitly said `düzelt` on a textual fix like a missing comma. v0.4.2 fixes this: only the voice-design categories (`foreign_word`, `abbreviation`) stay advisory.

The lens never translates: `pound → paund` is a phonetic hint; `pound → İngiliz lirası` is forbidden semantic substitution.

### Compact mode for long prompts

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

### Section-aware findings

For prompts that use numbered section headings (`## SECTION N` + `### N.M`), every finding carries a `section_ref` field pointing to its containing section and subsection. The report.md and inline-suggestions.md surfaces this as a section-aware header instead of the bare line number:

```
### Section 7.2 — L284 [C1 conflict severity=high, R3↔R8] — Tone contradiction...
```

Useful for long prompts (1500+ lines) where the user otherwise has to map line numbers to sections mentally. Findings outside any numbered section (preambles, flat prompts) show the bare line number with no section prefix.

The section index is built deterministically in Phase 3 of the audit (no LLM cost) and propagated to every lens runner via `inputs.section_index`. Schema lens, conflict, dominance, gap, and TR phonetic findings all attach `section_ref` automatically. Drift findings are behavioural and always carry `section_ref: null`.

All six lenses live in `skills/prompt-check/SKILL.md` and its `references/`.

## Two more skills — `/prompt-chat` + `/prompt-test`

Voice agent developers test prompts by **calling the bot**: trigger Vapi, listen to a turn, hang up, edit the prompt, call again. Slow (each test is a real call), expensive (per-minute Vapi billing), manual, and the lessons learned in one iteration are lost on the next.

VoicePromptKit adds two skills that move that loop into text:

- `/prompt-chat <prompt>` — opens an interactive call simulator in a new terminal window. The prompt is loaded as the simulated system prompt of a long-lived bare-Claude subprocess (`bin/prompt-chat-runner.py`); you converse with the persona turn by turn, bind prompt variables, and save interesting turns as test anchors.
- `/prompt-test <prompt>` — runs the saved anchors as regression tests. Each anchor becomes one scenario for the existing `drift-runner` (in `regression_only: true` mode), and you get a pass/fail table — one row per anchor.

They share one contract: a sidecar file `<prompt>.anchors.yaml` next to the prompt (v0.5.1+). `/prompt-chat` writes anchors via `/commit`; `/prompt-test` reads them. `/prompt-chat` also writes a second sidecar, `<prompt>.vars.yaml`, for variable bindings (v0.6.0 — see [Variables](#variables-v060)). The prompt file itself is never modified — its SHA stays stable across anchor and variable edits, so `/prompt-check` audit caches remain valid.

### `/prompt-chat` — converse, save, commit

```
/prompt-chat path/to/prompt.md
```

Phase 0 sets up `.voicepromptkit/<basename>/chat-NNN/` and detects prompt variables; Phase 0.5 binds any unbound ones; Phase 1 spawns `bin/prompt-chat-runner.py` in a fresh terminal window (`osascript` on macOS, `tmux` / `gnome-terminal` / `xterm` / `konsole` on Linux, `wt.exe` / `cmd` on Windows) and the skill exits. From there the runner owns the welcome screen and the chat loop.

Every user message goes to the long-lived bare-Claude subprocess, which produces one assistant turn faithful to the prompt's persona. The exchange is appended to `chat.jsonl` (append-only, atomic per turn). Slash commands while you chat:

| Command | Action |
|---|---|
| `/save` | Stage a test anchor. Asks which kind: **single turn** (the last user→assistant exchange) or **flow** (the whole conversation as a multi-turn flow anchor — the opening greeting is dropped, each bot turn gets its own assertions, and the last turn can be marked `end_call_expect`). Collects `expect_contains` / `expect_not_contains` / `rubric`, then stages in `saved_anchors.json` (written to the sidecar on `/commit`). |
| `/history` | Pretty-print the conversation so far. |
| `/reset` | Move `chat.jsonl` aside and start fresh. `saved_anchors.json` is untouched. |
| `/silence <N>` | Simulate the caller saying nothing for N seconds — sends `[silence for N seconds]` so the persona applies the script's silence policy. |
| `/vars` | **(v0.6.0)** List detected variables with their current bound value (or `(random)` when unbound). |
| `/set name=value` | **(v0.6.0)** Rebind a variable mid-chat. Persists to `<prompt>.vars.yaml` + applies on the next turn via a `[SYSTEM: variable update]` cue (no respawn → history preserved). |
| `/unset name` | **(v0.6.0)** Drop a binding so the persona invents the value again. |
| `/commit` | Atomic-write the staged anchors into `<prompt>.anchors.yaml` (creates the sidecar if missing; refuses if schema_version != 1). Validation re-parses the temp file post-write; rollback on any failure. Archived to `committed-<UTC>.json`. |
| `/help` | Reprint the command list. |
| `/quit` | Final summary. Offers to commit, keep, or discard any uncommitted staged anchors before exit. |

**Modern terminal UI.** When [Rich](https://github.com/Textualize/rich) is installed (`pip install rich`), the chat renders as a modern surface: Markdown-formatted bot replies in bordered panels, right-aligned user bubbles, live token-by-token streaming, a responsive layout, and truecolor with automatic terminal detection (including Windows). Without Rich it falls back to a clean stdlib ANSI renderer — no extra dependency required. Set `VOICEPROMPTKIT_NO_RICH=1` to force the fallback, or the standard `NO_COLOR=1` for plain text.

### `/prompt-test` — replay anchors, report pass/fail

```
/prompt-test path/to/prompt.md
```

Reads `<prompt>.anchors.yaml` (falls back to `frontmatter.anchors[]` with a deprecation warning if the sidecar is missing). Dispatches `drift-runner` with `regression_only: true`. The runner skips every probe template except regression, ignores `expand_count`, accepts null static-lens inputs. Each anchor becomes one scenario; flow anchors expand into multi-turn `flow_regression` scenarios with per-step assertions.

Output goes to `.voicepromptkit/<basename>/test-NNN/drift.json`. The skill renders a markdown table:

```
VoicePromptKit test — test-001

| id | tür  | input / name                | geçti | puan | sebepler |
|----|------|-----------------------------|-------|------|----------|
| S1 | tek  | Merhaba                     | ✅    | 1.00 | mekanik contains 'Alex' geçti; ... |
| S2 | akış | silence + recovery + booking| ❌    | 0.67 | step 4 (end_call_expect): assistant did not close politely |

Toplam: 2 anchor, 1 geçti, 1 kaldı.
```

_Column legend (TR → EN): `tür` → kind, `geçti` → passed, `puan` → score, `sebepler` → reasons, `Toplam` → Total. Set `report_language: "en"` for an English table._

If any anchor fails, the skill offers a follow-up: see verdict details (full `step_verdicts[]` for flow scenarios), investigate the failing turn in `/prompt-chat`, audit with `/prompt-check`, or close. Commands are printed but not auto-dispatched — you run them yourself.

### Anchor schema (v0.5.1)

Sidecar file: `<prompt>.anchors.yaml` (e.g. `mybot.md` → `mybot.md.anchors.yaml`). Single source of truth — the prompt file itself stays untouched.

```yaml
schema_version: 1

anchors:
  # Single-turn anchor — direct user input, no prior conversation.
  - input: "Merhaba"
    expect_contains: ["Alex"]
    expect_not_contains: ["tekrar bağlanacağım"]
    rubric: "Bot identifies itself and states the call's purpose"

  # Single-turn anchor with prior context — drift-runner replays the conversation
  # before sending `input` as the next user turn. Closes the state-machine gap
  # for tests that only make sense mid-flow.
  - input: "İptal etmek istiyorum"
    context:
      - role: assistant
        content: "Merhaba, ben Alex. Rezervasyonunuzu teyit etmek için arıyorum..."
      - role: user
        content: "Tamam dinliyorum"
    expect_not_contains: ["tekrar bağlanacağım"]
    rubric: "Persona handles cancellation per prompt policy — no false handoff"

  # Flow anchor — full conversation script with per-turn assertions, silence
  # handling, and explicit call closure. This is the recommended shape for
  # voice-agent state machines.
  - kind: flow
    name: "silence + recovery + booking"
    turns:
      - kind: silence_input      # sugar — expands to user_input with "[silence for 6 seconds]"
        duration_seconds: 6
      - kind: assistant_expect
        rubric: "asks an open question OR politely confirms caller is still there"
      - kind: user_input
        content: "Pardon, hattım dondu — bir rezervasyon yapacaktım"
      - kind: assistant_expect
        rubric: "acknowledges + proceeds to gather party size / date / time"
      - kind: end_call_expect
        rubric: "polite close once booking confirmed"
```

**Step kinds inside `turns[]`:**

- `user_input` — free-form user content. Use `"[silence for N seconds]"` for silence, OR use the `silence_input` sugar below.
- `silence_input` — `kind: silence_input` + `duration_seconds: <int>`. Expands at runtime to `user_input` with content `"[silence for N seconds]"`. Authoring convenience only — semantically identical to the opaque string.
- `assistant_expect` — assertions for the assistant turn that follows the immediately preceding `user_input`. Carries `expect_contains[]` / `expect_not_contains[]` / `rubric` (at least one required). NO `content` field — the assistant content comes from the simulator.
- `end_call_expect` — terminal step. Same assertion fields as `assistant_expect`, plus an implicit "session is closed by the assistant" rubric. The simulator stops here; no further turns processed.

Turns must alternate `user_input` (or `silence_input`) → `assistant_expect` → … → `end_call_expect`. The reader drops any anchor that violates alternation, with a warning.

**Backward compatibility.** If `<prompt>.anchors.yaml` is missing but the prompt's frontmatter has an `anchors:` block, the legacy v0.5.0 path is used with a `"frontmatter.anchors is deprecated — migrate to <prompt>.anchors.yaml"` warning. Move the block over by hand — the sidecar is the supported location going forward.

### Variables (v0.6.0)

Voice-agent scripts carry placeholder tokens that production Vapi fills from `assistantOverrides.variableValues` before a call connects. `/prompt-chat` mirrors this with an explicit **detect → bind → inject** layer, so caller data is a reproducible constant you control instead of something the persona invents at random.

**Token forms detected:** Vapi mustache `{{ name }}` / `{{ customer.name }}` and bracketed upper-snake `[MÜŞTERİ_ADI]` (Turkish uppercase included). Harness-control brackets (`[SYSTEM: …]`, `[PERSONA_NAME: …]`, `[silence …]`) are never treated as variables.

**Pre-chat binding.** When you launch `/prompt-chat`, Phase 0 detects the tokens and — for any not already bound — asks you for a value (with inferred sample options, or type your own; pick "random" to leave it to the model). Bound values are substituted into the system prompt before the call opens; unbound tokens are left for the persona to invent.

**Storage — sidecar `<prompt>.vars.yaml`** (e.g. `mybot.md` → `mybot.md.vars.yaml`). Prompt-level and persistent across chats; the prompt file itself is never touched, so its SHA256 stays stable and cached `/prompt-check` audits remain valid — the same contract as the anchors sidecar.

```yaml
schema_version: 1
variables:
  musteri_adi: "Zeynep Kaya"
  randevu_tarihi: "12 Haziran 14:00"
```

An author-provided `chat_variables:` block in the prompt's frontmatter is read as a **seed only** (never written back); the sidecar wins on conflict.

**Mid-chat editing.** `/vars` shows the current bindings; `/set name=value` and `/unset name` change them live — persisted to the sidecar and applied on the next turn via a `[SYSTEM: variable update]` cue, with no subprocess respawn so the conversation history is preserved. `/reset` re-substitutes the current values into a fresh call.

### Workflow loop

```
1. /prompt-chat your-prompt.md
2. Converse with the persona until something interesting happens.
3. /save — stage an anchor: a single turn, or the whole conversation as a flow.
4. (repeat 2-3 for more scenarios)
5. /commit — write staged anchors to <prompt>.anchors.yaml.
6. Edit the prompt body. (Prompt SHA stays stable; cached audits stay valid.)
7. /prompt-test your-prompt.md — does the persona still behave?
8. If anchors fail, /prompt-chat again to investigate, OR /prompt-check to audit for new conflicts.
```

The bridge is `<prompt>.anchors.yaml`. Stay on text, iterate fast, only call Vapi when you're confident.

### Cost controls (v0.5.2 → v0.5.6)

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

  | knob | default | scope |
  |---|---|---|
  | `target_model` | `claude-opus-4-7` | drift Step 2 simulation (regression — production fidelity) |
  | `worker_model` | `claude-haiku-4-5-20251001` | static-lens × 4, tr-phonetic, drift Step 1 + 3 |
  | `judge_model` | `claude-haiku-4-5-20251001` | drift Step 3 rubric eval (separately tunable, cross-provider OK) |
  | `chat_model` | `claude-haiku-4-5-20251001` | `prompt-chat-runner` persona dispatches (exploration — fast + cheap) |

## Output layout

Every run gets its own directory. Older runs are preserved so you can diff audits across prompt edits.

```
.voicepromptkit/
└── <prompt-basename>/
    ├── run-001/
    │   ├── frontmatter.json
    │   ├── body.txt
    │   ├── rules.json
    │   ├── conflicts.json     (if conflict lens selected)
    │   ├── dominances.json    (if dominance lens selected)
    │   ├── gaps.json          (if gap lens selected)
    │   ├── schema.json        (if schema lens selected, with applicability flag)
    │   ├── drift.json         (if drift lens selected, or skip reason)
    │   ├── tr_phonetic.json   (if TR lens selected)
    │   ├── findings.json
    │   ├── report.md
    │   ├── session.json            ← NEW: current decision snapshot
    │   ├── decisions.jsonl         ← NEW: append-only audit log
    │   └── inline-suggestions.md   ← NEW: overlay (re-rendered each pass)
    ├── run-002/
    ├── run-003/
    └── latest -> run-003/
```

### `report.md` — what humans read

```markdown
# VoicePromptKit Report — your-prompt

- **Prompt:** `/abs/path/your-prompt.md`
- **Run:** `run-003`
- **Generated:** 2026-05-17T19:42:00Z
- **Target model:** claude-opus-4-7

## Summary

| Lens | Total | High | Medium | Low |
|---|---|---|---|---|
| Conflict | 10 | 4 | 4 | 2 |
| Dominance | 7 | 3 | 3 | 1 |
| Gap | 13 | 5 | 6 | 2 |
| Drift | 10 scenarios: 5✓ / 5✗ | — | — | — |
| TR phonetic | 8 | 2 | 5 | 1 |

## Findings

### Conflicts
- **L15** [C1 severity=high, R1↔R2] — Tone contradiction: "always formal" vs "be casual and friendly".
  - **Current:** `Always be formal and use professional language at all times.`
  - **Fix:** `Maintain a professional but warm register.`
…
```

### `findings.json` — what the interactive flow reads

```json
{
  "prompt_path": "/abs/path/your-prompt.md",
  "prompt_sha256": "a3f1...c7",
  "run_id": "run-003",
  "generated_at": "2026-05-17T19:42:00Z",
  "summary": { "rules": 50, "conflicts": {"total": 10, "high": 4}, ... },
  "findings": [
    {
      "id": "C1",
      "lens": "conflict",
      "fix_kind": "replace",
      "severity": "high",
      "line": 15,
      "related_lines": [15, 16],
      "current_excerpt": "Always be formal and use professional language at all times.",
      "suggested_fix": "Maintain a professional but warm register.",
      "pronunciation_entry": null,
      "rationale": "Tone contradiction with R2 on line 16.",
      "rule_ids": ["R1","R2"]
    },
    {
      "id": "T3",
      "lens": "tr_phonetic",
      "fix_kind": "advisory",
      "severity": "high",
      "line": 858,
      "current_excerpt": "şehir dışı gönderilerde ise DHL kullanıyoruz.",
      "suggested_fix": "",
      "pronunciation_entry": {
        "term": "DHL",
        "strategy": "pronounce",
        "phonetic": "de-ha-el",
        "alt_translation": null,
        "note": null
      },
      "rationale": "TTS reads DHL as English D-H-L.",
      "rule_ids": []
    }
  ],
  "pronunciation_map": [
    { "term": "DHL", "strategy": "pronounce", "phonetic": "de-ha-el", "alt_translation": null, "note": null, "source": "finding", "source_finding_ids": ["T3"] }
  ]
}
```

Each finding declares one of two `fix_kind` values:

- `replace` — substring replacement (`current_excerpt` → `suggested_fix`). Emitted by `conflict`, `dominance`, `gap`, `drift` lenses, and TR phonetic findings with `kind: "number_readability"` or `kind: "punctuation"`.
- `advisory` — reported only, never auto-applied. Emitted by TR phonetic findings with `kind: "foreign_word"` or `kind: "abbreviation"` (the `pronunciation_entry` lands in the overlay for the author to hand-merge into a TTS pronunciation guide).
- `schema` lens emits `fix_kind: "replace"` for every finding (just like conflict / dominance / gap). Most schema findings have `fix_strategy: "structural"` (insert/renumber/reorder requires the Edit tool); `heading_style_inconsistent` is `fix_strategy: "substring"` (clean text replacement of one heading).

After the summary, you make per-finding decisions in free-form text (see Usage above). The skill carries out each decision in Phase 10.

## Customizing defaults

The plugin's behaviour is layered, highest priority first:

1. **Per-prompt frontmatter** — overrides everything for one prompt.
2. **Environment variables** (`VOICEPROMPTKIT_*`) — change defaults for every prompt in your shell / Claude Code session.
3. **Project config** (`.voicepromptkit.json` at repo root) — repo-level defaults shared with your team.
4. **Built-in defaults** — applied when none of the above is set.

### First-run wizard

The first time you run `/prompt-check` in a repo, the skill walks you through a 7-question wizard and saves the answers to `<repo-root>/.voicepromptkit.json`. Subsequent runs read that file silently. Edit it by hand to change defaults, or delete it to rerun the wizard.

The wizard asks:

1. **Default prompt type** for this repo (`system | agent | vapi | task | chain | unspecified`). Used when a prompt has no `type:` in its frontmatter.
2. **Turkish phonetic lens** active by default? — recommended `true` if you picked `vapi` above, otherwise `false`.
3. **Target model** for reports + drift simulation (`claude-opus-4-7` default, free text accepted).
4. **Output formats** — multi-select from `markdown`, `findings_json`, `json`.
5. **Drift `expand_count`** — how many extra adversarial scenarios beyond the anchor + conflict budget. `0` disables the drift lens entirely.
6. **Max prompt character limit** — repo-level threshold for triggering compact mode. Default `50000`. `0` disables compact mode entirely. Useful when your prompts routinely exceed 50K chars and you want every audit to run at full depth regardless of size.
7. **Report language** — controls the language of report.md, terminal summary, and Phase 9 dialog prompts. Options: `tr` (Türkçe, default) or `en` (English). Lens-generated content (rationale, suggested_fix, current_excerpt) stays in whatever language the runner produced; only skill-side templates translate.

### Project config (`.voicepromptkit.json`)

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

### Environment variables (session-wide overrides)

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
| `VOICEPROMPTKIT_BACKEND` | `claude` (default) or `codex`. Selects the engine that runs the lens analysis (Phases 4–6). See [Codex CLI backend](#codex-cli-backend-v0100). | project config → `claude` |
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

### Per-prompt frontmatter

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

### Codex CLI backend (v0.10.0)

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

## Pipeline

The plugin runs one orchestrating skill that fans out to three subagents for the lens work. `static-lens-runner` is dispatched on every run; `drift-runner` and `tr-phonetic-runner` are conditional (drift only when anchors / conflicts / role-overrides exist AND `expand_count > 0`; TR only when `tr_phonetic: true`).

**Parallel topology:** Phase 4 fans out FIVE concurrent `Agent` calls in a single assistant turn: conflict, dominance, gap, schema (each via static-lens-runner with `selected_lenses` singleton), and tr-phonetic (via tr-phonetic-runner). Phase 5 (drift) is downstream of conflict + dominance + gap — drift-runner reads those three artefacts as inputs, so it starts as soon as those three land (doesn't wait for schema or tr-phonetic). Phase 7 waits for all six lens outputs before rendering.

Phase 9 and Phase 10 are the interactive layer — they run automatically after Phase 8 in `/prompt-check` and re-enter from `/prompt-check-resume`.

1. **Phase 0** — First-run wizard or load existing `.voicepromptkit.json`.
2. **Phase 1** — Allocate a fresh `run-NNN` directory (atomic; `latest` symlink is updated only on success).
3. **Phase 2** — Parse frontmatter deterministically and split body. Stores `body_line_offset`, `prompt_sha256`, `body_char_count`, and `compact_mode` (true when body_char_count > max_char_limit AND max_char_limit > 0). Phase 4-6 dispatches propagate these to each runner.
4. **Phase 3** — Extract atomic, line-anchored rules from `body.txt`.
   - **Phase 3.5 — Per-run lens-selection wizard.** VoicePromptKit emits an `AskUserQuestion` widget asking which of the six lenses to apply. Repo defaults from `.voicepromptkit.json` seed which options are pre-checked, but the question itself is MANDATORY — prose substitutes are a contract violation. If the user is in a headless context where AskUserQuestion is unavailable, the audit aborts with a clear error rather than proceeding silently.
5. **Phase 4 — Parallel lens dispatch (5 concurrent Agent calls).** Emits five Agent calls in one turn:
   - `static-lens-runner` × 4 (conflict / dominance / gap / schema, each with singleton `selected_lenses`)
   - `tr-phonetic-runner` × 1 (conditional on user_intent.tr_phonetic_enabled)
   The skill awaits all five before proceeding. Schema lens auto-skips on flat prompts with no numbered headings.
6. **Phase 5 — Drift (downstream of static lenses).** Triggered as soon as conflicts.json + gaps.json + dominances.json land. Runs in parallel with schema and tr-phonetic if those are still working. Conditional: skipped when expand_count == 0 or no anchors/conflicts/role-overrides.
7. **Phase 7 — Render.** Awaits all six lens outputs. Builds findings.json + report.md (line numbers translated back to the original prompt file; `prompt_sha256` carried through). Phase 7 renders findings as a markdown TABLE with columns `id | mercek | önem | bölüm / satır | açıklama | düzeltme` (TR) or `id | lens | sev | section / line | rationale | fix` (EN). Runners self-cap rationale at ≤200 chars and fix at ≤150 chars — render uses full text verbatim, no truncation.
8. **Phase 8** — Update `latest` symlink (commit point), print terminal summary.
9. **Phase 9** — Render summary table from `findings.json`. Bootstrap `session.json` (all findings start `pending`). Accept free-form decision string from the user, parse it, apply TR routing rule, append each decision to `decisions.jsonl`.
10. **Phase 10** — Process decisions: dismissed (log only), overlay (rebuild `inline-suggestions.md`), applied (SHA256-guarded prompt edits, with auto-conversion to overlay on stale audit or ambiguous occurrences), discussed (per-finding sub-dialogue with accept / revise / overlay / dismiss). Re-render `session.json` snapshot. Print Phase 10 summary. Phase 10's konuşalım sub-flow (per-finding deep dialogue) MANDATES `AskUserQuestion` for the four-option choice (kabul / revize / overlay / atla). Free-text follow-ups (revised suggestion text) use plain conversational input — that's intentional. The four-option choice itself is always AskUserQuestion.

## Architecture

The plugin is pure Claude Code skill orchestration. No Node, no TypeScript, no `npm install`, no `node_modules`, no `package.json` — four skill files, a handful of references, two commands, three subagent definitions (one always dispatched, two conditional), and three Python helper scripts.

```
.claude-plugin/
├── plugin.json
└── marketplace.json
skills/
├── prompt-check/
│   ├── SKILL.md
│   └── references/
│       ├── lens-rules.md        (entry point for the static-lens criteria)
│       ├── lens-rules/          (per-lens criteria: _shared, conflict, dominance, gap, schema)
│       ├── tr-phonetic.md       (Turkish TTS rules)
│       ├── probes.md            (drift probe templates)
│       ├── dialog-flow.md       (Phase 9 templates + decision grammar)
│       └── overlay-format.md    (inline-suggestions.md + decisions.jsonl spec)
├── prompt-chat/SKILL.md         (live call simulator — bootstrap)
├── prompt-chat-session/SKILL.md (chat session entry — execs the Python runner)
└── prompt-test/SKILL.md         (anchor regression runner)
commands/
├── prompt-check.md
└── prompt-check-resume.md
agents/
├── drift-runner.md              (conditional — adversarial scenarios + judging)
├── static-lens-runner.md        (always dispatched — conflict + dominance + gap + schema)
└── tr-phonetic-runner.md        (conditional — advisory-only TR lens)
bin/
├── prompt-chat-runner.py        (long-lived chat REPL orchestrator)
├── read-anchors.py              (anchor sidecar reader / validator)
└── codex-lens.py                (Codex CLI backend dispatcher — backend: codex)
examples/
├── sample-system.md
├── sample-agent.md
└── sample-vapi.md               (sets tr_phonetic: true)
```

All cross-phase state is exchanged via JSON files under `.voicepromptkit/<basename>/run-NNN/`. The orchestrating skill reads its own writes; each subagent reads paths it is given and writes only the JSON artefacts assigned to it.

## License

MIT — see [LICENSE](./LICENSE) for the full text.
