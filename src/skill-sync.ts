/**
 * Installs agent skills into a group's session directory.
 *
 * Built-in skills live in this repository; others can be pointed at through
 * EXTRA_SKILLS_DIRS, so a private skill is usable here without ever living in
 * this tree. The copy happens on every run, which is what keeps an installed
 * skill from drifting away from its source — the same reason the service units
 * here are symlinks rather than copies.
 */
import fs from 'fs';
import path from 'path';

/** What a configured skill directory turned out to be. */
export type SkillDirState = 'ok' | 'missing' | 'empty';

/**
 * Reports whether a configured skill directory is usable.
 *
 * Empty counts as unusable on purpose: an operator who points at the wrong
 * level of a tree gets the same silence as one who points at nothing.
 */
export function inspectSkillDir(dir: string): SkillDirState {
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) return 'missing';
  const hasSkill = fs
    .readdirSync(dir)
    .some((entry) => fs.statSync(path.join(dir, entry)).isDirectory());
  return hasSkill ? 'ok' : 'empty';
}

/**
 * Turns EXTRA_SKILLS_DIRS into the directories to sync, plus what to warn about.
 *
 * Order is the operator's and is preserved, since a later directory overrides an
 * earlier skill of the same name. A directory that is missing or holds no skills
 * is dropped and named: a skill that silently fails to arrive shows up much
 * later as a command that fails for no visible reason, which is the failure mode
 * this project keeps paying for.
 *
 * Pure apart from the injected inspector, so it can be tested without a
 * filesystem.
 */
export function resolveExtraSkillDirs(
  raw: string | undefined,
  inspect: (dir: string) => SkillDirState,
): { dirs: string[]; warnings: string[] } {
  const dirs: string[] = [];
  const warnings: string[] = [];
  const seen = new Set<string>();
  for (const entry of (raw ?? '').split(path.delimiter)) {
    const dir = entry.trim();
    if (!dir || seen.has(dir)) continue;
    seen.add(dir);
    const state = inspect(dir);
    if (state === 'ok') {
      dirs.push(dir);
    } else {
      warnings.push(
        state === 'missing'
          ? `EXTRA_SKILLS_DIRS: no such directory, skipping: ${dir}`
          : `EXTRA_SKILLS_DIRS: no skills found in: ${dir}`,
      );
    }
  }
  return { dirs, warnings };
}

/**
 * Copies every skill in `sources` into `dst`, in order.
 *
 * Later sources overwrite earlier ones, so a configured directory can replace a
 * built-in skill of the same name. Only directories are copied: a README sitting
 * beside the skills is not a skill, and installing it would put a file into the
 * agent's skill list that no command can load.
 */
export function syncSkillDirs(sources: string[], dst: string): void {
  for (const src of sources) {
    if (!fs.existsSync(src)) continue;
    for (const entry of fs.readdirSync(src)) {
      const srcDir = path.join(src, entry);
      if (!fs.statSync(srcDir).isDirectory()) continue;
      fs.cpSync(srcDir, path.join(dst, entry), { recursive: true });
    }
  }
}
