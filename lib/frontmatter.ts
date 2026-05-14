import yaml from 'js-yaml';
import { Frontmatter } from './types';

const FRONTMATTER_REGEX = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/;

export function parsePromptFile(raw: string): { frontmatter: Frontmatter; body: string } {
  const match = raw.match(FRONTMATTER_REGEX);
  if (!match) {
    return { frontmatter: Frontmatter.parse({}), body: raw };
  }
  const [, yamlBlock, body] = match;
  const parsed = yaml.load(yamlBlock!) ?? {};
  const frontmatter = Frontmatter.parse(parsed);
  return { frontmatter, body: body ?? '' };
}

export function stripPromptCheckerAnnotations(body: string): string {
  return body.replace(/^<!-- PROMPTCHECK \[.*?\] L\d+[^>]*-->\n?/gm, '');
}
