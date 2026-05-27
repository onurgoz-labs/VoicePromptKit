# Overlay & audit-log formats — reference

Read this when Phase 9 or Phase 10 of `SKILL.md` carries out user decisions. It is the canonical specification for the two artefacts that record what the user chose: `inline-suggestions.md` (the overlay file) and `decisions.jsonl` (the append-only audit log). Both live under `.promptcheck/<basename>/run-NNN/` alongside `findings.json` and `report.md`.

This file defines **formats and contracts only**. The procedural flow — when each artefact is written, which prompts the user sees — belongs in `SKILL.md` (Phase 9, Phase 10) and is not duplicated here.

## 1. `inline-suggestions.md` — the overlay file

### Purpose

The original prompt file is sacrosanct. When the user says `yorum bırak` / `overlay` / `leave as suggestion` for a finding, the suggested change lands **here**, not in the prompt. The user can read this file side-by-side with the prompt; Phase 10 re-renders it idempotently on every pass.

The overlay file is the structured home for "I want to keep thinking about this" decisions. It is plain markdown so the user can grep it, read it on GitHub, or paste it into a separate discussion.

### File path

```
.promptcheck/<basename>/run-NNN/inline-suggestions.md
```

Phase 10 always writes to the **latest** run's overlay (the run that produced the `findings.json` being acted on). Older runs keep their own overlays so the history of past decisions is preserved per-run.

### Template (strict — Phase 10 rewrites this verbatim)

```markdown
# PromptChecker Overlay — <basename>

- **Prompt:** `<absolute path>`
- **Run:** `run-NNN`
- **Generated:** <ISO 8601>
- **Status legend:** pending / overlay / overlay (revised) / applied (kept for history)

## Findings with overlay status

### L<line> [<finding-id> <lens> severity=<low|medium|high>] — status: overlay
- **Current:** `<current_excerpt>`
- **Suggested:** `<suggested_fix or "(see rationale)">`
- **Rationale:** <rationale>
- **Decided:** <ISO 8601>
- **Note:** <user-supplied note or omit>

### L<line> [<finding-id> <lens>] — status: overlay (revised)
- **Current:** `<current_excerpt>`
- **Original suggestion:** `<original suggested_fix from findings.json>`
- **User-revised:** `<the text the user supplied during konuşalım>`
- **Rationale:** <original rationale>
- **Decided:** <ISO 8601>
```

### Rendering rules

