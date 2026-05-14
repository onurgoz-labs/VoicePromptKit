import { readFile } from 'node:fs/promises';
import { writeJsonReport } from './reporters/json';
import { writeMarkdownReport } from './reporters/markdown';
import { writeHtmlReport } from './reporters/html';
import { writeInlineReport } from './reporters/inline';
import type { Report, OutputFormat } from './types';

const TMP = '.promptcheck/.tmp';

async function loadOptional<T>(path: string, fallback: T): Promise<T> {
  try {
    return JSON.parse(await readFile(path, 'utf8'));
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return fallback;
    process.stderr.write(`[report] ${path} malformed, falling back: ${(err as Error).message}\n`);
    return fallback;
  }
}

async function main(): Promise<void> {
  const promptPath = process.argv[2];
  if (!promptPath) throw new Error('Usage: report.ts <prompt-path>');
  const fm = await loadOptional(`${TMP}/frontmatter.json`, { output: ['inline'] as OutputFormat[], target_model: 'unknown', type: undefined as string | undefined });
  const rules = (await loadOptional<{ rules: unknown[] }>(`${TMP}/rules.json`, { rules: [] })).rules;
  const conflicts = (await loadOptional<{ conflicts: unknown[] }>(`${TMP}/conflicts.json`, { conflicts: [] })).conflicts;
  const dominances = (await loadOptional<{ dominances: unknown[] }>(`${TMP}/dominances.json`, { dominances: [] })).dominances;
  const gaps = (await loadOptional<{ gaps: unknown[] }>(`${TMP}/gaps.json`, { gaps: [] })).gaps;
  const scenarios = (await loadOptional<{ scenarios: unknown[] }>(`${TMP}/scenarios.json`, { scenarios: [] })).scenarios;
  const runs = (await loadOptional<{ runs: unknown[] }>(`${TMP}/runs.json`, { runs: [] })).runs;
  const verdicts = (await loadOptional<{ verdicts: unknown[] }>(`${TMP}/verdicts.json`, { verdicts: [] })).verdicts;

  const report: Report = {
    prompt_path: promptPath,
    prompt_type: fm.type as never,
    target_model: fm.target_model,
    rules: rules as never,
    conflicts: conflicts as never,
    dominances: dominances as never,
    gaps: gaps as never,
    scenarios: scenarios as never,
    runs: runs as never,
    verdicts: verdicts as never,
    summary: {
      total_scenarios: scenarios.length,
      passed: (verdicts as Array<{ pass: boolean }>).filter((v) => v.pass).length,
      failed: (verdicts as Array<{ pass: boolean }>).filter((v) => !v.pass).length,
      high_severity_findings: (conflicts as Array<{ severity: string }>).filter((c) => c.severity === 'high').length
        + (gaps as Array<{ severity: string }>).filter((g) => g.severity === 'high').length,
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

if (import.meta.url === `file://${process.argv[1]}`) {
  void main();
}
