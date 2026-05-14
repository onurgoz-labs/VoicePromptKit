import { describe, it, expect } from 'vitest';
import { parsePromptFile } from '../lib/frontmatter';

describe('parsePromptFile', () => {
  it('extracts frontmatter and body', () => {
    const raw = `---\ntype: system\ntarget_model: claude-opus-4-7\nexpand_count: 3\n---\nYou are a helpful agent.\n`;
    const { frontmatter, body } = parsePromptFile(raw);
    expect(frontmatter.type).toBe('system');
    expect(frontmatter.target_model).toBe('claude-opus-4-7');
    expect(frontmatter.expand_count).toBe(3);
    expect(body.trim()).toBe('You are a helpful agent.');
  });

  it('applies defaults when frontmatter is missing', () => {
    const raw = `Just a prompt body.\n`;
    const { frontmatter, body } = parsePromptFile(raw);
    expect(frontmatter.target_model).toBe('claude-opus-4-7');
    expect(frontmatter.output).toEqual(['inline']);
    expect(frontmatter.anchors).toEqual([]);
    expect(body.trim()).toBe('Just a prompt body.');
  });

  it('parses anchors with all optional fields', () => {
    const raw = `---\nanchors:\n  - input: "test"\n    expect_contains: ["yes"]\n    rubric: "be polite"\n---\nbody`;
    const { frontmatter } = parsePromptFile(raw);
    expect(frontmatter.anchors).toHaveLength(1);
    expect(frontmatter.anchors[0]!.input).toBe('test');
    expect(frontmatter.anchors[0]!.rubric).toBe('be polite');
  });

  it('throws on invalid frontmatter shape', () => {
    const raw = `---\ntype: invalid_type\n---\nbody`;
    expect(() => parsePromptFile(raw)).toThrow();
  });

  it('handles CRLF line endings (cross-platform)', () => {
    const raw = `---\r\ntype: system\r\nexpand_count: 2\r\n---\r\nWindows body\r\n`;
    const { frontmatter, body } = parsePromptFile(raw);
    expect(frontmatter.type).toBe('system');
    expect(frontmatter.expand_count).toBe(2);
    expect(body.trim()).toBe('Windows body');
  });
});
