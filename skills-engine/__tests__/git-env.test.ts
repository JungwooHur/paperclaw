import { describe, expect, it } from 'vitest';

import { INHERITED_GIT_VARS, scrubGitEnv } from '../../vitest.setup';

// Git exports these to every hook it runs. A test that spawns git in a temp
// directory inherits them, and they OVERRIDE cwd — so `git init --bare` and
// `git config user.name` land on the repository the hook is running in rather
// than the temp one. That is not hypothetical: it flipped this repository to
// bare, replaced the committed identity with the fixture's, and left hundreds of
// phantom staged deletions in the index.
describe('the git environment a hook hands down', () => {
  it('is stripped of everything that overrides cwd', () => {
    const env: Record<string, string | undefined> = {};
    for (const name of INHERITED_GIT_VARS) env[name] = '/some/other/repo';

    scrubGitEnv(env);

    expect(Object.keys(env)).toEqual([]);
  });

  it('names GIT_DIR, the one that does the damage', () => {
    expect(INHERITED_GIT_VARS).toContain('GIT_DIR');
    expect(INHERITED_GIT_VARS).toContain('GIT_WORK_TREE');
    expect(INHERITED_GIT_VARS).toContain('GIT_INDEX_FILE');
  });

  it('leaves settings a test deliberately asked for alone', () => {
    // The suite sets this itself to keep git offline; scrubbing it would put
    // the network back into the build.
    const env = { GIT_DIR: '/some/other/repo', GIT_ALLOW_PROTOCOL: 'file' };

    scrubGitEnv(env);

    expect(env).toEqual({ GIT_ALLOW_PROTOCOL: 'file' });
  });

  it('has already run by the time a test executes', () => {
    for (const name of INHERITED_GIT_VARS) {
      expect(process.env[name], `${name} still set`).toBeUndefined();
    }
  });
});
