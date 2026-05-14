import Anthropic from '@anthropic-ai/sdk';
import type { IAdapter, RunOpts, RunResult } from './base';
import { AdapterError } from './base';

export class AnthropicAdapter implements IAdapter {
  readonly name = 'anthropic' as const;
  private client: Anthropic;

  constructor(client?: Anthropic) {
    this.client = client ?? new Anthropic();
  }

  supportsModel(model: string): boolean {
    return model.startsWith('claude-');
  }

  async run(opts: RunOpts): Promise<RunResult> {
    const start = Date.now();
    try {
      const system: string | Anthropic.TextBlockParam[] = opts.cacheSystemPrompt
        ? [{ type: 'text', text: opts.systemPrompt, cache_control: { type: 'ephemeral' } }]
        : opts.systemPrompt;
      const res = await this.client.messages.create({
        model: opts.model,
        max_tokens: opts.maxTokens ?? 1024,
        temperature: opts.temperature ?? 0,
        system,
        messages: [{ role: 'user', content: opts.userInput }],
      });
      const text = res.content
        .filter((b): b is Anthropic.TextBlock => b.type === 'text')
        .map((b) => b.text)
        .join('');
      const usage = res.usage as Anthropic.Usage & {
        cache_read_input_tokens?: number;
        cache_creation_input_tokens?: number;
      };
      return {
        output: text,
        tokens: {
          input: usage.input_tokens,
          output: usage.output_tokens,
          cache_read: usage.cache_read_input_tokens ?? 0,
          cache_write: usage.cache_creation_input_tokens ?? 0,
        },
        latency_ms: Date.now() - start,
        model: res.model,
        provider: 'anthropic',
      };
    } catch (err) {
      throw new AdapterError('anthropic.run failed', err);
    }
  }
}
