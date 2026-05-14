import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';
import type { Scenario, Run, Verdict, Assertion } from './types';

export function evaluateMechanical(scenario: Scenario, run: Run): Verdict {
  const violated: Assertion[] = [];
  const reasons: string[] = [];
  for (const a of scenario.assertions) {
    if (!check(a, run.output)) {
      violated.push(a);
      reasons.push(`assertion ${a.kind} "${a.value}" failed`);
    } else {
      reasons.push(`assertion ${a.kind} "${a.value}" passed`);
    }
  }
  const total = scenario.assertions.length;
  const passed = total - violated.length;
  const score = total === 0 ? 1 : passed / total;
  return { scenario_id: scenario.id, pass: violated.length === 0, score, reasons, violated_assertions: violated };
}

function check(a: Assertion, output: string): boolean {
  switch (a.kind) {
    case 'contains': return output.includes(a.value);
    case 'not_contains': return !output.includes(a.value);
    case 'regex': return new RegExp(a.value).test(output);
    case 'length_max': return output.length <= parseInt(a.value, 10);
    case 'length_min': return output.length >= parseInt(a.value, 10);
  }
}

// CLI entrypoint
if (import.meta.url === `file://${process.argv[1]}`) {
  void main();
}

async function main(): Promise<void> {
  const [scenariosPath, runsPath] = [process.argv[2], process.argv[3]];
  if (!scenariosPath || !runsPath) throw new Error('Usage: judge.ts <scenarios.json> <runs.json>');
  const scenarios = JSON.parse(await readFile(scenariosPath, 'utf8')).scenarios as Scenario[];
  const runs = JSON.parse(await readFile(runsPath, 'utf8')).runs as Run[];
  const verdicts: Verdict[] = [];
  for (const sc of scenarios) {
    const run = runs.find((r) => r.scenario_id === sc.id);
    if (!run) {
      verdicts.push({ scenario_id: sc.id, pass: false, score: 0, reasons: ['no run for scenario'], violated_assertions: [] });
      continue;
    }
    verdicts.push(evaluateMechanical(sc, run));
  }
  const outPath = `.promptcheck/.tmp/verdicts-mechanical.json`;
  await mkdir(dirname(outPath), { recursive: true });
  const result = { verdicts };
  await writeFile(outPath, JSON.stringify(result, null, 2), 'utf8');
  process.stdout.write(JSON.stringify(result));
}
