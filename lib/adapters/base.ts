export interface RunOpts {
  systemPrompt: string;
  userInput: string;
  model: string;
  temperature?: number;
  maxTokens?: number;
  cacheSystemPrompt?: boolean;
}

export interface RunResult {
  output: string;
  tokens: { input: number; output: number; cache_read?: number; cache_write?: number };
  latency_ms: number;
  model: string;
  provider: 'anthropic' | 'openai';
}

export interface IAdapter {
  readonly name: 'anthropic' | 'openai';
  supportsModel(model: string): boolean;
  run(opts: RunOpts): Promise<RunResult>;
}

export class AdapterError extends Error {
  constructor(message: string, public readonly cause?: unknown) {
    super(message);
    this.name = 'AdapterError';
  }
}
