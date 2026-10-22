#!/usr/bin/env npx tsx
/**
 * Reads what the CLI bundled with the agent SDK writes as a turn's result in
 * place of an answer, and writes it to src/usage-limit-cli-sentences.json:
 *
 * - `sentences`: its usage-limit refusals, with the reset each one states,
 *   which src/usage-limit-cli.test.ts checks the classifier against;
 * - `errors`: its error messages, as templates, from which src/usage-limit.ts
 *   builds the check that tells them from an answer.
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
 * is found by name. The functions that word a refusal or an error are found
 * by their shape and how they are called, and run in a vm with the bundle's
 * own producers, a fixed clock, and stubs for every lookup they make. When the
 * bundle's shape no longer fits, the script stops and says where; read the
 * bundle by hand then, starting from "You've hit your" or "isApiErrorMessage".
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
/**
 * How an error template marks the parts the CLI fills in: the failed
 * request's own error message, and any other value it interpolates.
 */
const MESSAGE = '{message}';
const VALUE = '{value}';
/** A function this long or longer is not run, only read where it calls. */
const RUNNABLE_FUNCTION_CHARS = 20_000;
const MAX_RUNS = 2_000_000;
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

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Index of the first character at or after `start`, outside any bracket,
 * string or template that opens after it, that is one of `stops`; -1 when
 * none comes before `limit`. Regex literals and comments are not skipped.
 */
function scanTo(
  src: string,
  start: number,
  stops: string,
  limit = src.length,
): number {
  // '(' '[' '{' a bracket, '${' a template expression, '`' inside a template.
  const stack: string[] = [];
  const end = Math.min(src.length, limit);
  for (let i = start; i < end; i++) {
    const c = src[i];
    if (stack.at(-1) === '`') {
      if (c === '\\') i++;
      else if (c === '`') stack.pop();
      else if (c === '$' && src[i + 1] === '{') {
        stack.push('${');
        i++;
      }
      continue;
    }
    if (stack.length === 0 && stops.includes(c)) return i;
    if (c === '"' || c === "'") {
      for (i++; i < end && src[i] !== c; i++) {
        if (src[i] === '\\') i++;
      }
    } else if (c === '`') stack.push('`');
    else if (c === '(' || c === '[' || c === '{') stack.push(c);
    else if (c === ')' || c === ']' || c === '}') stack.pop();
  }
  return -1;
}

/** Index just past the function declared at `start`; -1 past `limit`. */
function functionEnd(src: string, start: number, limit = src.length): number {
  const paren = src.indexOf('(', start);
  const close = scanTo(src, paren + 1, ')', limit);
  if (close < 0) return -1;
  const open = src.indexOf('{', close);
  const end = scanTo(src, open + 1, '}', limit);
  return end < 0 ? -1 : end + 1;
}

