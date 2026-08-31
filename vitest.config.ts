import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['src/**/*.test.ts', 'setup/**/*.test.ts', 'skills-engine/**/*.test.ts'],
    // Runs before any test file, so a spawned git can never be pointed
    // at the repository the suite is running in. See vitest.setup.ts.
    setupFiles: ['./vitest.setup.ts'],
  },
});
