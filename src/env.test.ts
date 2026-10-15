import fs from 'fs';
import os from 'os';
import path from 'path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { readEnvFile } from './env.js';

/**
 * Where .env is read from, and why the suite never reads the developer's own.
 *
 * The suite runs in a checkout, and a checkout usually has a real .env. Read
 * from there, ASSISTANT_NAME or TZ would change what a test sees, so a test
 * could pass in CI and fail on the pre-push hook of the owner's checkout.
 * vitest.setup.ts points PAPERCLAW_ENV_FILE at a file that does not exist.
 */
describe('readEnvFile', () => {
  let tmp: string;
  let saved: string | undefined;

  beforeEach(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'paperclaw-envfile-'));
    saved = process.env.PAPERCLAW_ENV_FILE;
    fs.writeFileSync(path.join(tmp, '.env'), 'ASSISTANT_NAME=FromCwd\n');
    vi.spyOn(process, 'cwd').mockReturnValue(tmp);
  });

  afterEach(() => {
    if (saved === undefined) delete process.env.PAPERCLAW_ENV_FILE;
    else process.env.PAPERCLAW_ENV_FILE = saved;
    vi.restoreAllMocks();
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('under the test suite, ignores the .env in the working directory', () => {
    expect(readEnvFile(['ASSISTANT_NAME'])).toEqual({});
  });

  it('reads <cwd>/.env when PAPERCLAW_ENV_FILE is unset, as the service does', () => {
    delete process.env.PAPERCLAW_ENV_FILE;
    expect(readEnvFile(['ASSISTANT_NAME'])).toEqual({
      ASSISTANT_NAME: 'FromCwd',
    });
  });

  it('reads the file PAPERCLAW_ENV_FILE names instead', () => {
    const other = path.join(tmp, 'other.env');
    fs.writeFileSync(other, 'ASSISTANT_NAME=FromOverride\n');
    process.env.PAPERCLAW_ENV_FILE = other;
    expect(readEnvFile(['ASSISTANT_NAME'])).toEqual({
      ASSISTANT_NAME: 'FromOverride',
    });
  });
});
