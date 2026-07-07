# Render reference — TEMPLATE_STRINGS + table helpers

Read by the `prompt-check` skill at Phase 7 entry (and reused by Phase 9.2's summary view). This file owns the bilingual template-string dictionary and the Python helpers that render the findings table. `SKILL.md` Phase 7 owns the control flow (artefact merging, line translation, section_ref attachment, sort order); nothing here changes those rules.

## TEMPLATE_STRINGS

`frontmatter.report_language` (`tr` | `en`) is the key. Only these skill-side strings translate — lens-generated content (`rationale`, `suggested_fix`, `current_excerpt`) is NEVER translated and stays in whatever language the runner produced.

```
TEMPLATE_STRINGS = {
  "tr": {
    "report_title": "VoicePromptKit Raporu — {basename}",
    "prompt_label": "**Prompt:**",
    "run_label": "**Çalıştırma:**",
    "generated_label": "**Oluşturulma:**",
    "target_model_label": "**Hedef model:**",
    "summary_heading": "## Özet",
    "lens_column": "Mercek",
    "total_column": "Toplam",
    "high_column": "Yüksek",
    "medium_column": "Orta",
    "low_column": "Düşük",
    "conflict_row": "Çelişki",
    "dominance_row": "Baskınlık",
    "gap_row": "Boşluk",
    "schema_row": "Şema",
    "drift_row": "Davranışsal sapma",
    "tr_phonetic_row": "Türkçe fonetik",
    "findings_heading": "## Bulgular",
    "high_severity_heading": "### Yüksek önem",
    "medium_severity_heading": "### Orta önem",
    "low_severity_heading": "### Düşük önem",
    "conflicts_subheading": "#### Çelişkiler",
    "dominances_subheading": "#### Baskınlıklar",
    "gaps_subheading": "#### Boşluklar",
    "schema_subheading": "#### Şema",
    "drift_subheading": "#### Davranışsal sapma",
    "tr_phonetic_subheading": "#### Türkçe fonetik",
    "none_marker": "_(yok)_",
    "section_prefix": "Bölüm",
    "line_prefix": "Satır",
    "lens_label_conflict": "çelişki",
    "lens_label_dominance": "baskınlık",
    "lens_label_gap": "boşluk",
    "lens_label_schema": "şema",
    "lens_label_drift": "davranışsal sapma",
    "lens_label_tr_phonetic": "türkçe fonetik",
    "severity_label_high": "yüksek",
    "severity_label_medium": "orta",
    "severity_label_low": "düşük",
    "table_id_column": "id",
    "table_lens_column": "mercek",
    "table_severity_column": "önem",
    "table_section_line_column": "bölüm / satır",
    "table_rationale_column": "açıklama",
    "table_fix_column": "düzeltme",
    "drift_passed_no_fix": "(geçti — düzeltme yok)",
    "sentinel_todo_render": "_TODO: {text}_",
    "sentinel_intentional_render": "_Intentional — atla_",
    "no_line_marker": "— / —",
    "no_section_marker": "—"
  },
  "en": {
    "report_title": "VoicePromptKit Report — {basename}",
    "prompt_label": "**Prompt:**",
    "run_label": "**Run:**",
    "generated_label": "**Generated:**",
    "target_model_label": "**Target model:**",
    "summary_heading": "## Summary",
    "lens_column": "Lens",
    "total_column": "Total",
    "high_column": "High",
    "medium_column": "Medium",
    "low_column": "Low",
    "conflict_row": "Conflict",
    "dominance_row": "Dominance",
    "gap_row": "Gap",
    "schema_row": "Schema",
    "drift_row": "Drift",
    "tr_phonetic_row": "TR phonetic",
    "findings_heading": "## Findings",
    "high_severity_heading": "### HIGH severity",
    "medium_severity_heading": "### MEDIUM severity",
    "low_severity_heading": "### LOW severity",
    "conflicts_subheading": "#### Conflicts",
    "dominances_subheading": "#### Dominances",
    "gaps_subheading": "#### Gaps",
    "schema_subheading": "#### Schema",
    "drift_subheading": "#### Drift",
    "tr_phonetic_subheading": "#### TR phonetic",
    "none_marker": "_None._",
    "section_prefix": "Section",
    "line_prefix": "L",
    "lens_label_conflict": "conflict",
    "lens_label_dominance": "dominance",
    "lens_label_gap": "gap",
    "lens_label_schema": "schema",
    "lens_label_drift": "drift",
    "lens_label_tr_phonetic": "tr phonetic",
    "severity_label_high": "high",
    "severity_label_medium": "medium",
    "severity_label_low": "low",
    "table_id_column": "id",
    "table_lens_column": "lens",
    "table_severity_column": "sev",
    "table_section_line_column": "section / line",
    "table_rationale_column": "rationale",
    "table_fix_column": "fix",
    "drift_passed_no_fix": "(passed — no fix)",
    "sentinel_todo_render": "_TODO: {text}_",
    "sentinel_intentional_render": "_Intentional — dismiss_",
    "no_line_marker": "— / —",
    "no_section_marker": "—"
  }
}
```

## Python render helpers

Used by Phase 7's report.md findings table and Phase 9.2's summary view. Cells are populated VERBATIM (no truncation); pipes and newlines are escaped for markdown-table safety only — `findings.json` keeps the raw text.

```python
def escape_cell(text):
    if not text:
        return ""
    return text.replace("|", "\\|").replace("\n", "<br>")

def build_section_line_cell(f, T):
    sref = f.get("section_ref")
    line = f.get("line")
    # Drift "— / —" rule: both line and section_ref are null.
    if line is None and sref is None:
        return T["no_line_marker"]
    sec_p = T["section_prefix"]
    line_p = T["line_prefix"]
    if sref is not None and sref.get("subsection"):
        return f"{sec_p} {sref['subsection']} / {line_p}{line}"
    if sref is not None and sref.get("section"):
        return f"{sec_p} {sref['section']} / {line_p}{line}"
    # section_ref null but line present (preamble line)
    return f"{line_p}{line}"

def render_fix_cell(f, T):
    suggested = f.get("suggested_fix") or ""
    if not suggested.strip():
        if f.get("lens") == "drift":
            return T["drift_passed_no_fix"]
        return ""
    if suggested.startswith("TODO:"):
        return T["sentinel_todo_render"]
    if suggested.startswith("Intentional —") or suggested.startswith("Intentional -"):
        return T["sentinel_intentional_render"]
    return suggested  # verbatim — no truncation

def render_finding_row(f, lang):
    T = TEMPLATE_STRINGS[lang]
    lens_label = T[f"lens_label_{f['lens']}"]
    sev_label = T[f"severity_label_{f.get('severity','low')}"]
    section_line = build_section_line_cell(f, T)
    rationale = escape_cell(f.get("rationale", ""))
    fix = escape_cell(render_fix_cell(f, T))
    return f"| {f['id']} | {lens_label} | {sev_label} | {section_line} | {rationale} | {fix} |"

def render_findings_table(findings, lang):
    T = TEMPLATE_STRINGS[lang]
    header = (
        f"| {T['table_id_column']} | {T['table_lens_column']} | {T['table_severity_column']} "
        f"| {T['table_section_line_column']} | {T['table_rationale_column']} | {T['table_fix_column']} |"
    )
    sep = "|---|---|---|---|---|---|"
    rows = [render_finding_row(f, lang) for f in findings]
    return "\n".join([header, sep] + rows)
```