- **Sort:** entries are ordered by `line` ascending, then by severity descending (`high` → `medium` → `low`). Within a line + severity tie, order by finding id (`C1 < D1 < G1 < T1`).
- **Subset:** only findings where `status == "overlay"` or `status == "overlay (revised)"` appear here. `applied` and `dismissed` findings DO NOT appear in the overlay file — they live only in `decisions.jsonl`.
- **Pending findings DO NOT appear either.** A `pending` finding (one the user left without a decision at session end) is surfaced via `/prompt-check-resume`, not this file.
- **Idempotent rewrite, not append.** Phase 10 rebuilds the whole file from the current state of `session.json`. Re-running Phase 10 against the same `session.json` produces byte-identical output (modulo the `Generated:` timestamp). Never `>>` append to this file.
- **Empty state:** if no findings have overlay status, write the header section only — title, four metadata bullets, the `## Findings with overlay status` heading, and nothing under it. No error, no placeholder text.
- **Backticks inside excerpts:** if `current_excerpt` or `suggested_fix` contains a backtick, swap the surrounding `` ` `` for `` `` `` (pair of backticks) so markdown renders correctly.
- **Note line omitted** when the user supplied no note. Don't write `**Note:** —` or empty quotes.
- **Decided timestamp** equals the `ts` of the most recent `overlay` / `revised` entry for that finding in `decisions.jsonl`.

## 2. `decisions.jsonl` — append-only audit log

### Purpose

Every action ever taken on a finding is recorded here, in chronological order, one JSON object per line. This is the **source of truth** for "what did we decide and when". `session.json` is a derived snapshot computable from this log; `inline-suggestions.md` is a human-readable view of a subset of it.

### File path

```
.promptcheck/<basename>/run-NNN/decisions.jsonl
```

- Created the first time a user decision is logged in Phase 9 of this run.
- Appended to from Phase 10 (and any later resume session).
- **Never rewritten.** Not even on `/prompt-check-resume`. Not even when a decision is undone — see `reverted` below.

### Append discipline

Each line MUST be a single, complete JSON object terminated by a newline. Use `echo "$LINE" >> decisions.jsonl` semantics: one append syscall, race-safe under POSIX (`O_APPEND` guarantees atomicity for writes ≤ `PIPE_BUF`). No multi-line JSON. No trailing comma. UTF-8 encoding throughout.

If a write fails partway, the next Phase 10 pass MUST detect the truncated final line (JSON parse failure) and abort with a clear error rather than silently appending past corrupted data.

### Required fields on every line

- `ts` — ISO 8601 UTC timestamp with millisecond precision (`2026-05-27T10:15:23.421Z`).
- `finding` — finding id (e.g. `"C1"`, `"D2"`, `"G1"`, `"T3"`).
- `lens` — one of `"conflict"`, `"dominance"`, `"gap"`, `"drift"`, `"tr_phonetic"`.
- `action` — one of the values defined in the taxonomy below.

Action-specific fields are required as noted per action.

### Action taxonomy

The `action` field has exactly these values:

| `action` | Meaning | Required extra fields | Optional extra fields |
|---|---|---|---|
| `applied` | Suggested fix was written to the prompt file. | `target` (`"prompt_file"` or `"overlay_file"`), `from` (pre-edit excerpt), `to` (post-edit string), `line` | — |
| `overlay` | Suggested fix was written to `inline-suggestions.md`; the prompt is untouched. | — | `note` (user-supplied text) |
| `dismissed` | User chose to leave the finding alone. No file is touched. | — | `reason` (user-supplied note) |
| `discussed` | User entered the `konuşalım` sub-flow for this finding. No file is touched yet — this entry just marks that the conversation started. | — | — |
| `revised` | During `konuşalım` the user changed the suggested text. | `from` (original `suggested_fix`), `to` (user's revised text) | — |
| `routed_to_overlay` | TR routing rule fired (or stale-audit guard converted an applied to an overlay). | `reason` (e.g. `"TR phonetic findings never modify the prompt file"`) | — |
| `reverted` | Undoes a prior entry. The original entry is **never deleted**; the reversion is its own line. | `reverts` (the `ts` of the entry being undone) | `reason` |

**Sequencing rules:**
- A `revised` line is always followed by another line — either `applied` or `overlay` — that records the final destination of the revised text.
- A `routed_to_overlay` line is always followed (in the same Phase 10 pass) by an `overlay` line that records the actual write to `inline-suggestions.md`.
- A `discussed` line MAY or MAY NOT be followed by `revised` / `applied` / `overlay` / `dismissed`. If a session ends mid-discussion, the finding stays `pending` and `/prompt-check-resume` picks it back up.

### Example lines

```jsonl
{"ts":"2026-05-27T10:15:23.421Z","finding":"C1","lens":"conflict","action":"applied","target":"prompt_file","from":"Always be formal and use professional language at all times.","to":"Maintain a professional but warm register.","line":15}
{"ts":"2026-05-27T10:15:42.108Z","finding":"D2","lens":"dominance","action":"dismissed","reason":"role-override is intentional"}
{"ts":"2026-05-27T10:16:01.892Z","finding":"G2","lens":"gap","action":"overlay","note":"will revisit next week"}
{"ts":"2026-05-27T10:16:30.014Z","finding":"C3","lens":"conflict","action":"discussed"}
{"ts":"2026-05-27T10:17:15.502Z","finding":"C3","lens":"conflict","action":"revised","from":"Maintain a professional but warm register.","to":"Default formal; lean warm for first-time callers."}
{"ts":"2026-05-27T10:17:20.330Z","finding":"C3","lens":"conflict","action":"overlay","note":"discussed result"}
{"ts":"2026-05-27T10:18:00.117Z","finding":"T1","lens":"tr_phonetic","action":"routed_to_overlay","reason":"TR phonetic findings never modify the prompt file"}
{"ts":"2026-05-27T10:18:00.245Z","finding":"T1","lens":"tr_phonetic","action":"overlay"}
```

### Reading the log

Common one-liners:

- `grep '"action":"applied"' decisions.jsonl` — every prompt-file modification, in order.
- `grep '"action":"dismissed"' decisions.jsonl` — every conscious "no, leave it".
- `jq 'select(.finding=="C3")' decisions.jsonl` — full history for one finding.
- `jq -s 'group_by(.finding) | map({finding: .[0].finding, last: .[-1].action})' decisions.jsonl` — current state per finding (derived snapshot).

## 3. Relationship between session.json, decisions.jsonl, and inline-suggestions.md

| Artefact | Shape | Update style | Role |
|---|---|---|---|
| `session.json` | Current snapshot — where each finding stands RIGHT NOW. | Rewritten on every state change. | Fast read for the next interactive turn. |
| `decisions.jsonl` | Full history in chronological order. | Append-only. Never rewritten. | Source of truth. Survives `session.json` corruption. |
| `inline-suggestions.md` | Human-readable view of the OVERLAY subset of `session.json`. | Rewritten on every Phase 10 pass (idempotent). | Doc humans actually read. |

If `session.json` is lost or corrupted, `decisions.jsonl` can rebuild it deterministically: replay each line, applying the action to the in-memory state, and the final state matches what `session.json` should hold. The skill MAY use this as a recovery path; it is not required in the normal flow.

## 4. Phase 10 ordering when both `apply` and `overlay` decisions land in the same pass

A single Phase 10 pass processes decisions in this order:

1. **`dismissed`** — log only, no I/O beyond appending to `decisions.jsonl`.
2. **`overlay`** — rebuild `inline-suggestions.md` from the post-update `session.json`.
3. **`applied`** — write to the prompt file in one pass. For each finding, `line` and `current_excerpt` must agree (substring match on the named line). Ambiguous occurrences (substring appears more than once on the line) are skipped and **converted to `overlay`** with a note explaining why.
4. **TR-routed entries** — every finding that hit `routed_to_overlay` belongs in the overlay file too; the routing line in `decisions.jsonl` is a pure redirect record, the `overlay` entry that follows it is what causes the actual write.
5. **Re-render `session.json`** to reflect every state change in this pass.
6. **Return to Phase 9** for any `discussed` findings that have not been resolved yet (`revised` / `applied` / `overlay` / `dismissed` has not yet followed the `discussed` line).

The ordering matters: writing the overlay file before the prompt file means a failure between steps 2 and 3 leaves the user with an honest "here's what I planned" record rather than half-modified state.

## 5. Stale-audit guard interaction

Phase 10 MUST verify the prompt file's SHA256 matches `findings.json.prompt_sha256` before any `applied` action (this mirrors the apply-mode pre-flight described in `SKILL.md`).

On mismatch — i.e. the prompt drifted since the audit:

- **Abort the `applied` step** for the affected finding(s). Do not write to the prompt file.
- **Convert** that decision automatically to `overlay`, attaching a note: `"prompt drifted since audit; routed to overlay"`.
- **Log** the conversion in `decisions.jsonl` as a `routed_to_overlay` entry with `reason: "stale audit — prompt SHA256 mismatch"`, immediately followed by the resulting `overlay` entry.
- Surface to the user: the run is salvaged (their intent is preserved in the overlay file) but they should re-run `/prompt-check` to refresh the audit before any future `applied` actions.

The stale-audit guard never silently discards a decision; it always preserves intent by re-routing to the overlay channel.
