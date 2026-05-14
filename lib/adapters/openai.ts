import OpenAI from 'openai';
import type { IAdapter, RunOpts, RunResult } from './base';
import { AdapterError } from './base';

export class OpenAIAdapter implements IAdapter {
  readonly name = 'openai' as const;
  private client: OpenAI;

  constructor(client?: OpenAI) {
    this.client = client ?? new OpenAI();
  }

  supportsModel(model: string): boolean {
    return model.startsWith('gpt-') || model.startsWith('codex-') || model.startsWith('o1') || model.startsWith('o3');
  }

  async run(opts: RunOpts): Promise<RunResult> {
    const start = Date.now();
    try {
      const res = await this.client.chat.completions.create({
        model: opts.model,
        temperature: opts.temperature ?? 0,
        max_tokens: opts.maxTokens ?? 1024,
        messages: [
          { role: 'system', content: opts.systemPrompt },
          { role: 'user', content: opts.userInput },
        ],
      });
      const text = res.choices[0]?.message?.content ?? '';
      return {
        output: text,
        tokens: {
          input: res.usage?.prompt_tokens ?? 0,
          output: res.usage?.completion_tokens ?? 0,
        },
        latency_ms: Date.now() - start,
        model: res.model,
        provider: 'openai',
      };
    } catch (err) {
      throw new AdapterError('openai.run failed', err);
    }
  }
}
