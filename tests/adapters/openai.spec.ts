import { describe, it, expect, vi } from 'vitest';
import { OpenAIAdapter } from '../../lib/adapters/openai';

const fakeClient = {
  chat: {
    completions: {
      create: vi.fn().mockResolvedValue({
        choices: [{ message: { content: 'hello' } }],
        usage: { prompt_tokens: 12, completion_tokens: 4 },
        model: 'gpt-4o',
      }),
    },
  },
};

describe('OpenAIAdapter', () => {
  it('supports gpt-* and codex-* models', () => {
    const a = new OpenAIAdapter(fakeClient as never);
    expect(a.supportsModel('gpt-4o')).toBe(true);
    expect(a.supportsModel('codex-mini')).toBe(true);
    expect(a.supportsModel('claude-opus-4-7')).toBe(false);
  });

  it('sends system + user messages and returns RunResult', async () => {
    const a = new OpenAIAdapter(fakeClient as never);
    const result = await a.run({
      systemPrompt: 'You are X.',
      userInput: 'hi',
      model: 'gpt-4o',
    });
    const call = fakeClient.chat.completions.create.mock.calls.at(-1)![0];
    expect(call.messages[0]).toEqual({ role: 'system', content: 'You are X.' });
    expect(call.messages[1]).toEqual({ role: 'user', content: 'hi' });
    expect(result.output).toBe('hello');
    expect(result.tokens.input).toBe(12);
    expect(result.provider).toBe('openai');
  });
});
