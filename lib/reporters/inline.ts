import { readFile, writeFile } from 'node:fs/promises';
import type { Report, Conflict, Dominance, Gap, Rule } from '../types';
import { stripPromptCheckerAnnotations } from '../frontmatter';

interface Annotation { line: number; text: string; }

export async function writeInlineReport(report: Report, opts: { promptPath: string }): Promise<void> {
  const raw = await readFile(opts.promptPath, 'utf8');
  const cleaned = stripPromptCheckerAnnotations(raw);
  const { head, body } = splitHeadBody(cleaned);
  const ruleById = new Map(report.rules.map((r) => [r.id, r]));

  const annotations: Annotation[] = [];
  for (const c of report.conflicts) annotations.push(buildConflictAnnotation(c, ruleById));
  for (const d of report.dominances) annotations.push(buildDominanceAnnotation(d, ruleById));
  for (const g of report.gaps) annotations.push(buildGapAnnotation(g, ruleById));
  annotations.sort((a, b) => b.line - a.line);

  const lines = body.split('\n');
  for (const a of annotations) lines.splice(Math.max(0, a.line - 1), 0, a.text);
  await writeFile(opts.promptPath, head + lines.join('\n'), 'utf8');
}

function splitHeadBody(raw: string): { head: string; body: string } {
  const m = raw.match(/^(---\n[\s\S]*?\n---\n)([\s\S]*)$/);
  return m ? { head: m[1]!, body: m[2]! } : { head: '', body: raw };
}

function buildConflictAnnotation(c: Conflict, rules: Map<string, Rule>): Annotation {
  const lineNumbers = c.rule_ids.map((id) => rules.get(id)?.line ?? 1);
  const minLine = Math.min(...lineNumbers);
  const refs = lineNumbers.join('↔L');
  return {
    line: minLine,
    text: `<!-- PROMPTCHECK [CONFLICT severity=${c.severity}] L${refs}: ${oneLine(c.reasoning)} -->`,
  };
}

function buildDominanceAnnotation(d: Dominance, rules: Map<string, Rule>): Annotation {
  const dom = rules.get(d.dominant_rule_id)?.line ?? 1;
  const sub = rules.get(d.dominated_rule_id)?.line ?? 1;
  return {
    line: sub,
    text: `<!-- PROMPTCHECK [DOMINANCE mechanism=${d.mechanism}] L${dom}>L${sub}: ${oneLine(d.reasoning)} -->`,
  };
}

function buildGapAnnotation(g: Gap, rules: Map<string, Rule>): Annotation {
  const line = g.related_rule_ids[0] ? (rules.get(g.related_rule_ids[0])?.line ?? 1) : 1;
  return {
    line,
    text: `<!-- PROMPTCHECK [GAP severity=${g.severity}] L${line}: ${oneLine(g.description)} -->`,
  };
}

function oneLine(s: string): string {
  return s.replace(/\s+/g, ' ').slice(0, 240);
}
