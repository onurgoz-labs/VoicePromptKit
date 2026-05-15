import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtempSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { evaluateMechanical } from '../lib/judge';
import { writeMarkdownReport } from '../lib/reporters/markdown';
import { writeInlineReport } from '../lib/reporters/inline';
import { parsePromptFile } from '../lib/frontmatter';
import type { Scenario, Run, Report } from '../lib/types';

describe('end-to-end (stubbed executor output)', () => {
  let dir: string;
  beforeEach(() => { dir = mkdtempSync(join(tmpdir(), 'pc-e2e-')); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it('runs the post-executor pipeline against a sample prompt with conflicting rules', async () => {
    const promptPath = join(dir, 'sample.md');
    writeFileSync(promptPath, [
      '---',
      'type: system',
      'target_model: claude-opus-4-7',
      'output: [inline, markdown]',
      'anchors:',
      '  - input: "I am furious!"',
      '    expect_not_contains: ["sure thing"]',
      '---',
      'Always be formal.',
      'Be casual and friendly.',
    ].join('\n'), 'utf8');

    const { frontmatter } = parsePromptFile(readFileSync(promptPath, 'utf8'));
    expect(frontmatter.anchors).toHaveLength(1);

    const scenarios: Scenario[] = [
      {
        id: 'S1', kind: 'regression',
        input: frontmatter.anchors[0]!.input,
        assertions: [{ kind: 'not_contains', value: 'sure thing' }],
      },
    ];

    // In production, behavior-runner agent dispatches prompt-executor subagents and collects runs.
    // For this test, we stub the executor's output directly.
    const runs: Run[] = [
      {
        scenario_id: 'S1',
        output: 'I understand your frustration.',
        tokens: { input: 0, output: 0 },
        latency_ms: 0,
        model: 'claude-opus-4-7',
        provider: 'subagent',
      },
    ];

    const verdicts = scenarios.map((sc) => evaluateMechanical(sc, runs.find((r) => r.scenario_id === sc.id)!));
    expect(verdicts[0]!.pass).toBe(true);

    const report: Report = {
      prompt_path: promptPath, target_model: 'claude-opus-4-7',
      rules: [
        { id: 'R1', category: 'tone', text: 'be formal', line: 1, source_excerpt: 'Always be formal.' },
        { id: 'R2', category: 'tone', text: 'be casual', line: 2, source_excerpt: 'Be casual and friendly.' },
      ],
      conflicts: [{ id: 'C1', rule_ids: ['R1', 'R2'], severity: 'high', reasoning: 'tone contradiction' }],
      dominances: [], gaps: [], scenarios, runs, verdicts,
      summary: { total_scenarios: 1, passed: 1, failed: 0, high_severity_findings: 1 },
      generated_at: '2026-05-14T00:00:00Z',
    };

    const mdPath = join(dir, 'report.md');
    await writeMarkdownReport(report, { outputPath: mdPath });
    expect(readFileSync(mdPath, 'utf8')).toContain('## Conflicts');

    await writeInlineReport(report, { promptPath });
    expect(readFileSync(promptPath, 'utf8')).toMatch(/PROMPTCHECK \[CONFLICT/);
  });
});
