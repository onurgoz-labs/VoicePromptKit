import { z } from 'zod';

export const PromptType = z.enum(['system', 'agent', 'vapi', 'task', 'chain']);
export type PromptType = z.infer<typeof PromptType>;

export const OutputFormat = z.enum(['inline', 'markdown', 'html', 'json']);
export type OutputFormat = z.infer<typeof OutputFormat>;

export const RuleCategory = z.enum(['behavior', 'format', 'tone', 'policy', 'persona']);
export type RuleCategory = z.infer<typeof RuleCategory>;

export const Severity = z.enum(['low', 'medium', 'high']);
export type Severity = z.infer<typeof Severity>;

export const LensId = z.enum(['conflict', 'dominance', 'gap', 'drift']);
export type LensId = z.infer<typeof LensId>;

export interface LensManifest {
  id: LensId;
  kind: 'static' | 'dynamic';
  description: string;
  implementer: string[];
  artefacts: string[];
}

export const Anchor = z.object({
  input: z.string(),
  expect_contains: z.array(z.string()).optional(),
  expect_not_contains: z.array(z.string()).optional(),
  rubric: z.string().optional(),
});
export type Anchor = z.infer<typeof Anchor>;

export const Frontmatter = z.object({
  type: PromptType.optional(),
  target_model: z.string().default('claude-opus-4-7'),
  output: z.array(OutputFormat).default(['inline']),
  expand_count: z.number().int().nonnegative().default(5),
  anchors: z.array(Anchor).default([]),
  executor: z.string().optional(),
});
export type Frontmatter = z.infer<typeof Frontmatter>;

export const Rule = z.object({
  id: z.string(),
  category: RuleCategory,
  text: z.string(),
  line: z.number().int().nonnegative(),
  source_excerpt: z.string(),
});
export type Rule = z.infer<typeof Rule>;

export const Conflict = z.object({
  id: z.string(),
  rule_ids: z.array(z.string()).min(2),
  severity: Severity,
  reasoning: z.string(),
});
export type Conflict = z.infer<typeof Conflict>;

export const Dominance = z.object({
  id: z.string(),
  dominant_rule_id: z.string(),
  dominated_rule_id: z.string(),
  mechanism: z.enum(['position', 'length', 'specificity', 'recency', 'role-override']),
  reasoning: z.string(),
});
export type Dominance = z.infer<typeof Dominance>;

export const GapKind = z.enum(['undefined_edge_case', 'ambiguous_term']);
export type GapKind = z.infer<typeof GapKind>;

export const Gap = z.object({
  id: z.string(),
  kind: GapKind.optional(),
  description: z.string(),
  related_rule_ids: z.array(z.string()).default([]),
  severity: Severity,
});
export type Gap = z.infer<typeof Gap>;

export const Assertion = z.object({
  kind: z.enum(['contains', 'not_contains', 'regex', 'length_max', 'length_min']),
  value: z.string(),
});
export type Assertion = z.infer<typeof Assertion>;

export const ScenarioKind = z.enum([
  'regression',
  'conflict',
  'role-override',
  'boundary',
  'ambiguity',
  'normal',
]);
export type ScenarioKind = z.infer<typeof ScenarioKind>;

export const Scenario = z.object({
  id: z.string(),
  kind: ScenarioKind,
  input: z.string(),
  assertions: z.array(Assertion).default([]),
  rubric: z.string().optional(),
  derived_from: z.string().optional(),
});
export type Scenario = z.infer<typeof Scenario>;

export const Run = z.object({
  scenario_id: z.string(),
  output: z.string(),
  tokens: z.object({ input: z.number(), output: z.number() }),
  latency_ms: z.number(),
  model: z.string(),
  provider: z.string(),
});
export type Run = z.infer<typeof Run>;

export const Verdict = z.object({
  scenario_id: z.string(),
  pass: z.boolean(),
  score: z.number().min(0).max(1),
  reasons: z.array(z.string()),
  violated_assertions: z.array(Assertion).default([]),
});
export type Verdict = z.infer<typeof Verdict>;

export const Report = z.object({
  prompt_path: z.string(),
  prompt_type: PromptType.optional(),
  target_model: z.string(),
  rules: z.array(Rule),
  conflicts: z.array(Conflict),
  dominances: z.array(Dominance),
  gaps: z.array(Gap),
  scenarios: z.array(Scenario),
  runs: z.array(Run),
  verdicts: z.array(Verdict),
  summary: z.object({
    total_scenarios: z.number(),
    passed: z.number(),
    failed: z.number(),
    high_severity_findings: z.number(),
  }),
  generated_at: z.string(),
});
export type Report = z.infer<typeof Report>;
