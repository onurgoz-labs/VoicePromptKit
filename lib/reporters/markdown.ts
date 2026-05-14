import { writeFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';
import type { Report } from '../types';

export async function writeMarkdownReport(report: Report, opts: { outputPath: string }): Promise<void> {
  await mkdir(dirname(opts.outputPath), { recursive: true });
  await writeFile(opts.outputPath, renderMarkdown(report), 'utf8');
}

export function renderMarkdown(report: Report): string {
  const lines: string[] = [];
  lines.push(`# PromptChecker Report`);
  lines.push(``);
  lines.push(`- **Prompt:** \`${report.prompt_path}\``);
  lines.push(`- **Target model:** ${report.target_model}`);
  lines.push(`- **Generated:** ${report.generated_at}`);
  lines.push(``);
  lines.push(`## Summary`);
  lines.push(``);
  const s = report.summary;
  lines.push(`| Metric | Count |`);
  lines.push(`|---|---|`);
  lines.push(`| Rules | ${report.rules.length} |`);
  lines.push(`| Conflicts | ${report.conflicts.length} (${report.conflicts.filter((c) => c.severity === 'high').length} high) |`);
  lines.push(`| Dominances | ${report.dominances.length} |`);
  lines.push(`| Gaps | ${report.gaps.length} |`);
  lines.push(`| Scenarios | ${s.total_scenarios} (passed ${s.passed}, failed ${s.failed}) |`);
  lines.push(``);
  lines.push(`## Rules`);
  lines.push(``);
  lines.push(`| ID | Cat | Line | Text |`);
  lines.push(`|---|---|---|---|`);
  for (const r of report.rules) lines.push(`| ${r.id} | ${r.category} | ${r.line} | ${escapePipe(r.text)} |`);
  lines.push(``);
  lines.push(`## Conflicts`);
  if (report.conflicts.length) {
    lines.push(``);
    lines.push('```mermaid');
    lines.push('graph LR');
    for (const c of report.conflicts) {
      for (let i = 0; i < c.rule_ids.length - 1; i++) {
        lines.push(`  ${c.rule_ids[i]} ---|${c.severity}| ${c.rule_ids[i + 1]}`);
      }
    }
    lines.push('```');
    lines.push(``);
    for (const c of report.conflicts) lines.push(`- **${c.id}** (${c.severity}) rules ${c.rule_ids.join(', ')}: ${c.reasoning}`);
  } else lines.push(`_None._`);
  lines.push(``);
  lines.push(`## Dominances`);
  if (report.dominances.length) {
    for (const d of report.dominances) lines.push(`- **${d.id}** ${d.dominant_rule_id} > ${d.dominated_rule_id} (${d.mechanism}): ${d.reasoning}`);
  } else lines.push(`_None._`);
  lines.push(``);
  lines.push(`## Gaps`);
  if (report.gaps.length) {
    for (const g of report.gaps) lines.push(`- **${g.id}** (${g.severity}): ${g.description}`);
  } else lines.push(`_None._`);
  lines.push(``);
  lines.push(`## Test Matrix`);
  lines.push(``);
  lines.push(`| Scenario | Kind | Pass | Score | Reasons |`);
  lines.push(`|---|---|---|---|---|`);
  for (const sc of report.scenarios) {
    const v = report.verdicts.find((x) => x.scenario_id === sc.id);
    lines.push(`| ${sc.id} | ${sc.kind} | ${v?.pass ? '✅' : '❌'} | ${(v?.score ?? 0).toFixed(2)} | ${escapePipe((v?.reasons ?? []).join('; '))} |`);
  }
  return lines.join('\n') + '\n';
}

function escapePipe(s: string): string {
  return s.replace(/\|/g, '\\|').replace(/\n/g, ' ');
}
