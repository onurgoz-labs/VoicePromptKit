import yaml from 'js-yaml';
import { Frontmatter } from './types';

const FRONTMATTER_REGEX = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/;

export type Env = Record<string, string | undefined>;

export function parsePromptFile(raw: string, env: Env = process.env): { frontmatter: Frontmatter; body: string } {
  const match = raw.match(FRONTMATTER_REGEX);
  if (!match) {
    return { frontmatter: Frontmatter.parse(applyEnvOverrides({}, env)), body: raw };
  }
  const [, yamlBlock, body] = match;
  const parsed = (yaml.load(yamlBlock!) ?? {}) as Record<string, unknown>;
  const layered = applyEnvOverrides(parsed, env);
  const frontmatter = Frontmatter.parse(layered);
  return { frontmatter, body: body ?? '' };
}

export function applyEnvOverrides(raw: Record<string, unknown>, env: Env): Record<string, unknown> {
  const overrides: Record<string, unknown> = {};
  if (env.PROMPTCHECKER_TARGET_MODEL) overrides.target_model = env.PROMPTCHECKER_TARGET_MODEL;
  if (env.PROMPTCHECKER_OUTPUT) overrides.output = env.PROMPTCHECKER_OUTPUT.split(',').map((s) => s.trim());
  if (env.PROMPTCHECKER_EXPAND_COUNT) overrides.expand_count = Number(env.PROMPTCHECKER_EXPAND_COUNT);
  if (env.PROMPTCHECKER_EXECUTOR) overrides.executor = env.PROMPTCHECKER_EXECUTOR;
  return { ...overrides, ...raw };
}

export function stripPromptCheckerAnnotations(body: string): string {
  return body.replace(/^<!-- PROMPTCHECK \[.*?\] L\d+[^>]*-->\r?\n?/gm, '');
}
