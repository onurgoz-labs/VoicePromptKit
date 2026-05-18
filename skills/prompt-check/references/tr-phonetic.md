# Turkish phonetic lens — reference

Active only when `frontmatter.tr_phonetic == true`. Voice agents (Vapi, ElevenLabs, OpenAI Realtime, etc.) read text aloud; constructs that look fine in writing may be mispronounced or sound robotic.

The lens emits **three kinds of finding** so apply-mode can act correctly:

| `fix_kind` | When to use | Apply-mode behaviour |
|---|---|---|
| `replace` | Real textual error (typo, double space, missing/extra punctuation) | Substring replace `current_excerpt` → `suggested_fix` |
| `pronunciation_hint` | Foreign word, abbreviation, brand name — the **written text must stay**, only the TTS reading needs help | Add a `pronunciation_entry` to a guide block in the prompt; do not touch the original line |
| `advisory` | Borderline / judgement call (long unparseable sentence, possible mistranslation) | Reported only; no automatic apply |

Common finding shape:

```json
{
  "id": "T1",
  "kind": "number_readability | abbreviation | foreign_word | punctuation",
  "fix_kind": "replace | pronunciation_hint | advisory",
  "severity": "low | medium | high",
  "line": 42,
  "current_excerpt": "...",
  "suggested_fix": "...",          // populated only for fix_kind = replace
  "pronunciation_entry": null,      // populated only for fix_kind = pronunciation_hint
  "rationale": "..."
}
```

`pronunciation_entry` schema (4 fields, last two nullable):

```json
{
  "term": "DHL",
  "phonetic": "de-ha-el",
  "alt_translation": null,    // a genuine Turkish alternative if one exists (e.g. "İbrani" for "Hebrew"); else null
  "note": null                // optional usage hint (e.g. "use on first mention only"); else null
}
```

Severity guide:
- **high** — TTS will mispronounce or skip; meaning lost.
- **medium** — TTS reads it but unnaturally; listener may hesitate.
- **low** — cosmetic / preference.

## Hard rule: never translate

This lens is about **how Turkish TTS reads text**, not about Turkish vocabulary substitution. `pound → paund` is a phonetic hint (allowed). `pound → İngiliz lirası` is a semantic translation (forbidden — the brand / domain meaning is the author's call, not the lens's).

The only exception is `alt_translation` inside a `pronunciation_entry`, which **suggests** a Turkish alternative without forcing it. The original term still stays in the prompt; the alternative is metadata.

## 1. Number readability (`kind: "number_readability"`)

Numerals get read digit-by-digit or with wrong place values by many Turkish TTS engines. For monetary amounts and percentages spoken in real conversation, replacing `100 TL` with `yüz lira` is a textual correction (use `fix_kind: "replace"`). For phone numbers, IBANs, postal codes, etc., the model needs an *instruction* to read them digit-by-digit (use `fix_kind: "advisory"` — there is no clean automatic fix).

| Pattern | fix_kind | Suggested rewrite |
|---|---|---|
| Currency: `100 TL`, `8.100 TL`, `₺1.250,50` | `replace` | `yüz lira`, `sekiz bin yüz lira`, `bin iki yüz elli lira elli kuruş` |
| Percentage: `%25`, `%40 avantaj` | `replace` | `yüzde yirmi beş`, `yüzde kırk avantaj` |
| Date: `17/05/2026`, `17.05.2026`, `2026-05-17` | `replace` | `on yedi Mayıs iki bin yirmi altı` |
| Time: `14:30`, `09:00` | `replace` | `on dört otuz`, `dokuzda` |
| Phone, IBAN, posta kodu | `advisory` | Add an instruction near the line: "telefon numarasını rakam rakam, ikişerli oku" |
| Large numbers with units | `replace` | `bin yedi yüz elli sekiz satır`, `elli bin kullanıcı` |
| Ordinals: `5.`, `21.` | `replace` | `beşinci`, `yirmi birinci` |

**Detection heuristic:** scan body for `\d+[.,]?\d*\s*(TL|₺|lira|%|saat|dakika|gün|ay|yıl|adet|kişi)` and date/time/phone/IBAN regexes. Flag any numeric span longer than two digits that is followed by a unit or precedes a noun.

## 2. Abbreviations & technical terms (`kind: "abbreviation"`)

Default behaviour: **do not flag**. Turkish TTS handles most acronyms acceptably letter-by-letter, and false positives here are the lens's biggest source of noise.

Flag only when the abbreviation is in the **curated risky list**, or when the prompt explicitly requires layperson expansion.

**Curated risky abbreviations (flag as `pronunciation_hint`):**

