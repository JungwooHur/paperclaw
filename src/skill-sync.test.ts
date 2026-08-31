import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';

import {
  SkillDirState,
  resolveExtraSkillDirs,
  syncSkillDirs,
} from './skill-sync.js';

describe('resolveExtraSkillDirs', () => {
  // The filesystem is the only thing injected: the function is handed a verdict
  // per path and returns the directories to sync plus what to warn about.
  const all = (state: SkillDirState) => (): SkillDirState => state;

  it('is empty when the variable is unset', () => {
    expect(resolveExtraSkillDirs(undefined, all('ok'))).toEqual({
      dirs: [],
      warnings: [],
    });
  });

  it('is empty when the variable is blank', () => {
    expect(resolveExtraSkillDirs('  ', all('ok'))).toEqual({
      dirs: [],
      warnings: [],
    });
  });

  it('keeps the order the operator wrote', () => {
    const { dirs } = resolveExtraSkillDirs('/b:/a:/c', all('ok'));
    expect(dirs).toEqual(['/b', '/a', '/c']);
  });

  it('keeps the first of a repeated directory', () => {
    const { dirs } = resolveExtraSkillDirs('/a:/b:/a', all('ok'));
    expect(dirs).toEqual(['/a', '/b']);
  });

  it('ignores empty entries and surrounding space', () => {
    const { dirs } = resolveExtraSkillDirs(' /a : : /b ', all('ok'));
    expect(dirs).toEqual(['/a', '/b']);
  });

  it('drops a directory that is not there, and names it', () => {
    const { dirs, warnings } = resolveExtraSkillDirs('/gone', all('missing'));
    expect(dirs).toEqual([]);
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain('/gone');
  });

  it('drops a directory holding no skills, and names it', () => {
    const { dirs, warnings } = resolveExtraSkillDirs('/bare', all('empty'));
    expect(dirs).toEqual([]);
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain('/bare');
  });

  it('warns about the bad one without dropping the good one', () => {
    const { dirs, warnings } = resolveExtraSkillDirs('/good:/gone', (dir) =>
      dir === '/good' ? 'ok' : 'missing',
    );
    expect(dirs).toEqual(['/good']);
    expect(warnings).toHaveLength(1);
  });
});

describe('syncSkillDirs', () => {
  let root: string;

  beforeEach(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), 'skill-sync-'));
  });

  afterEach(() => {
    fs.rmSync(root, { recursive: true, force: true });
  });

  const skill = (dir: string, name: string, body: string): string => {
    const at = path.join(root, dir, name);
    fs.mkdirSync(at, { recursive: true });
    fs.writeFileSync(path.join(at, 'SKILL.md'), body);
    return path.join(root, dir);
  };

  const installed = (name: string): string =>
    fs.readFileSync(path.join(root, 'dst', name, 'SKILL.md'), 'utf8');

  it('installs a skill that lives outside this repository', () => {
    const builtin = skill('builtin', 'browser', 'built-in');
    const extra = skill('extra', 'private', 'private');

    syncSkillDirs([builtin, extra], path.join(root, 'dst'));

    expect(installed('browser')).toBe('built-in');
    expect(installed('private')).toBe('private');
  });

  it('lets a configured directory override a built-in of the same name', () => {
    const builtin = skill('builtin', 'browser', 'built-in');
    const extra = skill('extra', 'browser', 'overridden');

    syncSkillDirs([builtin, extra], path.join(root, 'dst'));

    expect(installed('browser')).toBe('overridden');
  });

  it('ignores a source directory that is not there', () => {
    const builtin = skill('builtin', 'browser', 'built-in');

    syncSkillDirs([builtin, path.join(root, 'gone')], path.join(root, 'dst'));

    expect(installed('browser')).toBe('built-in');
  });

  it('installs loose files nowhere', () => {
    const builtin = skill('builtin', 'browser', 'built-in');
    fs.writeFileSync(path.join(builtin, 'README.md'), 'not a skill');

    syncSkillDirs([builtin], path.join(root, 'dst'));

    expect(fs.readdirSync(path.join(root, 'dst'))).toEqual(['browser']);
  });

  it('leaves the destination untouched when nothing is configured', () => {
    const dst = path.join(root, 'dst');
    fs.mkdirSync(dst, { recursive: true });

    syncSkillDirs([], dst);

    expect(fs.readdirSync(dst)).toEqual([]);
  });
});
