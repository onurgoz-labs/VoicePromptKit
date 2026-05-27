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

### {{section_marker}} [{{finding_id}} {{lens}} severity={{severity}}] — status: overlay
- **Current:** `<current_excerpt>`
- **Diff:** (for substring-style suggestions — render as a ```diff block; see "Rendering mode" below)
  ```diff
  - <current_excerpt>
  + <suggested_fix>
  ```
- **Action:** `<suggested_fix>` (for structural / TODO suggestions; see "Rendering mode" below)
- **Rationale:** <rationale>
- **Decided:** <ISO 8601>
- **Note:** <user-supplied note or omit>

### {{section_marker}} [{{finding_id}} {{lens}}] — status: overlay (revised)
- **Current:** `<current_excerpt>`
- **Original suggestion:** `<original suggested_fix from findings.json>`
- **User-revised:** `<the text the user supplied during konuşalım>` (rendered as a diff block vs. `<current_excerpt>` when substring-style, otherwise as **Action:**)
- **Rationale:** <original rationale>
- **Decided:** <ISO 8601>

## Pronunciation map (TTS — internal reference)

(Rendered only when findings.json.pronunciation_map is non-empty. Entries from BOTH source: "seed" and source: "finding" are listed, deduped by term.)

- `<term>` → "<phonetic>" (strategy: <strategy>) — <note (if present)>
  - From: <T<id>, T<id>>  (source_finding_ids; empty for seed-only entries)
- `<term>` — rephrase as "<alt_translation>" — <note (if present)>
  - From: seed (prompt's pre-existing pronunciation guide block)
```

Where `{{section_marker}}` is:
- `Section 7.2 — L284` (when section_ref.subsection is set)
- `Section 7 — L284` (when only section is set)
- `L284` (when section_ref is null)

(English in inline-suggestions.md regardless of `report_language` — the overlay file is a structured artefact, language switching applies to report.md only.)

The section marker matches `section_ref` from findings.json. When `section_ref: null`, the marker is the bare line number. Schema lens findings that flag a heading itself (e.g. `section_gap` on line 280 = `## SECTION 7`) use the heading's own section as the marker.

### Rendering mode for the Suggested field

The overlay uses the same heuristic as report.md Phase 7:
- Substring-style suggestion (ratio > 0.6 to current_excerpt AND not starting with a structural keyword) → render as a ```diff block.
- Structural suggestion ("Add ", "Rewrite ", "Move ", "Replace R", "Remove ", "Reword ", TODO sentinels, "Intentional —") → render as `**Action:** <suggested_fix>`.
- Empty suggested_fix → render `**Action:** _(see rationale)_`.

The overlay is rewritten in full each Phase 10 pass (idempotent), so the rendering choice is recomputed every time — no stale diff blocks survive a switch from substring to structural.

In Python (mirrors the heredoc in SKILL.md Phase 7):

```python
import difflib

STRUCTURAL_PREFIXES = ("Add ", "Add: ", "Rewrite ", "Move ", "Replace R", "Remove ",
                       "Reword ", "TODO:", "Intentional —", "Intentional -")

def render_overlay_body(current, suggested):
    if not suggested or suggested.strip() == "":
        return "**Action:** _(see rationale)_"
    if any(suggested.startswith(p) for p in STRUCTURAL_PREFIXES):
        return f"**Action:** {suggested}"
    ratio = difflib.SequenceMatcher(None, current, suggested).ratio()
    if ratio > 0.6:
        return f"```diff\n- {current}\n+ {suggested}\n```"
    return f"**Action:** {suggested}"
```

### Rendering rules

- **Sort:** entries are ordered by **severity descending** (`high` → `medium` → `low`), then by **lens group** in the fixed order (`conflict`, `dominance`, `gap`, `drift`, `tr_phonetic`), then by **line ascending** within each (severity, lens) bucket. Within a (severity, lens, line) tie, order by finding id (`C1 < D1 < G1 < T1`). This matches the sort applied to `findings.json.findings[]` and to `report.md`.
- **Subset:** only findings where `status == "overlay"` or `status == "overlay (revised)"` appear here. `applied` and `dismissed` findings DO NOT appear in the overlay file — they live only in `decisions.jsonl`.
- **Pending findings DO NOT appear either.** A `pending` finding (one the user left without a decision at session end) is surfaced via `/prompt-check-resume`, not this file.

**Excluded from per-finding entries:**

- TR findings with `fix_kind: "advisory"` that were auto-filed (i.e. their most recent `decisions.jsonl` action is `auto_filed`). These appear ONLY in the bottom "Pronunciation map" section. The rationale: auto-filed findings represent background routing, not user decisions; surfacing them as per-finding entries forces the reader to scan past noise the user never asked about. The per-finding section is reserved for findings the user explicitly chose to overlay (action: `overlay`) or revised (action: `revised`).
- Backward compatibility: for sessions produced before the `auto_filed` action existed, treat any TR finding with `lens == "tr_phonetic"` AND `fix_kind == "advisory"` AND `status == "overlay"` as auto-filed for rendering purposes (per-finding entry skip), even when its decisions.jsonl history lacks an `auto_filed` line. This preserves the new UX behaviour when resuming legacy runs.
- **Idempotent rewrite, not append.** Phase 10 rebuilds the whole file from the current state of `session.json`. Re-running Phase 10 against the same `session.json` produces byte-identical output (modulo the `Generated:` timestamp). Never `>>` append to this file.
- **Empty state:** if no findings have overlay status, write the header section only — title, four metadata bullets, the `## Findings with overlay status` heading, and nothing under it. No error, no placeholder text.
- **Backticks inside excerpts:** if `current_excerpt` or `suggested_fix` contains a backtick, swap the surrounding `` ` `` for `` `` `` (pair of backticks) so markdown renders correctly.
- **Note line omitted** when the user supplied no note. Don't write `**Note:** —` or empty quotes.
- **Decided timestamp** equals the `ts` of the most recent `overlay` / `revised` entry for that finding in `decisions.jsonl`.

### Pronunciation map rendering rules

The "Pronunciation map" section sits at the **bottom** of `inline-suggestions.md`, after every per-finding entry. It is a flat reference list of every term in `findings.json.pronunciation_map` — both `source: "seed"` (entries the prompt's pre-existing pronunciation guide block already contained) and `source: "finding"` (entries derived from TR phonetic findings). Phase 10 reads `findings.json.pronunciation_map` and renders one bullet per entry.

Per-entry rendering depends on `strategy`:

- **`strategy: "pronounce"`** → `` `<term>` → "<phonetic>" `` with optional `(alt: "<alt_translation>")` appended when `alt_translation` is present.
- **`strategy: "rephrase"`** → `` `<term>` — rephrase as "<alt_translation>" `` with the `note` appended in parens when present. If `alt_translation` is empty, render `` `<term>` — rephrase (no concrete alternative supplied) `` and surface the note as-is.
- **`strategy: "follow_with_translation"`** → `` `<term>` → follow with: "<alt_translation>" ``.

After the main line, indent a sub-bullet `From: ...`:
- For `source: "seed"` entries → `From: seed (prompt's pre-existing pronunciation guide block)`.
- For `source: "finding"` entries → `From: <comma-joined source_finding_ids>` (e.g. `From: T3, T7`).

Append the entry's `note` as a trailing `— <note>` on the main line when present and non-empty, regardless of strategy.

Sort entries alphabetically by `term` (case-insensitive). Dedupe by `term` (case-insensitive); seed wins on collision (mirrors Phase 7's merge rule).

This section is **idempotent** — rewritten in full each Phase 10 pass. It mirrors `findings.json.pronunciation_map` so the user has every TR-phonetic risky term in a single bottom-of-file reference, independent of which findings they routed where (overlay, applied, dismissed, or still pending). The section heading + body exist purely as a consolidated TTS cheat-sheet; per-finding entries remain the structured home for the decision flow.

If `findings.json.pronunciation_map` is empty or absent, the entire "Pronunciation map" section is OMITTED — no empty heading, no "(no entries)" placeholder. The section's presence in the rendered file is itself a signal that there are risky terms worth scanning before recording.

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
| `auto_filed` | TR pronunciation finding (foreign_word / abbreviation) routed to overlay without user interaction. Logged once per such finding in Phase 9.6. The session.json status is `"overlay"` (same as a user overlay decision); the distinction lives in this action field. | `reason`, `fix_kind` (always `"advisory"`), `kind` (`"foreign_word"` or `"abbreviation"`) | — |
| `reverted` | Undoes a prior entry. The original entry is **never deleted**; the reversion is its own line. | `reverts` (the `ts` of the entry being undone) | `reason` |

**Sequencing rules:**
- A `revised` line is always followed by another line — either `applied` or `overlay` — that records the final destination of the revised text.
- A `routed_to_overlay` line is always followed (in the same Phase 10 pass) by an `overlay` line that records the actual write to `inline-suggestions.md`.
- An `auto_filed` line stands alone — it is NOT followed by an `overlay` line. The auto-file action conceptually collapses the routing + overlay write into a single audit entry, since the user never made an explicit overlay decision. `session.json.findings_state[<id>].status` is still `"overlay"`, mirroring a user-initiated overlay.
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
{"ts":"2026-05-27T18:24:39.821Z","finding":"T1","lens":"tr_phonetic","action":"auto_filed","reason":"TR advisory finding — pronunciation hints are filed to inline-suggestions.md without user decision","fix_kind":"advisory","kind":"foreign_word"}
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

**`auto_filed` and user-`overlay` collapse to the same session status.** Both the `auto_filed` action (Phase 9.6 background routing for TR advisory findings) and the user-initiated `overlay` action result in `session.json.findings_state[<id>].status: "overlay"`. The distinction is purely audit-trail (decisions.jsonl). For replay purposes, the FIRST action ever logged for a TR advisory finding determines whether it was auto-filed or user-routed — replay the log line by line and the first `auto_filed` / `overlay` entry per finding sets the origin. This origin is preserved in `decisions.jsonl` even when `session.json` is rebuilt from scratch.

## 4. Phase 10 ordering when both `apply` and `overlay` decisions land in the same pass

A single Phase 10 pass processes decisions in this order:

1. **`dismissed`** — log only, no I/O beyond appending to `decisions.jsonl`.
2. **`overlay`** — rebuild `inline-suggestions.md` from the post-update `session.json`.
3. **`applied`** — **feasibility-first, single-event logging.** Phase 10 evaluates each `applied`-tagged finding's feasibility BEFORE writing anything to `decisions.jsonl`. The `decisions.jsonl` write is a single line reflecting the actual outcome, not the user's pre-check intent. `applied` is written only when the prompt file was genuinely modified; otherwise the finding produces exactly one `routed_to_overlay` line followed by exactly one `overlay` line.

   The feasibility check, in fixed order, assigns each finding one of these outcomes:

   | Outcome | Trigger | `routed_to_overlay.reason` string |
   |---|---|---|
   | `tr_routed` | `finding.lens == "tr_phonetic"` | `"TR phonetic findings never modify the prompt file"` |
   | `no_concrete_fix` | `finding.suggested_fix` is null or empty | `"no concrete suggested_fix — manual author revision required"` |
   | `sha_mismatch` | current prompt SHA256 != `findings.json.prompt_sha256` | `"stale audit — prompt SHA256 mismatch"` |
   | `ambiguous` | `current_excerpt` appears zero or >1 times on the named line | `"ambiguous occurrence — substring matches multiple positions"` |
   | `applicable` | all four checks above passed | (no `routed_to_overlay`; a single `applied` line is written instead) |

   For `applicable`: perform the substring replacement, write the prompt file, then append ONE `applied` line. For any other outcome: append ONE `routed_to_overlay` line (with the matching reason), then ONE `overlay` line. Two lines max per failed finding. Never write a misleading `applied` line that implies the prompt was modified when it was not.
4. **TR-routed entries from Phase 9** — TR `applied` decisions that Phase 9 already redirected to `overlay` arrive at step 2 above as ordinary `overlay` state; the `routed_to_overlay` line for them was logged in Phase 9.4, not in step 3.
5. **Re-render `session.json`** to reflect every state change in this pass.
6. **Return to Phase 9** for any `discussed` findings that have not been resolved yet (`revised` / `applied` / `overlay` / `dismissed` has not yet followed the `discussed` line).

The ordering matters: writing the overlay file before the prompt file means a failure between steps 2 and 3 leaves the user with an honest "here's what I planned" record rather than half-modified state. The feasibility-first rule inside step 3 means `decisions.jsonl` never lies about whether the prompt file was actually touched.

Findings are rendered in severity descending → lens group → line ascending order, matching the sort in findings.json.

## 5. Stale-audit guard interaction

Phase 10 MUST verify the prompt file's SHA256 matches `findings.json.prompt_sha256` before any `applied` action (this mirrors the apply-mode pre-flight described in `SKILL.md`). The SHA check is one of the four feasibility outcomes enumerated in Section 4 — when it fires the `sha_mismatch` outcome is assigned and the same single-event pattern applies.

On mismatch — i.e. the prompt drifted since the audit:

- **Skip the prompt-file write** for the affected finding(s). Do not write to the prompt file.
- **Append ONE `routed_to_overlay` entry** to `decisions.jsonl` with `reason: "stale audit — prompt SHA256 mismatch"`, immediately followed by ONE `overlay` entry. **Never** an `applied` entry first — the feasibility check runs before any log write, so the misleading "applied then routed" double-event is impossible.
- Surface to the user: the run is salvaged (their intent is preserved in the overlay file) but they should re-run `/prompt-check` to refresh the audit before any future `applied` actions.

The stale-audit guard never silently discards a decision; it always preserves intent by re-routing to the overlay channel — using the same two-line pattern as the other three failed-feasibility outcomes (`tr_routed`, `no_concrete_fix`, `ambiguous`).

## 5. `pronunciations.md` — cross-version master file

The third PromptChecker artefact (alongside `inline-suggestions.md` and
`decisions.jsonl`). Sits at `.promptcheck/<basename>/pronunciations.md` —
ONE level above the per-run `run-NNN/` directories. Aggregates every
`pronunciation_map` entry across every audit run for this prompt. Updated
by Phase 10 in addition to the per-run inline-suggestions.md.

### Why it exists

Every audit re-discovers the same TR pronunciation hints (Gaggia → "gacca",
DHL → "de-ha-el"). Without an aggregate file, the author has to read each
run's `inline-suggestions.md` and assemble the master guide by hand. The
master file does that automatically — one source of truth for the TTS
provider config.

### File format

````markdown
# Pronunciations — <basename>

Cross-version pronunciation guide for `<absolute path to prompt file>`.

- **Last updated:** <ISO 8601 UTC>
- **Aggregated from:** <N> audit runs (run-001 … run-NNN)
- **Total entries:** <M> unique terms

## Entries

| Term | Strategy | Phonetic | Alternative | Note | First seen | Last seen | Section | Findings |
|---|---|---|---|---|---|---|---|---|
| `Gaggia Milano` | pronounce | gacca milano | — | Italian brand. Turkish TTS often reads 'Gaggia' letter-literally as 'gag-gia'; the intended Italian reading is closer to 'gacca' (gg+i → /dʒː/). 'Milano' reads naturally in Turkish. | run-001 (2026-05-27) | run-003 (2026-05-30) | 1.3 | T1@run-001, T1@run-002, T1@run-003 |
| `DHL` | pronounce | de-ha-el | — | English-trained TTS reads "D-H-L"; the Turkish reading is "de-ha-el". | run-002 (2026-05-28) | run-003 (2026-05-30) | 3.1 | T4@run-002, T4@run-003 |
| `Konstantinopolis` | rephrase | — | Bizans başkenti | Author marked this as high risk; rephrase when possible. | run-001 (2026-05-27) | run-003 (2026-05-30) | — | seed |

(One row per unique term, sorted alphabetically. Strategy column: `pronounce` / `rephrase` / `follow_with_translation`. Empty cells render as `—`. The `Section` column carries `section_ref.subsection` (e.g. `1.3`) if set, else `section_ref.section` (e.g. `1`) if set, else `—`; when the same term appeared in different sections across audits, the values are comma-separated (e.g. `1.3, 3.1`).)

## VAPI / TTS provider config (copy-paste)

```yaml
# pronunciations.yaml — paste into your Vapi/ElevenLabs/OpenAI Realtime config.
# Generated by PromptChecker. Override entries by hand if your TTS provider
# has different conventions.
pronunciations:
  - term: "Gaggia Milano"
    phonetic: "gacca milano"
    strategy: pronounce
  - term: "DHL"
    phonetic: "de-ha-el"
    strategy: pronounce
  - term: "Konstantinopolis"
    alternative: "Bizans başkenti"
    strategy: rephrase
    note: "Author marked this as high risk; rephrase when possible."
```

## Source attribution

Every row above is derived from one or more `pronunciation_map` entries
captured during audit runs. See each run's `inline-suggestions.md` for the
finding context and per-run timestamps; see each run's `decisions.jsonl`
for the per-finding audit trail.

Re-running `/prompt-check <basename>` regenerates this file. Manual edits
are preserved across runs ONLY for the per-term `Note` column (the rest is
recomputed from source). To pin a custom phonetic, append a row by hand
below the `## Custom additions` heading at the bottom of this file.

## Custom additions

(Author-managed. PromptChecker never touches anything below this heading.)

<!-- promptchecker:custom-additions:start -->
<!-- Append your own pronunciation rows here. They are preserved across runs. -->
<!-- promptchecker:custom-additions:end -->
````

### Update semantics

Phase 10 rebuilds this file on every run, AFTER `inline-suggestions.md` is
written. The rebuild walks every `run-NNN/findings.json` in the
prompt-scoped directory, unions their `pronunciation_map` arrays, dedupes
by term (case-insensitive, original casing preserved from first
occurrence), and re-emits the file.

Conflict resolution when the same term appears across runs with different
phonetic / alt_translation / note values:
- Later runs WIN for phonetic, alt_translation, and note (most recent
  audit is the most refined guess).
- First-seen `(run, date)` is recorded as immutable provenance.
- Last-seen `(run, date)` is updated on every appearance.
- `finding_refs` is the union of every contributing `<id>@<run>` reference.

The `## Custom additions` block (between the marker comments) is
PRESERVED VERBATIM on every rebuild. PromptChecker never touches it.

### Relationship to other artefacts

| File | Scope | Lifecycle |
|---|---|---|
| `inline-suggestions.md` | Per-run overlay (run-NNN) | Rewritten each Phase 10 pass for the current run. |
| `pronunciations.md` | Prompt-scoped (above run-NNN) | Rewritten each Phase 10 pass, aggregating ALL runs to date. |
| `decisions.jsonl` | Per-run audit log | Append-only. |

`pronunciations.md` is a derived view of the per-run pronunciation_map
arrays — losing it does not lose data (it can be rebuilt from scratch by
reading the run-NNN findings.json files).

### Idempotency contract

Re-running `/prompt-check <prompt>` on a prompt with NO content changes
produces a `pronunciations.md` that differs from the previous run ONLY in
the `Last updated:` timestamp and the new run's appearance in `last_seen`
columns. The terms, phonetics, strategies, and the Custom additions block
are byte-identical.
