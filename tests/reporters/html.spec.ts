import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { writeHtmlReport } from '../../lib/reporters/html';
import type { Report } from '../../lib/types';

const report: Report = {
  prompt_path: '/p.md', target_model: 'x',
  rules: [], conflicts: [], dominances: [], gaps: [],
  scenarios: [], runs: [], verdicts: [],
  summary: { total_scenarios: 0, passed: 0, failed: 0, high_severity_findings: 0 },
  generated_at: '2026-05-14T00:00:00Z',
};

describe('writeHtmlReport', () => {
  let dir: string;
  beforeEach(() => { dir = mkdtempSync(join(tmpdir(), 'pc-')); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it('produces self-contained HTML', async () => {
    const path = join(dir, 'r.html');
    await writeHtmlReport(report, { outputPath: path });
    const html = readFileSync(path, 'utf8');
    expect(html).toContain('<!doctype html>');
    expect(html).toContain('<style>');
    expect(html).not.toContain('http://');
    expect(html).not.toContain('https://cdn');
  });
});
