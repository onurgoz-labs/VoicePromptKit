import type { IAdapter } from './base';
import { AnthropicAdapter } from './anthropic';
import { OpenAIAdapter } from './openai';

export interface SelectOpts {
  model: string;
  providerOverride?: 'anthropic' | 'openai';
  env?: Record<string, string | undefined>;
}

export function selectAdapter(opts: SelectOpts): IAdapter {
  const env = opts.env ?? process.env;
  const envProvider = env.PROMPTCHECK_PROVIDER as 'anthropic' | 'openai' | undefined;

  const provider = opts.providerOverride ?? envProvider ?? inferProvider(opts.model);
  if (!provider) {
    throw new Error(`Cannot infer provider for model "${opts.model}". Set PROMPTCHECK_PROVIDER or pass --provider.`);
  }
  switch (provider) {
    case 'anthropic':
      return new AnthropicAdapter();
    case 'openai':
      return new OpenAIAdapter();
  }
}

function inferProvider(model: string): 'anthropic' | 'openai' | undefined {
  if (model.startsWith('claude-')) return 'anthropic';
  if (model.startsWith('gpt-') || model.startsWith('codex-') || model.startsWith('o1') || model.startsWith('o3'))
    return 'openai';
  return undefined;
}
