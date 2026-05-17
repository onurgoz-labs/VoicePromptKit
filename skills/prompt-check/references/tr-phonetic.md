# Turkish phonetic lens — reference

Active only when `frontmatter.tr_phonetic == true`. Voice agents (Vapi, ElevenLabs, OpenAI Realtime, etc.) read text aloud; constructs that look fine in writing may be mispronounced or sound robotic. This lens flags four classes of issue and proposes concrete rewrites.

All findings use this shape (line-anchored, same as static lenses):

```json
{
  "id": "T1",
  "kind": "number_readability | abbreviation | foreign_word | punctuation",
  "severity": "low|medium|high",
  "line": 42,
  "current_excerpt": "...",
  "suggested_fix": "...",
  "rationale": "..."
}
```

Severity guide:
- **high** — TTS will mispronounce or skip; meaning lost.
- **medium** — TTS reads it but unnaturally; listener may hesitate.
- **low** — cosmetic / preference.

## 1. Number readability (`kind: "number_readability"`)

Numerals in text get read digit-by-digit or with wrong place values by many Turkish TTS systems. The fix is to spell numbers out **when they are spoken to the user**, leaving raw digits only when the model is supposed to record / transmit them (booking refs, phone numbers, IBANs).

| Pattern | Risk | Suggested rewrite |
|---|---|---|
| Currency: `100 TL`, `8.100 TL`, `₺1.250,50` | TTS reads "yüz te-le" or "sekiz bin yüz te-le" — never "te-le" should be spoken | `yüz lira`, `sekiz bin yüz lira`, `bin iki yüz elli lira elli kuruş` |
| Percentage: `%25`, `%40 avantaj` | "yüzde işareti yirmi beş" (literal symbol-read) | `yüzde yirmi beş`, `yüzde kırk avantaj` |
| Date: `17/05/2026`, `17.05.2026`, `2026-05-17` | Slash/dot read literally; year read digit-by-digit | `on yedi Mayıs iki bin yirmi altı` or `on yedi Mayıs` if year obvious |
| Time: `14:30`, `09:00` | Colon read as "iki nokta üst üste" | `on dört otuz`, `dokuzda` |
| Phone: `+90 532 123 45 67`, `0532 123 45 67` | Many TTS read groups; the model should announce phone numbers digit-by-digit *deliberately* | Wrap phone numbers in an instruction: `"telefon numarasını rakam rakam, ikişerli okuyarak söyle: sıfır beş üç iki, bir yirmi üç, kırk beş, altmış yedi"` |
| Large numbers: `1758 satır`, `50.000 kullanıcı` | TTS may say "bin yedi yüz elli sekiz" correctly but `50.000` ↔ `50,000` confusion | `bin yedi yüz elli sekiz satır`, `elli bin kullanıcı` |
| Decimals: `8.100` (TR thousand sep) vs `8,100` (TR decimal sep) | English-trained TTS reverses them | Specify in prompt: "fiyatları TR biçiminde söyle: 8.100 = sekiz bin yüz" |
| Ordinals: `5.`, `21.` | Read as "beş nokta" | `beşinci`, `yirmi birinci` |
| IBAN: `TR12 0001 0012 3456 7890 1234 56` | Group read as decimals | `İBAN'ı 4'erli gruplar halinde, harfleri tek tek söyle` |
| Posta kodu: `34394` | Five-digit numbers read as 34 bin 394 | `posta kodunu rakam rakam söyle: üç-dört-üç-dokuz-dört` |

**Detection heuristic:** scan body for `\d+[.,]?\d*\s*(TL|₺|lira|%|saat|dakika|gün|ay|yıl|adet|kişi)` and date/time/phone/IBAN regexes. Flag any numeric span longer than two digits that is followed by a unit or precedes a noun.

## 2. Abbreviations & technical terms (`kind: "abbreviation"`)

Many Turkish-trained TTS systems read uppercase abbreviations letter-by-letter; some force English pronunciation ("S-M-S" instead of "se-me-se"). Decide per-abbreviation whether the prompt should spell it out, expand it, or leave it.