/** The source of the function declared at `start`. */
function functionAt(bundle: string, start: number): string {
  const end = functionEnd(bundle, start);
  if (end < 0) fail(`unbalanced function at offset ${start}`);
  return bundle.slice(start, end);
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

/** The usage-limit refusals, with the reset each one states. */
function refusalSentences(
  bundle: string,
): { text: string; now: string; resetsAt: string | null }[] {
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

  return [...sentences.entries()]
    .map(([key, value]) => ({
      text: key.slice(value.now.length + 1),
      ...value,
    }))
    .sort((a, b) =>
      a.now === b.now
        ? a.text.localeCompare(b.text)
        : a.now.localeCompare(b.now),
    );
}

/**
 * The bundle's own names, resolved in a vm the way the bundle resolves them,
 * with stand-ins for what cannot run outside it. Anything a function reads
 * that is neither a builtin, a function the bundle declares once, nor a name
 * it gives one literal value once is a value the CLI fills in at run time.
 */
class BundleScope {
  readonly context = vm.createContext({});
  /** Stands for a value the CLI fills in; it prints as VALUE_TOKEN. */
  readonly variable: unknown;
  /** Names resolved to something other than the bundle's own definition. */
  readonly overrides = new Map<string, unknown>();
  private readonly global = vm.runInContext('globalThis', this.context);
  private readonly cache = new Map<string, unknown>();
  static readonly VALUE_TOKEN = '\u0001value\u0001';

  constructor(private readonly bundle: string) {
    const token = BundleScope.VALUE_TOKEN;
    const variable: unknown = new Proxy(function () {}, {
      get: (_target, key) =>
        key === Symbol.toPrimitive
          ? () => token
          : typeof key === 'symbol'
            ? undefined
            : variable,
      apply: () => variable,
    });
    this.variable = variable;
    this.global.__scope = new Proxy(
      {},
      {
        has: (_target, key) => typeof key === 'string' && !(key in this.global),
        get: (_target, key) =>
          typeof key === 'string' ? this.resolve(key) : undefined,
      },
    );
  }

  /** Evaluates `source` with its free names resolved through this scope. */
  evaluate(source: string): unknown {
    return vm.runInContext(`with(__scope){(${source})}`, this.context);
  }

  /** A string that tests and prints as `text`, and has it as every field. */
  chameleon(text: string): unknown {
    return new Proxy(function () {}, {
      get: (_target, key) =>
        key === Symbol.toPrimitive
          ? () => text
          : typeof key === 'symbol'
            ? undefined
            : text,
    });
  }

  isBuiltin(name: string): boolean {
    return name in this.global;
  }

  isVariable(value: unknown): boolean {
    return value === this.variable;
  }

  private resolve(name: string): unknown {
    if (this.overrides.has(name)) return this.overrides.get(name);
    if (this.cache.has(name)) return this.cache.get(name);
    const value = this.lookUp(name);
    this.cache.set(name, value);
    return value;
  }

  private lookUp(name: string): unknown {
    const declaration = `function ${name}(`;
    const start = this.bundle.indexOf(declaration);
    if (start >= 0 && this.bundle.indexOf(declaration, start + 1) < 0) {
      // A function of the values the CLI fills in fills one in too; one of
      // constants is a producer, like the formatter of a size limit.
      const fn = this.evaluate(functionAt(this.bundle, start)) as (
        ...args: unknown[]
      ) => unknown;
      return (...args: unknown[]) =>
        args.every((a) => a === null || !/object|function/.test(typeof a))
          ? fn(...args)
          : this.variable;
    }
    const assignments = [
      ...this.bundle.matchAll(
        new RegExp(`(?<![\\w$.])${escapeRegExp(name)}=(?![=>])`, 'g'),
      ),
    ];
    if (assignments.length === 1) {
      const at = assignments[0].index + assignments[0][0].length;
      const literal =
        /^(?:"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`[^`$\\]*`|\d+(?:\.\d+)?(?![\w$.]))/.exec(
          this.bundle.slice(at, at + 2000),
        )?.[0];
      if (literal) return vm.runInNewContext(literal);
    }
    return this.variable;
  }
}

interface ErrorSite {
  index: number;
  /** The source of the call's `content:` value. */
  content: string;
}

/**
 * The texts the CLI writes as a turn's result when a request fails. Every one
 * is made by one constructor, the assistant message flagged isApiErrorMessage
 * that the result reports as is_error, so its callers are all there is. A
 * caller that is a plain function, like the one that turns a failed request's
 * error into the message, is run on every value it tells apart; the rest are
 * read where they call. Non-interactive, as under the SDK: stdout is a pipe.
 */
function errorTemplates(bundle: string): string[] {
  const ctor =
    /function ([\w$]+)\(\{content:[\w$]+[^)]*\}\)\{return [\w$]+\(\{content:\[\{type:"text",text:[^\]]*\}\],isApiErrorMessage:!0/.exec(
      bundle,
    )?.[1];
  if (!ctor) fail('no constructor for an API error message');
  // The switch the CLI words its errors by: interactive or not.
  const mode = /function ([\w$]+)\(\)\{return!([\w$]+)\.isInteractive\}/.exec(
    bundle,
  )?.[1];
  if (!mode) fail('no function that tells an interactive session');

  const definition = bundle.indexOf(`function ${ctor}(`) + 'function '.length;
  const sites: ErrorSite[] = [];
  for (const m of bundle.matchAll(
    new RegExp(`(?<![\\w$.])${escapeRegExp(ctor)}\\(\\{`, 'g'),
  )) {
    if (m.index === definition) continue;
    const open = m.index + m[0].length - 1;
    const close = scanTo(bundle, open + 1, '}');
    const args = bundle.slice(open + 1, close);
    const key = /(?:^|,)content:/.exec(args);
    if (!key) fail(`no content in the error message at offset ${m.index}`);
    const from = key.index + key[0].length;
    const to = scanTo(args, from, ',}');
    sites.push({
      index: m.index,
      content: args.slice(from, to < 0 ? undefined : to),
    });
  }

  // The plain functions the sites are in; a site in none is read in place.
  const byFunction = new Map<number, ErrorSite[]>();
  const inPlace: ErrorSite[] = [];
  for (const site of sites) {
    const start = enclosingFunction(bundle, site.index);
    if (start === null) inPlace.push(site);
    else byFunction.set(start, [...(byFunction.get(start) ?? []), site]);
  }

  const scope = new BundleScope(bundle);
  scope.overrides.set(mode, () => true);
  const templates = new Set<string>();
  for (const [start, fnSites] of byFunction) {
    for (const t of runErrorFunction(bundle, scope, ctor, start, fnSites)) {
      templates.add(t);
    }
  }
  for (const site of inPlace) {
    for (const t of readErrorSite(bundle, scope, site)) templates.add(t);
  }
  console.log(
    `${sites.length} calls make an error message: ${sites.length - inPlace.length} in ${byFunction.size} functions run, ${inPlace.length} read in place.`,
  );
  return [...templates].map((t) => checkedTemplate(t)).sort();
}

/**
 * Start of the plain function (not async, not a generator, short enough to
 * run) declared around `at`, or null when there is none.
 */
function enclosingFunction(bundle: string, at: number): number | null {
  for (let pos = at; ; ) {
    const start = bundle.lastIndexOf('function ', pos - 1);
    if (start < 0 || at - start > RUNNABLE_FUNCTION_CHARS) return null;
    pos = start;
    if (!/^function [\w$]+\(/.test(bundle.slice(start, start + 100))) continue;
    const end = functionEnd(bundle, start, start + RUNNABLE_FUNCTION_CHARS);
    if (end < 0 || end <= at) continue;
    return bundle.slice(start - 6, start) === 'async ' ? null : start;
  }
}

/**
 * Runs the function at `start` on every value it tells apart, and returns the
 * templates of the error messages it makes. Its parameters take every string
 * they are compared with, or a value the CLI fills in; one it tests with
 * instanceof also takes an error of every class it names, with every status
 * and message fragment it looks for. Each lookup it makes is a choice point,
 * and every path through it is run. Every error message it makes must be
 * reached, or the script stops.
 */
function runErrorFunction(
  bundle: string,
  scope: BundleScope,
  ctor: string,
  start: number,
  sites: ErrorSite[],
): string[] {
  const original = functionAt(bundle, start);
  const name = /^function ([\w$]+)/.exec(original)![1];
  const params = /^function [\w$]+\(([^)]*)\)/
    .exec(original)![1]
    .split(',')
    .filter(Boolean);
  if (params.some((p) => !/^[\w$]+$/.test(p))) {
    fail(`${name} takes parameters that are not plain names`);
  }

  // Each error message it makes, tagged so a run says which one it reached.
  let tag = 0;
  const source = original.replace(
    new RegExp(`(?<![\\w$.])${escapeRegExp(ctor)}\\(\\{`, 'g'),
    () => `${ctor}(${tag++},{`,
  );
  if (tag !== sites.length) fail(`${name}: miscounted its error messages`);
  const contents = sites.map((s) => s.content).join('\n');

  // Calls outside an error message's content are lookups: config, plan,
  // feature flags, telemetry. They return nothing, or any string the function
  // compares against, as the value and as every field.
  const compared = [
    ...new Set(
      [...source.matchAll(/[!=]=="((?:[^"\\]|\\.)*)"/g)].map((m) => m[1]),
    ),
  ];
  const choices: unknown[] = [
    undefined,
    ...(compared.length > 0 ? compared : ['']).map((s) => scope.chameleon(s)),
  ];
  // The error classes it tells apart, with the bundle's own hierarchy.
  const errorParams = params.filter((p) =>
    new RegExp(`(?<![\\w$.])${escapeRegExp(p)} instanceof `).test(source),
  );
  const classes = defineErrorClasses(bundle, scope, source);

  const own = new Set([name, ctor, ...params]);
  const inContent = new Set(freeCalls(contents));
  const lookups = freeCalls(source).filter(
    (n) =>
      !own.has(n) &&
      !inContent.has(n) &&
      !scope.overrides.has(n) &&
      !scope.isBuiltin(n),
  );
  let path: number[] = [];
  let depth = 0;
  const choose = () => {
    if (depth === path.length) path.push(0);
    return choices[path[depth++]];
  };
  for (const lookup of lookups) scope.overrides.set(lookup, choose);

  let made: { site: number; content: unknown }[] = [];
  scope.overrides.set(ctor, (site: number, message: { content: unknown }) => {
    made.push({ site, content: message.content });
    return message;
  });
  const fn = scope.evaluate(source) as (...args: unknown[]) => unknown;

  const domains = params.map((p) => {
    const values: { value: unknown; message?: string }[] = [
      { value: scope.variable },
      ...[
        ...source.matchAll(
          new RegExp(
            `(?<![\\w$.])${escapeRegExp(p)}[!=]=="((?:[^"\\\\]|\\\\.)*)"`,
            'g',
          ),
        ),
      ].map((m) => ({ value: m[1] })),
    ];
    if (errorParams.includes(p)) {
      values.push(...craftedErrors(scope, source, p, classes));
    }
    return values;
  });

  const reached = new Set<number>();
  const templates = new Set<string>();
  let runs = 0;
  for (const args of product(domains)) {
    path = [];
    do {
      depth = 0;
      made = [];
      if (++runs > MAX_RUNS) fail(`${name}: more than ${MAX_RUNS} runs`);
      try {
        fn(...args.map((a) => a.value));
      } catch {
        // A path the stand-ins cannot take, like reading a field of nothing.
      }
      for (const { site, content } of made) {
        reached.add(site);
        // Not text: what a lookup returned, like the refusal's builder.
        if (typeof content !== 'string') continue;
        const message = args.find((a) => a.message)?.message;
        templates.add(toTemplate(content, message));
      }
      path.length = depth;
      while (path.length && ++path[path.length - 1] >= choices.length) {
        path.pop();
      }
    } while (path.length);
  }
  for (const lookup of lookups) scope.overrides.delete(lookup);
  scope.overrides.delete(ctor);

  const missed = sites.filter((_s, i) => !reached.has(i));
  if (missed.length > 0) {
    fail(
      `${name}: no run reached the error message at offset ${missed
        .map((s) => s.index)
        .join(', ')}`,
    );
  }
  return [...templates];
}

/**
 * Stand-ins for the error classes `source` tests with instanceof, each
 * extending the stand-in of the class the bundle extends, so an error is an
 * instance of every class the real one is.
 */
function defineErrorClasses(
  bundle: string,
  scope: BundleScope,
  source: string,
): Map<string, unknown> {
  const classes = new Map<string, unknown>([
    ['Error', scope.evaluate('Error')],
  ]);
  const define = (name: string): unknown => {
    if (classes.has(name)) return classes.get(name);
    const parent =
      new RegExp(
        `(?<![\\w$.])${escapeRegExp(name)}=class ${escapeRegExp(name)} extends ([\\w$]+)`,
      ).exec(bundle)?.[1] ?? 'Error';
    const base = define(parent);
    scope.overrides.set('__base', base);
    const stand = scope.evaluate(`class ${name} extends __base {}`);
    scope.overrides.delete('__base');
    scope.overrides.set(name, stand);
    classes.set(name, stand);
    return stand;
  };
  for (const m of source.matchAll(/ instanceof ([\w$]+)/g)) define(m[1]);
  return classes;
}

/**
 * An error of every class in `classes`, with every status `source` compares
 * the error's with and every message made of one or two of the fragments it
 * looks for. Any other field it reads is
 * either missing or a value the CLI fills in. The message ends in a token, so
 * a message carried into the text can be told from a sentence that happens to
 * repeat a fragment.
 */
function craftedErrors(
  scope: BundleScope,
  source: string,
  param: string,
  classes: Map<string, unknown>,
): { value: unknown; message: string }[] {
  const p = escapeRegExp(param);
  const statuses = [
    undefined,
    ...new Set(
      [...source.matchAll(new RegExp(`${p}\\.status===(\\d+)`, 'g'))].map((m) =>
        Number(m[1]),
      ),
    ),
  ];
  const fragments = new Set<string>();
  for (const m of source.matchAll(
    /\.includes\(("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[\w$]+)\)/g,
  )) {
    const value = /^["']/.test(m[1])
      ? vm.runInNewContext(m[1])
      : scope.evaluate(m[1]);
    if (typeof value === 'string') fragments.add(value);
  }
  for (const m of source.matchAll(
    new RegExp(`(\\/(?:[^/\\\\\\n]|\\\\.)+\\/[a-z]*)\\.test\\(${p}\\.`, 'g'),
  )) {
    fragments.add(sampleOf(m[1]));
  }
  const fields = [
    ...new Set(
      [...source.matchAll(new RegExp(`${p}(?:\\?)?\\.([\\w$]+)`, 'g'))].map(
        (m) => m[1],
      ),
    ),
  ].filter((f) => f !== 'message' && f !== 'status');

  const list = [...fragments];
  const messages = [
    '',
    ...list,
    ...list.flatMap((a, i) => list.slice(i + 1).map((b) => `${a} ${b}`)),
  ].map((m) => `${m}\u0002`);
  const errors: { value: unknown; message: string }[] = [];
  for (const cls of classes.values()) {
    for (const status of statuses) {
      for (const message of messages) {
        for (const filled of product(
          fields.map(() => [undefined, scope.variable]),
        )) {
          scope.overrides.set('__class', cls);
          const error = scope.evaluate(
            'Object.create(__class.prototype)',
          ) as Record<string, unknown>;
          scope.overrides.delete('__class');
          error.message = message;
          error.status = status;
          fields.forEach((f, i) => {
            error[f] = filled[i];
          });
          errors.push({ value: error, message });
        }
      }
    }
  }
  return errors;
}

/** A text a regex literal from the bundle matches, or the script stops. */
function sampleOf(literal: string): string {
  const body = literal.slice(1, literal.lastIndexOf('/'));
  const sample = body
    .replace(/\\d\+|\\d/g, '1')
    .replace(/\\s\+|\\s/g, ' ')
    .replace(/\\(.)/g, '$1');
  const regex = vm.runInNewContext(literal) as RegExp;
  if (!regex.test(sample)) fail(`no sample text for ${literal}`);
  return sample;
}

/**
 * What a call made of an error's content can say, read where it stands: its
 * content with the names it reads resolved through the scope, the caller's
 * own variables as values the CLI fills in. Content that is only a caught
 * error's message is what the classes the call tests that error with are made
 * with: what they are constructed with, and what their constructors compose.
 */
function readErrorSite(
  bundle: string,
  scope: BundleScope,
  site: ErrorSite,
): string[] {
  const own = /^([\w$]+)\.message$/.exec(site.content)?.[1];
  if (!own) {
    let content: unknown;
    try {
      content = scope.evaluate(site.content);
    } catch (err) {
      fail(`unreadable error message at offset ${site.index}: ${err}`);
    }
    if (typeof content !== 'string' || scope.isVariable(content)) {
      fail(`unreadable error message at offset ${site.index}`);
    }
    return [checkedTemplate(toTemplate(content), site.index)];
  }
  const guard = guardOf(bundle, site.index);
  const classes = [
    ...new Set(
      [
        ...guard.matchAll(
          new RegExp(
            `(?<![\\w$.])${escapeRegExp(own)} instanceof ([\\w$]+)`,
            'g',
          ),
        ),
      ].map((m) => m[1]),
    ),
  ];
  if (classes.length === 0) {
    fail(`an error message at offset ${site.index} of an error of no class`);
  }
  const templates: string[] = [];
  for (const cls of classes) {
    const found = classMessages(bundle, scope, cls);
    if (found.length === 0) fail(`no message the class ${cls} is made with`);
    templates.push(...found.map((t) => checkedTemplate(t, site.index)));
  }
  return templates;
}

/** The condition of the `if` whose block starts with the call at `at`. */
function guardOf(bundle: string, at: number): string {
  const lead = bundle.slice(Math.max(0, at - 40), at);
  const brace = /\)\{\s*(?:yield\s*|return\s*)?$/.exec(lead);
  if (!brace) fail(`no condition guards the call at offset ${at}`);
  const close = at - lead.length + brace.index;
  for (let pos = close; pos > close - 5000; ) {
    const open = bundle.lastIndexOf('if(', pos - 1);
    if (open < 0) break;
    if (scanTo(bundle, open + 3, ')', close + 1) === close) {
      return bundle.slice(open + 3, close);
    }
    pos = open;
  }
  return fail(`no condition guards the call at offset ${at}`);
}

/** The messages an error class is made with, as templates. */
function classMessages(
  bundle: string,
  scope: BundleScope,
  cls: string,
): string[] {
  const c = escapeRegExp(cls);
  const expressions: string[] = [];
  for (const m of bundle.matchAll(new RegExp(`(?<![\\w$.])new ${c}\\(`, 'g'))) {
    const from = m.index + m[0].length;
    expressions.push(bundle.slice(from, scanTo(bundle, from, ',)')));
  }
  const declared = new RegExp(
    `(?<![\\w$.])${c}=class ${c} extends [\\w$]+\\{constructor\\(`,
  ).exec(bundle);
  if (declared) {
    const open = scanTo(bundle, declared.index + declared[0].length, ')');
    const body = bundle.indexOf('{', open);
    const end = scanTo(bundle, body + 1, '}');
    const constructor = bundle.slice(body + 1, end);
    for (const m of constructor.matchAll(
      /(?<![\w$.])(?:[\w$]+=(?![=>])|super\()/g,
    )) {
      const from = m.index + m[0].length;
      expressions.push(
        constructor.slice(from, scanTo(constructor, from, ',;)}')),
      );
    }
  }
  const templates: string[] = [];
  for (const expression of expressions) {
    let value: unknown;
    try {
      value = scope.evaluate(expression);
    } catch {
      continue;
    }
    if (typeof value !== 'string' || !/[a-z]{3}/i.test(value)) continue;
    if (value.replaceAll(BundleScope.VALUE_TOKEN, '').trim() === '') continue;
    templates.push(toTemplate(value));
  }
  return templates;
}

/** `text` with what the CLI fills in marked, the message first. */
function toTemplate(text: string, message?: string): string {
  let template = message ? text.split(message).join(MESSAGE) : text;
  template = template.split(BundleScope.VALUE_TOKEN).join(VALUE);
  if (/[\u0001\u0002]/.test(template)) {
    fail(`part of a filled-in value in ${JSON.stringify(template)}`);
  }
  return template;
}

/** `template`, unless its fixed parts show a stand-in leaked into it. */
function checkedTemplate(template: string, at?: number): string {
  const fixed = template.split(MESSAGE).join('').split(VALUE).join('');
  if (/undefined|NaN|\[object|[{}]/.test(fixed) || fixed.trim() === '') {
    const where = at === undefined ? '' : ` at offset ${at}`;
    fail(`implausible error message${where}: ${template}`);
  }
  return template;
}

function* product<T>(lists: T[][]): Generator<T[]> {
  if (lists.length === 0) {
    yield [];
    return;
  }
  const [first, ...rest] = lists;
  for (const item of first) {
    for (const tail of product(rest)) yield [item, ...tail];
  }
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

  const out = {
    _comment: `Generated by scripts/extract-usage-limit-sentences.ts from the CLI bundled with the agent SDK. Do not edit by hand. In errors, ${MESSAGE} is the failed request's own error message and ${VALUE} any other value the CLI fills in.`,
    sdkVersion: version,
    zone: ZONE,
    sentences: refusalSentences(bundle),
    errors: errorTemplates(bundle),
  };
  fs.writeFileSync(OUT, `${JSON.stringify(out, null, 2)}\n`);
  console.log(
    `${out.sentences.length} refusals and ${out.errors.length} error templates from SDK ${version} -> ${path.relative(process.cwd(), OUT)}`,
  );
}

main();
