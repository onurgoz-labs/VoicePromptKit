import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { parsePromptFile } from './frontmatter';

async function main(): Promise<void> {
  const promptPath = process.argv[2];
  if (!promptPath) throw new Error('Usage: frontmatter-cli.ts <prompt-path>');
  const raw = await readFile(promptPath, 'utf8');
  const { frontmatter, body } = parsePromptFile(raw);
  await mkdir('.promptcheck/.tmp', { recursive: true });
  await writeFile('.promptcheck/.tmp/frontmatter.json', JSON.stringify(frontmatter, null, 2), 'utf8');
  await writeFile('.promptcheck/.tmp/body.txt', body, 'utf8');
  process.stdout.write(JSON.stringify({ frontmatter, bodyLength: body.length }));
}

if (import.meta.url === `file://${process.argv[1]}`) {
  void main();
}
