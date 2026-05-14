import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';
import { parsePromptFile } from './frontmatter';
import { selectAdapter } from './adapters/select';
import type { IAdapter } from './adapters/base';
import type { Scenario, Run } from './types';

export interface RunOpts {
  systemPrompt: string;
  scenarios: Scenario[];
  model: string;
  adapter: IAdapter;
  cacheSystemPrompt?: boolean;
}

export async function runScenarios(opts: RunOpts): Promise<Run[]> {
  const runs: Run[] = [];
  for (const sc of opts.scenarios) {
    const result = await opts.adapter.run({
      systemPrompt: opts.systemPrompt,
      userInput: sc.input,
      model: opts.model,
      temperature: 0,
      cacheSystemPrompt: opts.cacheSystemPrompt ?? true,
    });
    runs.push({
      scenario_id: sc.id,
      output: result.output,
      tokens: { input: result.tokens.input, output: result.tokens.output },
      latency_ms: result.latency_ms,
      model: result.model,
      provider: result.provider,
    });
  }
  return runs;
}

// CLI entrypoint
if (import.meta.url === `file://${process.argv[1]}`) {
  void main();
}

async function main(): Promise<void> {
  const payloadPath = process.argv[2];
  if (!payloadPath) throw new Error('Usage: runner.ts <payload.json>');
  const payload = JSON.parse(await readFile(payloadPath, 'utf8')) as {
    promptPath: string;
    model: string;
    providerOverride?: 'anthropic' | 'openai';
    scenarios: Scenario[];
    cacheSystemPrompt?: boolean;
  };
  const raw = await readFile(payload.promptPath, 'utf8');
  const { body } = parsePromptFile(raw);
  const adapter = selectAdapter({ model: payload.model, providerOverride: payload.providerOverride });
  const runs = await runScenarios({
    systemPrompt: body,
    scenarios: payload.scenarios,
    model: payload.model,
    adapter,
    cacheSystemPrompt: payload.cacheSystemPrompt,
  });
  const outPath = `.promptcheck/.tmp/runs.json`;
  await mkdir(dirname(outPath), { recursive: true });
  const result = { runs };
  await writeFile(outPath, JSON.stringify(result, null, 2), 'utf8');
  process.stdout.write(JSON.stringify(result));
}
