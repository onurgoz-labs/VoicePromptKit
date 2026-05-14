import { describe, it, expect } from 'vitest';
import { evaluateMechanical } from '../lib/judge';
import type { Scenario, Run } from '../lib/types';

const scenario: Scenario = {
  id: 'S1', kind: 'normal', input: 'x',
  assertions: [
    { kind: 'contains', value: 'policy' },
    { kind: 'not_contains', value: 'sure thing' },
    { kind: 'length_max', value: '50' },
  ],
};
const passingRun: Run = { scenario_id: 'S1', output: 'cite the policy', tokens: { input: 1, output: 1 }, latency_ms: 1, model: 'x', provider: 'anthropic' };
const failingRun: Run = { scenario_id: 'S1', output: 'sure thing here', tokens: { input: 1, output: 1 }, latency_ms: 1, model: 'x', provider: 'anthropic' };

describe('evaluateMechanical', () => {
  it('passes when all assertions succeed', () => {
    const v = evaluateMechanical(scenario, passingRun);
    expect(v.pass).toBe(true);
    expect(v.score).toBe(1);
  });
  it('fails when any assertion fails', () => {
    const v = evaluateMechanical(scenario, failingRun);
    expect(v.pass).toBe(false);
    expect(v.violated_assertions.length).toBeGreaterThan(0);
  });
});
