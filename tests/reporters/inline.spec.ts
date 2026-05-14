import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { writeInlineReport } from '../../lib/reporters/inline';
import type { Report, Conflict } from '../../lib/types';

describe('writeInlineReport', () => {
  let dir: string;
  beforeEach(() => { dir = mkdtempSync(join(tmpdir(), 'pc-')); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it('inserts conflict annotation above offending line', async () => {
    const path = join(dir, 'p.md');
    writeFileSync(path, '---\ntype: system\n---\nLine A\nLine B\nLine C\n', 'utf8');
    const report = makeReport(path, [
      { id: 'C1', rule_ids: ['R1', 'R2'], severity: 'high', reasoning: 'A vs B' },
    ]);
    await writeInlineReport(report, { promptPath: path });
    const out = readFileSync(path, 'utf8');
    expect(out).toMatch(/<!-- PROMPTCHECK \[CONFLICT severity=high\]/);
    expect(out).toContain('Line A');
  });

  it('is idempotent: running twice produces same result', async () => {
    const path = join(dir, 'p.md');
    writeFileSync(path, '---\ntype: system\n---\nLine A\n', 'utf8');
    const report = makeReport(path, [
      { id: 'C1', rule_ids: ['R1', 'R2'], severity: 'low', reasoning: 'x' },
    ]);
    await writeInlineReport(report, { promptPath: path });
    const a = readFileSync(path, 'utf8');
    await writeInlineReport(report, { promptPath: path });
    const b = readFileSync(path, 'utf8');
    expect(a).toBe(b);
  });
});

function makeReport(path: string, conflicts: Conflict[]): Report {
  return {
    prompt_path: path, target_model: 'claude-opus-4-7',
    rules: [
      { id: 'R1', category: 'tone', text: 'be formal', line: 1, source_excerpt: 'Line A' },
      { id: 'R2', category: 'tone', text: 'be casual', line: 2, source_excerpt: 'Line B' },
    ],
    conflicts, dominances: [], gaps: [],
    scenarios: [], runs: [], verdicts: [],
    summary: { total_scenarios: 0, passed: 0, failed: 0, high_severity_findings: 0 },
    generated_at: '2026-05-14T00:00:00Z',
  };
}
