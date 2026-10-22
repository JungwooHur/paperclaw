#!/usr/bin/env npx tsx
/**
 * Reads the usage-limit refusals out of the CLI bundled with the agent SDK and
 * writes them to src/usage-limit-cli-sentences.json, which
 * src/usage-limit-cli.test.ts checks the host's classifier against.
 *
 * Run it whenever the SDK pin in container/agent-runner/package.json moves:
 * that test fails until the file's version matches the pin. If the classifier
 * then fails on a sentence, the CLI reworded its refusal.
 *
 * Usage:
 *   npx tsx scripts/extract-usage-limit-sentences.ts <cli.js> [sdk-version]
 *
 * <cli.js> is the SDK's own bundle, which the SDK spawns from beside its
 * sdk.mjs, not the global claude-code the image also installs. Without a
 * version, the package.json beside it is read. To take it from the image:
 *   docker create --name sdk paperclaw-agent:latest
 *   docker cp sdk:/app/node_modules/@anthropic-ai/claude-agent-sdk /tmp/sdk
 *   docker rm sdk
 * or from the registry: npm pack @anthropic-ai/claude-agent-sdk@<version>.
 *
 * The bundle is minified and its names change per release, so nothing here
 * is found by name. The function that words a rejected request is found by
 * how it is called, and run in a vm on every combination of the values it
 * compares against, with the bundle's own reset formatter and a fixed clock.
 * When the bundle's shape no longer fits, the script stops and says where;
 * read the bundle by hand then, starting from "You've hit your".
 */
import fs from 'fs';
import path from 'path';
import vm from 'vm';

/** The zone the sentences are written in; the container runs in the host's. */
const ZONE = 'Asia/Seoul';
/** Resets to word, from the point in time each is refused at. */
const CASES: { now: string; resetsAt: string | null }[] = [
  { now: '2026-10-13T12:00:00.000Z', resetsAt: null },
  // Within a day: the time alone, with minutes only when they are not zero.
  { now: '2026-10-13T12:00:00.000Z', resetsAt: '2026-10-13T16:00:00.000Z' },
  { now: '2026-10-13T12:00:00.000Z', resetsAt: '2026-10-13T16:30:00.000Z' },
  // More than a day ahead: the date too.
  { now: '2026-10-13T12:00:00.000Z', resetsAt: '2026-10-16T14:00:00.000Z' },
  { now: '2026-10-13T12:00:00.000Z', resetsAt: '2026-10-16T14:30:00.000Z' },
  // In the next year: the year too.
  { now: '2026-12-28T03:00:00.000Z', resetsAt: '2027-01-01T16:00:00.000Z' },
];
const OUT = path.join(
  path.dirname(new URL(import.meta.url).pathname),
  '..',
  'src',
  'usage-limit-cli-sentences.json',
);
const KEYWORDS = new Set([
  'if',
  'for',
  'while',
  'switch',
  'return',
  'function',
  'catch',
  'typeof',
]);

function fail(message: string): never {
  console.error(`extract-usage-limit-sentences: ${message}`);
  process.exit(1);
}

/** The source of the function declared at `start`, braces matched. */
function functionAt(bundle: string, start: number): string {
  // '{' a block, '${' a template expression, '`' inside a template.
  const stack: string[] = [];
  for (let i = bundle.indexOf('{', start); i < bundle.length; i++) {
    const c = bundle[i];
    if (stack.at(-1) === '`') {
      if (c === '\\') i++;
      else if (c === '`') stack.pop();
      else if (c === '$' && bundle[i + 1] === '{') {
        stack.push('${');
        i++;
      }
      continue;
    }
    if (c === '"' || c === "'") {
      for (i++; i < bundle.length && bundle[i] !== c; i++) {
        if (bundle[i] === '\\') i++;
      }
    } else if (c === '`') stack.push('`');
    else if (c === '{') stack.push('{');
    else if (c === '}') {
      stack.pop();
      if (stack.length === 0) return bundle.slice(start, i + 1);
    }
  }
  return fail(`unbalanced function at offset ${start}`);
}

function functionNamed(bundle: string, name: string): string {
  const start = bundle.indexOf(`function ${name}(`);
  if (start < 0) fail(`no function ${name} in the bundle`);
  return functionAt(bundle, start);
}

