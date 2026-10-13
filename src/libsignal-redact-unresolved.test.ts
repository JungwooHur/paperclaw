import { describe, expect, it, vi } from 'vitest';

// What a baileys upgrade can do to the lookup: an exports map with no require
// condition makes require.resolve throw ERR_PACKAGE_PATH_NOT_EXPORTED, and a
// renamed libsignal dependency makes the require throw MODULE_NOT_FOUND.
vi.mock('module', async (importOriginal) => {
  const actual = await importOriginal<typeof import('module')>();
  const createRequire = () => {
    const fail = () => {
      throw Object.assign(new Error('No "exports" main defined'), {
        code: 'ERR_PACKAGE_PATH_NOT_EXPORTED',
      });
    };
    return Object.assign(fail, { resolve: fail });
  };
  return { ...actual, default: { ...actual, createRequire }, createRequire };
});

describe('libsignal-redact when libsignal cannot be found', () => {
  it('warns instead of failing the import', async () => {
    // The service imports this before baileys, so a throw here would stop it
    // starting, and Restart=always would turn that into a crash loop.
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    await expect(import('./libsignal-redact.js')).resolves.toBeDefined();
    expect(warn).toHaveBeenCalledTimes(1);
  });
});
