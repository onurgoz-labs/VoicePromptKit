import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { writeMarkdownReport } from '../../lib/reporters/markdown';
import type { Report } from '../../lib/types';

const report: Report = {
  prompt_path: '/p.md', target_model: 'claude-opus-4-7',
  rules: [{ id: 'R1', category: 'tone', text: 'be formal', line: 1, source_excerpt: 'x' }],
  conflicts: [{ id: 'C1', rule_ids: ['R1', 'R2'], severity: 'high', reasoning: 'r' }],
  dominances: [], gaps: [],
  scenarios: [{ id: 'S1', kind: 'normal', input: 'hi', assertions: [], rubric: 'be nice' }],
  runs: [{ scenario_id: 'S1', output: 'hello', tokens: { input: 1, output: 1 }, latency_ms: 10, model: 'x', provider: 'anthropic' }],
  verdicts: [{ scenario_id: 'S1', pass: true, score: 1, reasons: ['ok'], violated_assertions: [] }],
  summary: { total_scenarios: 1, passed: 1, failed: 0, high_severity_findings: 1 },
  generated_at: '2026-05-14T00:00:00Z',
};

describe('writeMarkdownReport', () => {
  let dir: string;
  beforeEach(() => { dir = mkdtempSync(join(tmpdir(), 'pc-')); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it('writes report with all sections', async () => {
    const path = join(dir, 'r.md');
    await writeMarkdownReport(report, { outputPath: path });
    const md = readFileSync(path, 'utf8');
    expect(md).toContain('# PromptChecker Report');
    expect(md).toContain('## Rules');
    expect(md).toContain('## Conflicts');
    expect(md).toContain('## Test Matrix');
    expect(md).toContain('mermaid');
    expect(md).toContain('be formal');
  });
});
