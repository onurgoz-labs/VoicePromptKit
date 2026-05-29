# Turkish phonetic lens — reference

Active only when `frontmatter.tr_phonetic == true`. Voice agents (Vapi, ElevenLabs, OpenAI Realtime, etc.) read text aloud; constructs that look fine in writing may be mispronounced or sound robotic.

## Output shape — fix_kind set per category

**TR findings carry `fix_kind` set by their `kind`:** `foreign_word` + `abbreviation` → `"advisory"` (overlay only, prompt never modified); `number_readability` + `punctuation` → `"replace"` (normal apply flow — user's `düzelt` decision modifies the prompt file). The `kind` field distinguishes the detection category, and each finding surfaces a concrete suggestion (either a `suggested_fix` for textual issues or a `pronunciation_entry` for foreign words / abbreviations).

| `kind` | `fix_kind` | Suggestion shape | What apply-mode does |
|---|---|---|---|
| `number_readability` | `replace` | `suggested_fix` (verbal form of the number) | Replaces `current_excerpt` with `suggested_fix` in the prompt file when the user picks `düzelt`; routes to overlay on `yorum bırak` |
| `abbreviation` | `advisory` | `pronunciation_entry` (curated risky list) | Overlay only — prompt text never modified. Author hand-merges into a pronunciation guide or TTS provider config |
| `foreign_word` | `advisory` | `pronunciation_entry` (with `strategy: pronounce | rephrase | follow_with_translation`) | Overlay only — voice-design call the author owns; the prompt visible script is not edited |
| `punctuation` | `replace` | `suggested_fix` (corrected punctuation / break point) | Replaces `current_excerpt` with `suggested_fix` in the prompt file when the user picks `düzelt`; routes to overlay on `yorum bırak` |

Common finding shape:

```json
{
  "id": "T1",
  "kind": "number_readability | abbreviation | foreign_word | punctuation",
  "fix_kind": "replace | advisory",
  "severity": "low | medium | high",
  "line": 42,
  "current_excerpt": "...",
  "suggested_fix": "...",
  "pronunciation_entry": {
    "term": "DHL",
    "strategy": "pronounce | rephrase | follow_with_translation",
    "phonetic": "de-ha-el",
    "alt_translation": null,
    "note": null
  },
  "rationale": "..."
}
```

Only one of `suggested_fix` / `pronunciation_entry` is populated per finding. For `fix_kind: "replace"` findings (`number_readability`, `punctuation`), apply-mode reads `suggested_fix` and rewrites the prompt file. For `fix_kind: "advisory"` findings (`foreign_word`, `abbreviation`), apply-mode never modifies the prompt — the `pronunciation_entry` lands in the overlay for the author to hand-merge.

### `strategy` semantics

| strategy | Meaning | `phonetic` | `alt_translation` |
|---|---|---|---|
| `pronounce` | TTS should read the term with the given phonetic spelling (`pound → paund`) | **required** | optional |
| `rephrase` | The author should avoid the term and substitute the alternative when speaking (`Palaeologlar → son Bizans hanedanı`) | optional / null | optional |
| `follow_with_translation` | Speak the term but always follow with a Turkish gloss (`La Turquie Kemaliste → "yani Kemal'in Türkiye'si dergisi"`) | optional | **required** (the gloss) |

If `phonetic` is null, `strategy` must be `rephrase` or `follow_with_translation`. Default strategy when omitted is `pronounce`.

Severity guide:
- **high** — TTS will mispronounce or skip; meaning lost.
- **medium** — TTS reads it but unnaturally; listener may hesitate.
- **low** — cosmetic / preference.

## Why the split — voice-design vs textual correction

`foreign_word` and `abbreviation` findings emit `pronunciation_entry` payloads (DHL → "de-ha-el", Gaggia → "gacca"). These describe HOW the TTS should speak the term, not WHAT text should appear in the prompt. Auto-editing the prompt to insert phonetic spellings would corrupt the visible script (`DHL` becoming `de-ha-el` breaks the meaning). These stay advisory — the author hand-merges them into a pronunciation guide block or the TTS provider's config.

`number_readability` and `punctuation` findings emit `suggested_fix` payloads with concrete textual corrections (100 TL → yüz lira; missing comma added). These ARE textual edits — the visible script should change. They emit `fix_kind: "replace"` and follow the normal apply flow when the user picks `düzelt`. The author can still route them to overlay via `yorum bırak`.

## Hard rule: foreign words and abbreviations never modify the prompt

`foreign_word` and `abbreviation` findings are advisory-only — apply-mode NEVER modifies the prompt file for them, regardless of the verb the user typed. The pronunciation lands in `inline-suggestions.md` for the author to hand-merge.

`number_readability` and `punctuation` findings MAY modify the prompt — same as `conflict` / `dominance` / `gap` findings. They emit `fix_kind: "replace"` and follow the normal `düzelt` / `yorum bırak` routing.

## Hard rule: never translate

This lens is about **how Turkish TTS reads text**, not about Turkish vocabulary substitution. `pound → paund` is a phonetic hint (allowed). `pound → İngiliz lirası` is a semantic translation (forbidden — the brand / domain meaning is the author's call, not the lens's).

The only exceptions are `alt_translation` (a Turkish alternative offered as metadata) and the `follow_with_translation` strategy (which keeps the original term and adds a gloss). Neither replaces the original text by default; the prompt author decides.

## Phase 6.0 — Seed `pronunciation_map` from existing blocks

Run this **before** body-scan detection in Phase 6. Look for any of these patterns in the body and parse them into seed entries:

1. **Managed marker block:**
   ```
   <!-- promptchecker:pronunciation-guide:start -->
   ... bullet entries ...
   <!-- promptchecker:pronunciation-guide:end -->
   ```
2. **Legacy heading prose blocks** (ALL-CAPS or markdown heading variants):
   - `TTS PRONUNCIATION NOTES`
   - `Pronunciation guide`
   - `Okunuş rehberi`
   - `Telaffuz` / `Telaffuz notları`
   followed by a bullet list (`- term → ...`) within the next ~20 lines.

For each parsed entry, infer the `strategy`:
- Contains "rephrase" / "rephrase as" / "yerine" → `rephrase`
- Contains "follow with" / "always follow" / "ardından söyle" → `follow_with_translation`
- Contains a phonetic-style hint (`"foo nokta kom"`, dashes, parenthesised reading) → `pronounce`

Add every parsed entry to the seed `pronunciation_map`. **Do not generate findings for terms already in the seed** — they are already curated; the body scan dedupes against the seed.

Seed entries are reported in the final `pronunciation_map` so the author can see at a glance what they have already written; apply-mode does not touch them.

## 1. Number readability (`kind: "number_readability"`)

Numerals get read digit-by-digit or with wrong place values. For monetary amounts and percentages spoken aloud, the recommended verbal form (`100 TL` → `yüz lira`) goes into `suggested_fix`. For phone numbers, IBANs, postal codes, the suggestion is to add a separate "read digit by digit" instruction near the line (rationale only — no fix string).

All findings are advisory; the table below records the **shape of the suggestion** the author sees in the report.

| Pattern | Suggestion shape | Example suggested_fix / rationale |
|---|---|---|
| Currency: `100 TL`, `8.100 TL`, `₺1.250,50` | `suggested_fix` populated | `yüz lira`, `sekiz bin yüz lira` |
| Percentage: `%25` | `suggested_fix` populated | `yüzde yirmi beş` |
| Date: `17/05/2026` | `suggested_fix` populated | `on yedi Mayıs iki bin yirmi altı` |
| Time: `14:30` | `suggested_fix` populated | `on dört otuz` |
| Phone, IBAN, posta kodu | `suggested_fix` empty; rationale only | "Add an instruction: 'rakam rakam oku' near this line" |
| Ordinals: `5.`, `21.` | `suggested_fix` populated | `beşinci`, `yirmi birinci` |

### Malformed Turkish numbers (severity `medium` or `high`)

These slip past spell-checkers but TTS reads them literally — flag them when the prompt is in Turkish and the body is meant to be spoken.

| Pattern | Why it's wrong | Suggested fix (in `suggested_fix`) |
|---|---|---|
| `bir bin` followed by `yüz`/`...` | "bin" already means 1000; "bir bin" reads as ~1001. The author meant just `bin`. | `bin` |
| `bir milyon`, `bir milyar` | Same root issue: redundant `bir`. | `milyon` / `milyar` (context-dependent) |
| `içinz`, `içiniz` mid-sentence where `için` was meant | Typo; TTS reads "ee-cheen-z" / similar. | `için` |
| Number-word with no space before unit (`onbin`, `yüzlira`) | TTS may concatenate or hesitate. | `on bin`, `yüz lira` |

## 2. Abbreviations & technical terms (`kind: "abbreviation"`)

Default behaviour: **do not flag**. Most Turkish acronyms are read acceptably letter-by-letter. Flag only when the abbreviation is in the **curated risky list**.

**Curated risky (advisory, `pronunciation_entry` populated):**

| Term | Phonetic | Strategy | Notes |
|---|---|---|---|
| `DHL` | `de-ha-el` | `pronounce` | English-trained TTS reads "D-H-L" |
| `SMS` | `se-me-se` | `pronounce` | Often anglicised |
| `OTP` | `o-te-pe` | `pronounce` | Often anglicised |
| `URL` | `u-er-le` | `pronounce` | — |
| `WhatsApp` | `votsap` | `pronounce` | Common brand |
| `iPhone` | `ay-fon` | `pronounce` | Common brand |
| `D&R`, `S&P`, `H&M` (any `X&Y` brand) | `de ve er`, etc. | `pronounce` | `&` symbol read literally |
| `pound`, `euro`, `dollar` | `paund`, `oyro`, `dolar` | `pronounce` | Foreign currency words |
| `Boyut Store` (or any `<TR-word> Store`) | `boyut store` | `pronounce` | "Store" English read |
| `CD`, `DVD`, `CD/DVD` | `se-de`, `de-ve-de` | `pronounce` | English letter read |
| `A.Ş.` | — | `rephrase` | `alt_translation: "anonim şirketi"` (optional) |
| `T.C.` | — | `rephrase` | `alt_translation: "Türkiye Cumhuriyeti"` (optional) |

**Whitelist — explicitly DO NOT flag:**

- `PTT`, `KDV`, `ÖTV`, `SGK`, `MEB`, `TBMM`, `TRT` — TR TTS reads these fine.
- Single-letter qualifiers: `A Blok`, `B kapısı`, `C segmenti`, `D vitamini`.
- Latin technical acronyms inside code fences (CRM, ERP, API) — those are config, not speech.
- Any line that is itself a pronunciation instruction (see Skip rules).

## 3. Foreign words & transliteration (`kind: "foreign_word"`)

Surface as an advisory finding with `pronunciation_entry` populated. Default `strategy: "pronounce"`; switch to `"follow_with_translation"` when a Turkish gloss is conventional.

| Word | phonetic | strategy | alt_translation |
|---|---|---|---|
| `iPhone` | `ay-fon` | pronounce | null |
| `WhatsApp` | `votsap` | pronounce | null |
| `Wi-Fi` | `vay-fay` | pronounce | null |
| `email` | `i-meyl` | follow_with_translation | `e-posta` |
| `check-in` | `çek-in` | follow_with_translation | `giriş işlemi` |
| `YouTube` | `yu-tüb` | pronounce | null |
| `Google` | `gugıl` | pronounce | null |
| `Hebrew` | `hebru` | follow_with_translation | `İbrani` |

**Latin/Greek/French proper nouns near historical/cultural context** (e.g. `Nea Roma`, `Palaeologlar`, `Iustinianus`, `La Turquie Kemaliste`):

These are hard to phonetic-guess confidently. Leave `pronunciation_entry` either empty or populated with `strategy: "rephrase"` and no `phonetic` — the author knows the intended reading. Heuristic for detection: repeated proper noun across multiple lines, non-Turkish morphology (consonant clusters like `Pala-`, `Ius-`, `Nea`, or prefixes like `La `), in historical / cultural text.

## 4. Punctuation & pacing (`kind: "punctuation"`)

All advisory; the column below records whether `suggested_fix` carries a concrete substitution or the rationale is the only payload.

| Pattern | Suggestion shape | Notes |
|---|---|---|
| Double space before comma, double commas, stray punctuation | `suggested_fix` populated | Obvious typo |
| Sentence > 120 chars with no comma / period | `suggested_fix` populated if break point is obvious; else rationale only | Suggest specific comma position |
| Sentence > 80 chars with one or zero commas | rationale only | `low` severity unless voice context is critical |
| Imperative ending in `.` where `?` or `…` would match prosody | rationale only | Author's judgement |
| Long enumeration without "birincisi / ikincisi" cues | rationale only | Pacing nuance |

## Skip rules — apply BEFORE detection

Do not generate findings for any line that matches any of these:

- **Pronunciation-context line** — contains `okunuş`, `telaffuz`, `oku:`, `şöyle oku`, `diye okunur`, `harf harf`, `→`, `->`, `phonetic`, `pronunciation`.
- **Internal-notes block** — line is under a heading containing `INTERNAL`, `INTERNAL NOTES`, `do not speak`, `okunmaz`, `söylenmez`.
- **Code / config** — line is inside a markdown code fence, inline `<code>`, YAML frontmatter, or a JSON / JS block.
- **Quoted transcript** — line is inside a multi-line quoted block.
- **Tabular content** — markdown table rows (`| ... |`).
- **Inside any pronunciation block** (managed marker block OR legacy heading block detected in Phase 6.0) — the block content is the canonical source, not a target for new findings.
- **`number_readability`-specific skip — already in spoken form.** Do NOT flag a `number_readability` finding when the relevant span contains NO Arabic numerals AND no currency abbreviations / symbols (`TL`, `₺`, `$`, `€`, `%`). Spoken-form expressions like `iki yüz Türk lirası`, `yüzde yirmi beş`, `on yedi Mayıs iki bin yirmi altı`, `on dört otuz` are already correctly written for TTS. The lens fires ONLY when there is a numeric / abbreviated token to transform — never to "trim" or "rephrase" prose that is already spoken. Rewording spoken expressions ("iki yüz Türk lirası" → "iki yüz lira", dropping `Türk` as "redundant") is semantic editing, forbidden by the **never translate / never semantically rewrite** hard rule above.

If a line satisfies any skip rule, no finding is emitted regardless of detection match.

## What this lens does NOT do

- Does not check ünlü uyumu (vowel harmony) — grammar, not TTS.
- Does not flag colloquialisms or dialect.
- Does not translate. Foreign words stay verbatim by default.
- Does not run regex on Latin technical terms inside code blocks or model identifiers.
- Does not flag Turkish-friendly abbreviations (`PTT`, `KDV`, `SGK`, `MEB`).
- Does not generate findings for lines that are themselves pronunciation instructions.
- Does not invent phonetic spellings for obscure proper nouns — flags them as `advisory` for the author to resolve.
