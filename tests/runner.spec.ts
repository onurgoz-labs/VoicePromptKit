import { describe, it, expect } from 'vitest';
import { runScenarios } from '../lib/runner';
import type { Scenario } from '../lib/types';

const fakeAdapter = {
  name: 'anthropic' as const,
  supportsModel: () => true,
  run: async (opts: { userInput: string }) => ({
    output: `echo: ${opts.userInput}`,
    tokens: { input: 1, output: 1 },
    latency_ms: 1,
    model: 'claude-opus-4-7',
    provider: 'anthropic' as const,
  }),
};

describe('runScenarios', () => {
  it('executes each scenario via injected adapter', async () => {
    const scenarios: Scenario[] = [
      { id: 'S1', kind: 'normal', input: 'hello', assertions: [] },
      { id: 'S2', kind: 'normal', input: 'world', assertions: [] },
    ];
    const runs = await runScenarios({
      systemPrompt: 'sys', scenarios, model: 'claude-opus-4-7', adapter: fakeAdapter,
    });
    expect(runs).toHaveLength(2);
    expect(runs[0]!.output).toBe('echo: hello');
    expect(runs[1]!.output).toBe('echo: world');
  });
});
