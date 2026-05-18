# Turkish phonetic lens — reference

Active only when `frontmatter.tr_phonetic == true`. Voice agents (Vapi, ElevenLabs, OpenAI Realtime, etc.) read text aloud; constructs that look fine in writing may be mispronounced or sound robotic.

## Output shape — three fix kinds, one optional strategy field

Each TR finding declares one of three `fix_kind` values:

| `fix_kind` | When | Apply-mode behaviour |
|---|---|---|
| `replace` | Real textual error (typo, double space, malformed Turkish number, missing/extra punctuation) | Substring replace `current_excerpt` → `suggested_fix` |
| `pronunciation_hint` | Foreign word / risky abbreviation / brand whose **written text must stay** | Add `pronunciation_entry` to the managed guide block; do not touch the original line |
| `advisory` | Borderline / judgement call (long unparseable sentence, possible mistranslation, unfamiliar proper noun) | Reported only; no automatic apply |

Common finding shape:

```json
{
  "id": "T1",
  "kind": "number_readability | abbreviation | foreign_word | punctuation",
  "fix_kind": "replace | pronunciation_hint | advisory",
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

Only one of `suggested_fix` / `pronunciation_entry` is populated per finding.

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

If a legacy heading block is found, remember its line range — apply-mode Pass 2 needs it to migrate the block.

## 1. Number readability (`kind: "number_readability"`)

Numerals get read digit-by-digit or with wrong place values. For monetary amounts and percentages spoken aloud, replacing `100 TL` with `yüz lira` is a textual correction (use `fix_kind: "replace"`). For phone numbers, IBANs, postal codes, the model needs a separate instruction (use `advisory`).

| Pattern | fix_kind | Example fix |
|---|---|---|
| Currency: `100 TL`, `8.100 TL`, `₺1.250,50` | `replace` | `yüz lira`, `sekiz bin yüz lira` |
| Percentage: `%25` | `replace` | `yüzde yirmi beş` |
| Date: `17/05/2026` | `replace` | `on yedi Mayıs iki bin yirmi altı` |
| Time: `14:30` | `replace` | `on dört otuz` |
| Phone, IBAN, posta kodu | `advisory` | Add an instruction near the line |
| Ordinals: `5.`, `21.` | `replace` | `beşinci`, `yirmi birinci` |

### Malformed Turkish numbers (`replace`, severity `medium` or `high`)

These slip past spell-checkers but TTS reads them literally — flag them when the prompt is in Turkish and the body is meant to be spoken.

| Pattern | Why it's wrong | Suggested fix |
|---|---|---|
| `bir bin` followed by `yüz`/`...` | "bin" already means 1000; "bir bin" reads as ~1001. The author meant just `bin`. | Replace `bir bin` with `bin` |
| `bir milyon`, `bir milyar` | Same root issue: redundant `bir`. | Replace with `milyon` / `milyar` (context-dependent) |
| `içinz`, `içiniz` mid-sentence where `için` was meant | Typo; TTS reads "ee-cheen-z" / similar. | Replace with `için` |
| Number-word with no space before unit (`onbin`, `yüzlira`) | TTS may concatenate or hesitate. | Insert space: `on bin`, `yüz lira` |

## 2. Abbreviations & technical terms (`kind: "abbreviation"`)

Default behaviour: **do not flag**. Most Turkish acronyms are read acceptably letter-by-letter. Flag only when the abbreviation is in the **curated risky list**.

**Curated risky (flag as `pronunciation_hint`):**

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

Flag as `pronunciation_hint` with `strategy: "pronounce"` (default) or `"follow_with_translation"` when a Turkish gloss is conventional.

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

These are hard to phonetic-guess confidently. Emit `advisory` rather than auto-generating phonetic spellings — the author knows the intended reading. Optionally suggest the `rephrase` strategy with no phonetic, letting the author add details. Heuristic for detection: repeated proper noun across multiple lines, non-Turkish morphology (consonant clusters like `Pala-`, `Ius-`, `Nea`, or prefixes like `La `), in historical / cultural text.

## 4. Punctuation & pacing (`kind: "punctuation"`)

| Pattern | fix_kind | Notes |
|---|---|---|
| Double space before comma, double commas, stray punctuation | `replace` | Obvious typo |
| Sentence > 120 chars with no comma / period | `replace` if break point is obvious; else `advisory` | Suggest specific comma position |
| Sentence > 80 chars with one or zero commas | `advisory` | `low` unless voice context is critical |
| Imperative ending in `.` where `?` or `…` would match prosody | `advisory` | Author's judgement |
| Long enumeration without "birincisi / ikincisi" cues | `advisory` | Pacing nuance |

## Skip rules — apply BEFORE detection

Do not generate findings for any line that matches any of these:

- **Pronunciation-context line** — contains `okunuş`, `telaffuz`, `oku:`, `şöyle oku`, `diye okunur`, `harf harf`, `→`, `->`, `phonetic`, `pronunciation`.
- **Internal-notes block** — line is under a heading containing `INTERNAL`, `INTERNAL NOTES`, `do not speak`, `okunmaz`, `söylenmez`.
- **Code / config** — line is inside a markdown code fence, inline `<code>`, YAML frontmatter, or a JSON / JS block.
- **Quoted transcript** — line is inside a multi-line quoted block.
- **Tabular content** — markdown table rows (`| ... |`).
- **Inside any pronunciation block** (managed marker block OR legacy heading block detected in Phase 6.0) — the block content is the canonical source, not a target for new findings.

If a line satisfies any skip rule, no finding is emitted regardless of detection match.

## What this lens does NOT do

- Does not check ünlü uyumu (vowel harmony) — grammar, not TTS.
- Does not flag colloquialisms or dialect.
- Does not translate. Foreign words stay verbatim by default.
- Does not run regex on Latin technical terms inside code blocks or model identifiers.
- Does not flag Turkish-friendly abbreviations (`PTT`, `KDV`, `SGK`, `MEB`).
- Does not generate findings for lines that are themselves pronunciation instructions.
- Does not invent phonetic spellings for obscure proper nouns — flags them as `advisory` for the author to resolve.
