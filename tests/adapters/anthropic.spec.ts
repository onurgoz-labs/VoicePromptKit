import { describe, it, expect, vi } from 'vitest';
import { AnthropicAdapter } from '../../lib/adapters/anthropic';

const fakeClient = {
  messages: {
    create: vi.fn().mockResolvedValue({
      content: [{ type: 'text', text: 'hello' }],
      usage: { input_tokens: 10, output_tokens: 5, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
      model: 'claude-opus-4-7',
    }),
  },
};

describe('AnthropicAdapter', () => {
  it('supports claude-* models', () => {
    const a = new AnthropicAdapter(fakeClient as never);
    expect(a.supportsModel('claude-opus-4-7')).toBe(true);
    expect(a.supportsModel('gpt-4o')).toBe(false);
  });

  it('calls messages.create with system prompt and returns RunResult', async () => {
    const a = new AnthropicAdapter(fakeClient as never);
    const result = await a.run({
      systemPrompt: 'You are X.',
      userInput: 'hi',
      model: 'claude-opus-4-7',
    });
    expect(fakeClient.messages.create).toHaveBeenCalledOnce();
    const call = fakeClient.messages.create.mock.calls[0]![0];
    expect(call.system).toBeTruthy();
    expect(call.messages[0].content).toBe('hi');
    expect(result.output).toBe('hello');
    expect(result.tokens.input).toBe(10);
    expect(result.provider).toBe('anthropic');
  });

  it('enables cache_control when cacheSystemPrompt is true', async () => {
    const a = new AnthropicAdapter(fakeClient as never);
    await a.run({
      systemPrompt: 'You are X.',
      userInput: 'hi',
      model: 'claude-opus-4-7',
      cacheSystemPrompt: true,
    });
    const call = fakeClient.messages.create.mock.calls.at(-1)![0];
    expect(call.system[0].cache_control).toEqual({ type: 'ephemeral' });
  });
});
