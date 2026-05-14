import { writeFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';
import type { Report } from '../types';

export async function writeJsonReport(report: Report, opts: { outputPath: string }): Promise<void> {
  await mkdir(dirname(opts.outputPath), { recursive: true });
  await writeFile(opts.outputPath, JSON.stringify(report, null, 2), 'utf8');
}
