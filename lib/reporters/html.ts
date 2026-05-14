import { writeFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';
import type { Report } from '../types';
import { renderMarkdown } from './markdown';

export async function writeHtmlReport(report: Report, opts: { outputPath: string }): Promise<void> {
  await mkdir(dirname(opts.outputPath), { recursive: true });
  const md = renderMarkdown(report);
  await writeFile(opts.outputPath, wrap(md), 'utf8');
}

function wrap(markdown: string): string {
  const escaped = markdown.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  return `<!doctype html><html><head><meta charset="utf-8"><title>PromptChecker Report</title><style>
body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #222; }
pre { background: #f5f5f5; padding: 1rem; overflow: auto; border-radius: 6px; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: #fafafa; }
code { background: #f0f0f0; padding: 0 4px; border-radius: 3px; }
</style></head><body><pre>${escaped}</pre></body></html>`;
}
