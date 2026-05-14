import { describe, it, expect, beforeAll } from 'vitest';
import { selectAdapter } from '../../lib/adapters/select';

beforeAll(() => {
  process.env.ANTHROPIC_API_KEY ??= 'test-key';
  process.env.OPENAI_API_KEY ??= 'test-key';
});

describe('selectAdapter', () => {
  it('returns anthropic for claude models', () => {
    expect(selectAdapter({ model: 'claude-opus-4-7' }).name).toBe('anthropic');
  });
  it('returns openai for gpt models', () => {
    expect(selectAdapter({ model: 'gpt-4o' }).name).toBe('openai');
  });
  it('CLI flag overrides model-based inference', () => {
    expect(selectAdapter({ model: 'claude-opus-4-7', providerOverride: 'openai' }).name).toBe('openai');
  });
  it('env override beats default', () => {
    expect(selectAdapter({ model: 'unknown-model', env: { PROMPTCHECK_PROVIDER: 'openai' } }).name).toBe('openai');
  });
  it('throws when no rule matches', () => {
    expect(() => selectAdapter({ model: 'mystery-1' })).toThrow();
  });
});
