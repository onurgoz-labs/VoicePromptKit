import { readFile } from 'node:fs/promises';
import { writeJsonReport } from './reporters/json';
import { writeMarkdownReport } from './reporters/markdown';
import { writeHtmlReport } from './reporters/html';
import { writeInlineReport } from './reporters/inline';
import type { Report, OutputFormat } from './types';

async function main(): Promise<void> {
  const promptPath = process.argv[2];
  if (!promptPath) throw new Error('Usage: report.ts <prompt-path>');
  const fm = JSON.parse(await readFile('.promptcheck/.tmp/frontmatter.json', 'utf8')) as { output: OutputFormat[]; target_model: string; type?: string };
  const rules = JSON.parse(await readFile('.promptcheck/.tmp/rules.json', 'utf8')).rules;
  const conflicts = JSON.parse(await readFile('.promptcheck/.tmp/conflicts.json', 'utf8')).conflicts;
  const dominances = JSON.parse(await readFile('.promptcheck/.tmp/dominances.json', 'utf8')).dominances;
  const gaps = JSON.parse(await readFile('.promptcheck/.tmp/gaps.json', 'utf8')).gaps;
  const scenarios = JSON.parse(await readFile('.promptcheck/.tmp/scenarios.json', 'utf8')).scenarios;
  const runs = JSON.parse(await readFile('.promptcheck/.tmp/runs.json', 'utf8')).runs;
  const verdicts = JSON.parse(await readFile('.promptcheck/.tmp/verdicts.json', 'utf8')).verdicts;

  const report: Report = {
    prompt_path: promptPath,
    prompt_type: fm.type as never,
    target_model: fm.target_model,
    rules, conflicts, dominances, gaps, scenarios, runs, verdicts,
    summary: {
      total_scenarios: scenarios.length,
      passed: verdicts.filter((v: { pass: boolean }) => v.pass).length,
      failed: verdicts.filter((v: { pass: boolean }) => !v.pass).length,
      high_severity_findings: conflicts.filter((c: { severity: string }) => c.severity === 'high').length
        + gaps.filter((g: { severity: string }) => g.severity === 'high').length,
    },
    generated_at: new Date().toISOString(),
  };

  const formats = new Set<OutputFormat>(fm.output);
  const base = `.promptcheck/${basename(promptPath)}-${new Date().toISOString().slice(0, 10)}`;
  const written: string[] = [];
  if (formats.has('json')) { const p = `${base}.json`; await writeJsonReport(report, { outputPath: p }); written.push(p); }
  if (formats.has('markdown')) { const p = `${base}.md`; await writeMarkdownReport(report, { outputPath: p }); written.push(p); }
  if (formats.has('html')) { const p = `${base}.html`; await writeHtmlReport(report, { outputPath: p }); written.push(p); }
  if (formats.has('inline')) { await writeInlineReport(report, { promptPath }); written.push(promptPath); }
  process.stdout.write(JSON.stringify({ written, summary: report.summary }));
}

function basename(p: string): string {
  return p.split('/').pop()!.replace(/\.[^.]+$/, '');
}

void main();
