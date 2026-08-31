/**
 * Keeps the suite's git commands off the repository the suite is running in.
 *
 * Git exports these to every hook it launches, and they take precedence over
 * `cwd`. So a test that does `execSync('git init --bare', { cwd: tempDir })`
 * initialises whatever GIT_DIR points at instead — which, under `pre-push`, is
 * the real repository. That is how this one was flipped to bare, had its
 * committed identity replaced by a fixture's, and ended up with hundreds of
 * phantom staged deletions in its index.
 *
 * Scrubbing them centrally rather than per-spawn is deliberate: the failure is
 * silent, it only appears when the suite runs from a hook, and a helper that
 * every future git call must remember to use is a helper that will eventually
 * be forgotten.
 */
export const INHERITED_GIT_VARS = [
  'GIT_DIR',
  'GIT_WORK_TREE',
  'GIT_INDEX_FILE',
  'GIT_OBJECT_DIRECTORY',
  'GIT_ALTERNATE_OBJECT_DIRECTORIES',
  'GIT_COMMON_DIR',
  'GIT_NAMESPACE',
  'GIT_PREFIX',
  'GIT_CONFIG',
  'GIT_CONFIG_GLOBAL',
] as const;

export function scrubGitEnv(env: Record<string, string | undefined>): void {
  for (const name of INHERITED_GIT_VARS) delete env[name];
}

scrubGitEnv(process.env);