| Term | Phonetic | Notes |
|---|---|---|
| `DHL` | `de-ha-el` | English-trained TTS often reads "D-H-L" |
| `SMS` | `se-me-se` | Often read as English "es-em-es" |
| `OTP` | `o-te-pe` | Often anglicised |
| `URL` | `u-er-le` | Anglicisation common |
| `WhatsApp` | `votsap` | Common brand |
| `iPhone` | `ay-fon` | Common brand |
| `D&R`, `S&P`, `H&M` (`X&Y` brands) | `de ve er`, etc. | `&` symbol read literally as "and" |
| `pound`, `euro`, `dollar` | `paund`, `oyro`, `dolar` | Foreign currency words |
| `A.Ş.` | (`alt_translation: "anonim şirketi"`) | Letter-by-letter read is OK but cluttered; optional expansion |
| `T.C.` | (`alt_translation: "Türkiye Cumhuriyeti"`) | Same — optional expansion |

**Whitelist — explicitly DO NOT flag:**

- `PTT`, `KDV`, `ÖTV`, `SGK`, `MEB`, `TBMM`, `TRT` — TR TTS reads these fine.
- Single-letter qualifiers: `A Blok`, `B kapısı`, `C segmenti`, `D vitamini` — TTS reads single letters acceptably in context.
- Latin technical acronyms inside `<code>` or markdown code fences (CRM, ERP, API) — those are config / documentation, not speech.
- Any line that is itself **about pronunciation**: see the next section.

## 3. Foreign words & transliteration (`kind: "foreign_word"`)

Brand and loanword pronunciation drifts wildly across TTS engines. The lens flags these as `pronunciation_hint` — the written text stays, the entry goes into the pronunciation guide block.

| Word | phonetic | alt_translation |
|---|---|---|
| `iPhone` | `ay-fon` | null |
| `WhatsApp` | `votsap` | null |
| `Wi-Fi` | `vay-fay` | null |
| `email` | `i-meyl` | `e-posta` |
| `check-in` | `çek-in` | `giriş işlemi` |
| `YouTube` | `yu-tüb` | null |
| `Google` | `gugıl` | null |
| `Microsoft` | `mayk-ro-soft` | null |
| `Hebrew` | `hebru` | `İbrani` |
| `pound`, `euro`, `dollar` | (see abbreviations table) | null |

**Detection heuristic:** flag tokens that contain ASCII Latin letters and at least one of `{w, q, x}` or end in `-ing`, `-tion`, `-ment`, `-ly`. Exclude common Turkish-friendly tokens (URLs, code snippets, model names like `claude-opus-4-7`).

Severity: usually `medium`; `high` only when the brand is central to the dialogue and mispronunciation breaks user trust; `low` for incidental references.

## 4. Punctuation & pacing (`kind: "punctuation"`)

Voice agents must breathe. Long unpunctuated sentences sound robotic.

| Pattern | fix_kind | Notes |
|---|---|---|
| Double space before comma, double commas, stray punctuation | `replace` | Obvious typo |
| Sentence > 120 chars with no comma / period | `replace` if break point is obvious; else `advisory` | Suggest specific comma position |
| Sentence > 80 chars with one or zero commas | `advisory` | Severity `low` unless voice context is critical |
| Imperative ending in `.` where `?` or `…` would match prosody | `advisory` | Author's judgement |
| Long enumeration without "birincisi / ikincisi / üçüncüsü" cues | `advisory` | Pacing nuance |

**Detection heuristic:** sentence-tokenise on `.!?` (carefully — `A.Ş.`, `T.C.` are not terminators); compute char-length, comma-count, sentence-initial verb form.

## Skip rules (apply BEFORE detection — false positive guard)

Do not flag any line that matches any of these:

- **Pronunciation-context line** — contains any of: `okunuş`, `telaffuz`, `oku:`, `şöyle oku`, `diye okunur`, `harf harf`, `→`, `->`, `phonetic`, `pronunciation`.
- **Internal-notes block** — line is under a heading containing `INTERNAL`, `INTERNAL NOTES`, `TTS NOTES`, `do not speak`, `okunmaz`, `söylenmez`.
- **Code / config** — line is inside a markdown code fence (` ``` `), inline `<code>`, YAML frontmatter, or a JSON / JS code block.
- **Quoted transcript** — line is inside a multi-line quoted block (lines beginning with `>` or wrapped in fenced quote markers) where pacing decisions are deliberate.
- **Tabular content** — markdown table rows (`| ... |`) — too constrained to rewrite line-anchored.
- **Already-curated pronunciation guide** — the existing block this lens manages (between the markers `<!-- promptchecker:pronunciation-guide:start -->` and `<!-- end -->`).

If a line satisfies any skip rule, no finding is emitted regardless of which detection heuristic matched.

## What this lens does NOT do

- Does not check ünlü uyumu (vowel harmony) — grammar, not TTS.
- Does not flag colloquialisms or dialect — those are intentional in voice agents.
- Does not translate. Foreign words stay verbatim. `pronunciation_entry.alt_translation` is a suggestion, not a forced replacement.
- Does not run regex on Latin technical terms inside code blocks or model identifiers (`claude-opus-4-7`).
- Does not flag abbreviations Turkish TTS reads correctly (`PTT`, `KDV`, `SGK`, `MEB`) unless the prompt explicitly requires layperson expansion.
- Does not generate findings for lines that are themselves pronunciation instructions (the lens reads them as already-handled).
