import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: [
      'src/**/*.test.ts',
      'setup/**/*.test.ts',
      'skills-engine/**/*.test.ts',
      // The agent runner's own tests: they cannot live in its src/, which the
      // container compiles whole at every start.
      'container/agent-runner/test/**/*.test.ts',
    ],
    // Runs before any test file, so a spawned git can never be pointed
    // at the repository the suite is running in. See vitest.setup.ts.
    setupFiles: ['./vitest.setup.ts'],
  },
});
