import fs from 'fs';
import path from 'path';

/**
 * Parse the .env file and return values for the requested keys.
 * Does NOT load anything into process.env — callers decide what to
 * do with the values. This keeps secrets out of the process environment
 * so they don't leak to child processes.
 *
 * The file is <cwd>/.env, the project root the service runs in, unless
 * PAPERCLAW_ENV_FILE names another. The test suite uses that to read none at
 * all (vitest.setup.ts), so a developer's own .env cannot change a result.
 *
 * Imports nothing of this project's own: logger.ts reads LOG_LEVEL through it,
 * so a logger import here would be a cycle.
 */
export function readEnvFile(keys: string[]): Record<string, string> {
  const envFile =
    process.env.PAPERCLAW_ENV_FILE || path.join(process.cwd(), '.env');
  let content: string;
  try {
    content = fs.readFileSync(envFile, 'utf-8');
  } catch {
    // No .env: every setting falls back to its default.
    return {};
  }

  const result: Record<string, string> = {};
  const wanted = new Set(keys);

  for (const line of content.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eqIdx = trimmed.indexOf('=');
    if (eqIdx === -1) continue;
    const key = trimmed.slice(0, eqIdx).trim();
    if (!wanted.has(key)) continue;
    let value = trimmed.slice(eqIdx + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (value) result[key] = value;
  }

  return result;
}
