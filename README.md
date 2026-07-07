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

**Optional:** to run the lens analysis through OpenAI's Codex CLI instead of Claude Code subagents, install [`codex`](https://github.com/openai/codex), run `codex login`, and set `backend: codex`. See [docs/codex-backend.md](docs/codex-backend.md).

## The three commands

### `/prompt-check` — audit a prompt

```
/prompt-check path/to/your/prompt.md
```

VoicePromptKit opens an interactive session: it asks which lenses to apply (pre-checked from your repo defaults), dispatches them as parallel subagents, then shows a summary table:

| id | mercek | önem | bölüm / satır | açıklama | düzeltme |
|---|---|---|---|---|---|
| C2 | çelişki | yüksek | Bölüm 5 / Satır 326 | R78 ile R80 sms_retry_count değerleri çelişiyor | R80'i max=1 yap VEYA R78'i max=2 yap |
| G5 | boşluk | orta | Bölüm 0.1 / Satır 7 | R4 "step instructions require it" tanımsız | R4'ü "verbatim scripts muaftır" diye netleştir |

Set `report_language: "en"` in `.voicepromptkit.json` for English columns (`lens | sev | section / line | rationale | fix`).

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

Good to know, in one line each:

- The prompt file is **only** modified when you explicitly say `düzelt` on an apply-eligible finding, and a SHA256 stale-audit guard blocks applies if the prompt changed since the audit — see [docs/sidecars-and-cache.md](docs/sidecars-and-cache.md).
- Mid-session interruption is fine — `/prompt-check-resume` re-enters the summary view for pending findings.
- TR phonetic findings are split by category: pronunciation hints go to an overlay file, textual fixes can edit the prompt — see [docs/lenses.md](docs/lenses.md).
- Prompts over 50K chars automatically enter a cheaper **compact mode** — see [docs/cost-tuning.md](docs/cost-tuning.md).
- The first run in a repo launches a 7-question wizard that saves defaults to `.voicepromptkit.json` — see [docs/configuration.md](docs/configuration.md).

### `/prompt-chat` — converse with the persona

```
/prompt-chat path/to/prompt.md
```

Opens an interactive call simulator in a new terminal window: the prompt is loaded as the simulated system prompt of a long-lived bare-Claude subprocess, and you converse with its persona turn by turn — no Vapi call, no per-minute billing. While chatting, `/save` stages an interesting exchange as a test anchor (single turn or whole-conversation flow), `/commit` writes staged anchors to the `<prompt>.anchors.yaml` sidecar, `/vars` + `/set` bind prompt variables, and `/help` lists everything else. The prompt file itself is never touched.

Full in-chat command table, variable binding, and the anchor/vars sidecar schemas: [docs/sidecars-and-cache.md](docs/sidecars-and-cache.md).

### `/prompt-test` — replay anchors, report pass/fail

```
/prompt-test path/to/prompt.md
```

Runs every anchor saved in `<prompt>.anchors.yaml` as a regression scenario and renders a pass/fail table:

```
VoicePromptKit test — test-001

| id | tür  | input / name                | geçti | puan | sebepler |
|----|------|-----------------------------|-------|------|----------|
| S1 | tek  | Merhaba                     | ✅    | 1.00 | mekanik contains 'Alex' geçti; ... |
| S2 | akış | silence + recovery + booking| ❌    | 0.67 | step 4 (end_call_expect): assistant did not close politely |

Toplam: 2 anchor, 1 geçti, 1 kaldı.
```

_Column legend (TR → EN): `tür` → kind, `geçti` → passed, `puan` → score, `sebepler` → reasons, `Toplam` → Total. Set `report_language: "en"` for an English table._

If an anchor fails, the skill offers follow-ups: verdict details, investigate in `/prompt-chat`, or audit with `/prompt-check`. Mechanics and output artefacts: [docs/sidecars-and-cache.md](docs/sidecars-and-cache.md).

## The workflow loop

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

## The six lenses at a glance

| Lens | Looks for | Always on? |
|---|---|---|
| **Conflict** | Rules that logically contradict each other | yes |
| **Dominance** | Rules that silently override others (position, recency, "ignore previous instructions…") | yes |
| **Gap** | Undefined edge cases and ambiguous terms the prompt's own rules raise | yes |
| **Drift** | Behavioural mismatch between stated rules and actual model output | only when anchors / conflicts / role-overrides exist |
| **TR phonetic** | Numbers, abbreviations, foreign words, and pacing that break Turkish TTS | opt-in via `tr_phonetic: true` |
| **Schema** | Section numbering / ordering / heading consistency | only when the prompt has numbered headings |

Full lens semantics, TR routing rules, and section-aware findings: [docs/lenses.md](docs/lenses.md).

## Documentation

| Page | Covers |
|---|---|
| [docs/lenses.md](docs/lenses.md) | Detailed lens semantics, TR phonetic category routing, section-aware findings |
| [docs/cost-tuning.md](docs/cost-tuning.md) | `target_model` / `worker_model` / `judge_model` / `chat_model` knobs, compact mode, cost-controls version history (v0.5.2 → v0.5.6) |
| [docs/codex-backend.md](docs/codex-backend.md) | Codex CLI backend (v0.10.0), running with Claude vs Codex side by side |
| [docs/sidecars-and-cache.md](docs/sidecars-and-cache.md) | `anchors.yaml` / `vars.yaml` schemas, in-chat commands, run-directory layout, `report.md` / `findings.json` formats, decision artefacts, stale-audit guard, resume, content-addressable cache |
| [docs/configuration.md](docs/configuration.md) | First-run wizard, `.voicepromptkit.json` keys, environment variables, per-prompt frontmatter overrides |
| [docs/architecture.md](docs/architecture.md) | Audit pipeline phases, repository layout |

## License

MIT — see [LICENSE](./LICENSE) for the full text.
