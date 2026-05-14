import { describe, it, expect } from 'vitest';
import { LENSES, STATIC_LENSES, DYNAMIC_LENSES } from '../lib/lenses';

describe('lens registry', () => {
  it('has exactly four lenses', () => {
    expect(Object.keys(LENSES)).toHaveLength(4);
  });

  it('every lens points to at least one implementer agent', () => {
    for (const lens of Object.values(LENSES)) {
      expect(lens.implementer.length).toBeGreaterThan(0);
    }
  });

  it('every lens produces at least one artefact', () => {
    for (const lens of Object.values(LENSES)) {
      expect(lens.artefacts.length).toBeGreaterThan(0);
    }
  });

  it('partitions cleanly into static and dynamic', () => {
    expect(STATIC_LENSES).toEqual(expect.arrayContaining(['conflict', 'dominance', 'gap']));
    expect(DYNAMIC_LENSES).toEqual(['drift']);
    expect(STATIC_LENSES.length + DYNAMIC_LENSES.length).toBe(4);
  });

  it('drift lens is the only multi-implementer lens', () => {
    expect(LENSES.drift.implementer).toEqual(['scenario-generator', 'behavior-runner', 'judge']);
    expect(LENSES.conflict.implementer).toEqual(['conflict-lens']);
    expect(LENSES.dominance.implementer).toEqual(['dominance-lens']);
    expect(LENSES.gap.implementer).toEqual(['gap-lens']);
  });
});