/** Names a function's body calls that it does not define or receive. */
function freeCalls(source: string, zeroArgOnly = false): string[] {
  const pattern = zeroArgOnly
    ? /(?<![\w$.])([\w$]+)\(\)/g
    : /(?<![\w$.])([\w$]+)\(/g;
  const own = /^function ([\w$]+)/.exec(source)?.[1];
  const names = [...source.matchAll(pattern)].map((m) => m[1]);
  return [...new Set(names)].filter((n) => n !== own && !KEYWORDS.has(n));
}

function main(): void {
  const [cliPath, versionArg] = process.argv.slice(2);
  if (!cliPath) fail('usage: <cli.js> [sdk-version]');
  // The formatter reads the process zone, as the CLI does in the container.
  process.env.TZ = ZONE;
  const bundle = fs.readFileSync(cliPath, 'utf-8');
  const version =
    versionArg ??
    (() => {
      const pkg = path.join(path.dirname(cliPath), 'package.json');
      if (!fs.existsSync(pkg)) fail(`no ${pkg}; pass the SDK version`);
      return JSON.parse(fs.readFileSync(pkg, 'utf-8')).version as string;
    })();

  // The message for a rejected request: {message: builder(limits, ...)}.
  const builderName = /status==="rejected"\)return\{message:([\w$]+)\(/.exec(
    bundle,
  )?.[1];
  if (!builderName) fail("no builder for a rejected request's message");
  const builder = functionNamed(bundle, builderName);

  // The reset formatter is called as fmt(seconds, true): with the zone.
  const formatterName = /([\w$]+)\([\w$.]+,!0\)/.exec(builder)?.[1];
  if (!formatterName) fail(`no reset formatter called in ${builderName}`);
  const formatter = functionNamed(bundle, formatterName);

  // Functions the builder calls that return one template, like the one that
  // composes "You've hit your …", run as they are. Any other call (the plan
  // lookup) is stubbed, and so is the formatter's zone lookup.
  const own = new Map<string, string>([[formatterName, formatter]]);
  const stubbed: string[] = [];
  for (const name of freeCalls(builder)) {
    if (name === formatterName) continue;
    const start = bundle.indexOf(`function ${name}(`);
    const source = start < 0 ? '' : functionAt(bundle, start);
    if (/^function [\w$]+\([^)]*\)\{return`[^`]*`\}$/.test(source)) {
      own.set(name, source);
    } else stubbed.push(name);
  }

  // Every value the builder compares against, for every field and the plan.
  const values: (string | undefined)[] = [
    undefined,
    ...new Set([...builder.matchAll(/==="([^"]*)"/g)].map((m) => m[1])),
  ];

  let nowMs = 0;
  let plan: string | undefined;
  const sandbox: Record<string, unknown> = {
    Date: class extends Date {
      constructor(...args: unknown[]) {
        if (args.length === 0) super(nowMs);
        else super(...(args as [number]));
      }
      static now(): number {
        return nowMs;
      }
    },
  };
  for (const name of freeCalls(formatter, true)) sandbox[name] = () => ZONE;
  for (const name of stubbed) sandbox[name] = () => plan;
  vm.createContext(sandbox);
  const build = vm.runInContext(
    `${[...own.values()].join('\n')}\n${builder}\n${builderName}`,
    sandbox,
  ) as (limits: Record<string, unknown>, extra?: unknown) => string;

  const sentences = new Map<string, { now: string; resetsAt: string | null }>();
  const word = (now: string, resetsAt: string | null) => {
    nowMs = Date.parse(now);
    const at = resetsAt === null ? undefined : Date.parse(resetsAt) / 1000;
    const texts = new Set<string>();
    for (const rateLimitType of values) {
      for (const overageStatus of values) {
        for (const overageDisabledReason of values) {
          for (plan of [...values]) {
            for (const [reset, overageReset] of [
              [at, undefined],
              [undefined, at],
              [at, at],
            ]) {
              texts.add(
                build({
                  status: 'rejected',
                  rateLimitType,
                  overageStatus,
                  overageDisabledReason,
                  resetsAt: reset,
                  overageResetsAt: overageReset,
                }),
              );
            }
          }
        }
      }
    }
    return texts;
  };

  for (const { now, resetsAt } of CASES) {
    const unstated = resetsAt === null ? new Set<string>() : word(now, null);
    for (const text of word(now, resetsAt)) {
      if (typeof text !== 'string' || /undefined|NaN|Invalid/.test(text)) {
        fail(`implausible sentence ${JSON.stringify(text)}`);
      }
      // A branch that leaves the reset out words it the same either way.
      const key = `${now} ${text}`;
      if (!unstated.has(text) && !sentences.has(key)) {
        sentences.set(key, { now, resetsAt });
      }
    }
  }

  const out = {
    _comment:
      'Generated by scripts/extract-usage-limit-sentences.ts from the CLI bundled with the agent SDK. Do not edit by hand.',
    sdkVersion: version,
    zone: ZONE,
    sentences: [...sentences.entries()]
      .map(([key, value]) => ({
        text: key.slice(value.now.length + 1),
        ...value,
      }))
      .sort((a, b) =>
        a.now === b.now
          ? a.text.localeCompare(b.text)
          : a.now.localeCompare(b.now),
      ),
  };
  fs.writeFileSync(OUT, `${JSON.stringify(out, null, 2)}\n`);
  console.log(
    `${out.sentences.length} sentences from SDK ${version} -> ${path.relative(process.cwd(), OUT)}`,
  );
}

main();
