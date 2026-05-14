import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { writeJsonReport } from '../../lib/reporters/json';
import type { Report } from '../../lib/types';

const minimal: Report = {
  prompt_path: '/x.md', target_model: 'claude-opus-4-7',
  rules: [], conflicts: [], dominances: [], gaps: [],
  scenarios: [], runs: [], verdicts: [],
  summary: { total_scenarios: 0, passed: 0, failed: 0, high_severity_findings: 0 },
  generated_at: '2026-05-14T00:00:00Z',
};

describe('writeJsonReport', () => {
  let dir: string;
  beforeEach(() => { dir = mkdtempSync(join(tmpdir(), 'pc-')); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it('writes pretty JSON to outputPath', async () => {
    const path = join(dir, 'r.json');
    await writeJsonReport(minimal, { outputPath: path });
    const parsed = JSON.parse(readFileSync(path, 'utf8'));
    expect(parsed.prompt_path).toBe('/x.md');
  });
});
