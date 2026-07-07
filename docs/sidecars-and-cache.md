# Sidecars, run artefacts, and cache

`/prompt-chat` and `/prompt-test` share one contract: a sidecar file `<prompt>.anchors.yaml` next to the prompt (v0.5.1+). `/prompt-chat` writes anchors via `/commit`; `/prompt-test` reads them. `/prompt-chat` also writes a second sidecar, `<prompt>.vars.yaml`, for variable bindings (v0.6.0 — see [Variables](#variables-v060)). The prompt file itself is never modified — its SHA stays stable across anchor and variable edits, so `/prompt-check` audit caches remain valid.

## Anchor schema (v0.5.1)

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

## Variables (v0.6.0)

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

## `/prompt-chat` in depth — how the sidecars get written

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

## `/prompt-test` in depth — how the sidecars get read

Reads `<prompt>.anchors.yaml` (falls back to `frontmatter.anchors[]` with a deprecation warning if the sidecar is missing). Dispatches `drift-runner` with `regression_only: true`. The runner skips every probe template except regression, ignores `expand_count`, accepts null static-lens inputs. Each anchor becomes one scenario; flow anchors expand into multi-turn `flow_regression` scenarios with per-step assertions.

Output goes to `.voicepromptkit/<basename>/test-NNN/drift.json`, rendered as a pass/fail markdown table (see the README for a sample).

If any anchor fails, the skill offers a follow-up: see verdict details (full `step_verdicts[]` for flow scenarios), investigate the failing turn in `/prompt-chat`, audit with `/prompt-check`, or close. Commands are printed but not auto-dispatched — you run them yourself.

## Output layout — run directories

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
    │   ├── session.json            ← current decision snapshot
    │   ├── decisions.jsonl         ← append-only audit log
    │   └── inline-suggestions.md   ← overlay (re-rendered each pass)
    ├── run-002/
    ├── run-003/
    ├── latest.txt          ← pointer file: holds "run-003" (cross-platform)
    └── latest -> run-003/  ← symlink, POSIX only (best-effort, back-compat)
```

The `latest` pointer is **cross-platform**: `latest.txt` (a one-line file holding the newest successful run's name) works on every OS, including Windows / Git Bash where symlinks need elevated privileges. On POSIX a `latest` symlink is also created best-effort so `latest/<file>` paths keep working. Readers resolve `latest.txt` first, then fall back to the symlink.

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

After the summary, you make per-finding decisions in free-form text (see the decision grammar in the README). The skill carries out each decision in Phase 10.

## Decision artefacts, stale-audit guard, and resume

Every decision lands in three places under `.voicepromptkit/<basename>/run-NNN/`:
- `session.json` — current snapshot (what's pending, what's applied, what's overlay, what's dismissed)
- `decisions.jsonl` — append-only audit log (every action ever taken, with timestamps)
- `inline-suggestions.md` — human-readable overlay of every finding routed to overlay

The original prompt file is **only** modified when you explicitly say `düzelt` on an apply-eligible finding — any non-TR finding, plus TR `number_readability` / `punctuation`. TR `foreign_word` / `abbreviation` always route to the overlay instead. Even then, a SHA256 stale-audit guard refuses to apply if the prompt was edited between audit and decision — re-run `/prompt-check <prompt>` to refresh.

Mid-session interruption is fine. Run `/prompt-check-resume` later and it re-enters the summary view filtered to findings with status: pending.

## Content-addressable cache

`/prompt-check` results are cached content-addressably: same prompt body + same config ⇒ same lens output can be reused instead of re-dispatching runners. Because the anchors and variables sidecars live next to the prompt instead of inside it, editing them never changes the prompt's SHA — cached `/prompt-check` audits remain valid across anchor and variable edits. And because `backend` is part of the resolved frontmatter that seeds the cache key, Claude-backend and Codex-backend runs cache independently — switching backends never serves a stale cross-engine artefact (see [Codex CLI backend](codex-backend.md)).