| Abbreviation | Likely TTS error | Suggested treatment |
|---|---|---|
| `TL` | "te-le" | Replace with `lira` in spoken text |
| `KDV` | "ka-de-ve" (often acceptable) or "K-D-V" | Acceptable if context is finance; expand to `katma değer vergisi` on first mention if audience is general |
| `ÖTV` | similar | Expand to `özel tüketim vergisi` on first mention |
| `A.Ş.` | "a nokta şe nokta" | Replace with `anonim şirketi` or just elide |
| `T.C.` | "te nokta ce nokta" | Replace with `Türkiye Cumhuriyeti` |
| `KVKK` | "ka-ve-ka-ka" | Expand first use: `Kişisel Verilerin Korunması Kanunu (KVKK)` then reuse abbreviation |
| `SMS` | "es-em-es" (English) | Replace with `kısa mesaj` or instruct "SMS'i 'se-me-se' diye oku" |
| `OTP` | "o-te-pe" or "O-T-P" | Replace with `tek seferlik şifre` |
| `OK`, `FAQ`, `URL`, `email` | English read | Replace with `tamam`, `sıkça sorulan sorular`, `bağlantı`, `e-posta` |
| Marka isimleri (Yapı Kredi, Türk Telekom) | usually fine | leave |
| Latin technical (CRM, ERP, API) | letter-by-letter (often desired) | Acceptable; add `"<X>'i tek tek harf olarak oku"` instruction if needed |

**Detection heuristic:** match `\b[A-ZÇĞİÖŞÜ]{2,5}\b` and `\b[A-ZÇĞİÖŞÜ]\.[A-ZÇĞİÖŞÜ]\.\b`. Cross-reference with the table above; flag the rest with severity `low` and ask the prompt author to confirm.

## 3. Foreign words & transliteration (`kind: "foreign_word"`)

Brand and loanword pronunciation drifts wildly across TTS engines. The safe pattern is to give the model an explicit phonetic spelling in parentheses on first mention.

| Word | Suggested rewrite |
|---|---|
| `iPhone` | `iPhone (ay-fon)` |
| `WhatsApp` | `WhatsApp (votsap)` |
| `email` | `e-posta` |
| `check-in` | `çek-in` |
| `Wi-Fi` | `Wi-Fi (vay-fay)` |
| `YouTube` | `YouTube (yu-tüb)` |
| `Google` | `Google (gugıl)` |
| `Microsoft` | `Microsoft (mayk-ro-soft)` — or just leave if audience is technical |
| Generic English word inside Turkish sentence | Suggest the Turkish equivalent, or add phonetic in parentheses |

**Detection heuristic:** flag tokens that contain ASCII Latin letters and at least one of `{w, q, x}` or end in `-ing`, `-tion`, `-ment`, `-ly`. Exclude common Turkish-friendly tokens (URLs, code snippets, model names like `claude-opus-4-7`).

Severity: usually `medium` (TTS will say *something* recognisable); `high` only when the brand is central to the dialogue and mispronunciation breaks the user's trust.

## 4. Punctuation & pacing (`kind: "punctuation"`)

Voice agents must breathe. Long unpunctuated sentences sound robotic and overwhelm listeners.

Flag these patterns:

- **Long sentence:** > 120 characters with no comma, period, or em-dash.
  Suggested fix: split into two sentences or insert a comma at the natural breath point.
- **Long sentence (medium):** > 80 chars with one or zero commas.
  Suggested fix: add a comma; severity `low` unless `target_model` is a voice model.
- **Imperative without ending tone marker:** sentence ends with declarative `.` but starts with imperative verb (`Sor`, `Söyle`, `İste`, `Onayla`). For voice, ending punctuation should match the intended prosody — questions need `?`, soft requests benefit from `…` or `.`.
- **Bare numbers / lists with no enumeration cue:** TTS reads commas as commas. Replace `"1, 2 ve 3"` with `"birincisi …, ikincisi …, üçüncüsü …"` so the model paces.
- **Run-on with `ve`:** more than two `ve` in one sentence → split.

**Detection heuristic:** sentence-tokenise the body on `.!?` (be careful with `A.Ş.`, `T.C.`); compute char-length, comma-count, sentence-initial verb form (capitalised infinitive-stem). Cross-check against the patterns above.

Severity: `medium` by default; `high` if the sentence is in a customer-facing turn (greeting, refusal, escalation) where pacing matters most; `low` for internal-rule text the model paraphrases.

## What this lens does **not** do

- Does not check ünlü uyumu (vowel harmony) — that's a grammar problem, not a TTS problem.
- Does not flag colloquialisms or dialect — those are intentional in voice agents.
- Does not translate. Foreign words in `current_excerpt` stay verbatim; `suggested_fix` adds the phonetic, doesn't replace the original.
- Does not run regex on Latin technical terms inside markdown code blocks or model identifiers (e.g. `claude-opus-4-7`) — those are config, not speech.
