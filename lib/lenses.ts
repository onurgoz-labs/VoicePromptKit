import type { LensManifest, LensId } from './types';

export const LENSES: Record<LensId, LensManifest> = {
  conflict: {
    id: 'conflict',
    kind: 'static',
    description: 'Detect rules that logically contradict each other.',
    implementer: ['conflict-lens'],
    artefacts: ['conflicts.json'],
  },
  dominance: {
    id: 'dominance',
    kind: 'static',
    description: 'Detect rules that silently override others through position, length, specificity, recency, or role-override.',
    implementer: ['dominance-lens'],
    artefacts: ['dominances.json'],
  },
  gap: {
    id: 'gap',
    kind: 'static',
    description: 'Surface undefined edge cases, ambiguous terms, and missing failure modes.',
    implementer: ['gap-lens'],
    artefacts: ['gaps.json'],
  },
  drift: {
    id: 'drift',
    kind: 'dynamic',
    description: 'Detect behavioural drift by generating adversarial scenarios, running them against a real LLM, and judging the outputs.',
    implementer: ['scenario-generator', 'behavior-runner', 'judge'],
    artefacts: ['scenarios.json', 'runs.json', 'verdicts.json'],
  },
};

export const STATIC_LENSES: LensId[] = (Object.values(LENSES) as LensManifest[])
  .filter((l) => l.kind === 'static')
  .map((l) => l.id);

export const DYNAMIC_LENSES: LensId[] = (Object.values(LENSES) as LensManifest[])
  .filter((l) => l.kind === 'dynamic')
  .map((l) => l.id);
