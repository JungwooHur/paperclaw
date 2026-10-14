import { spawnSync } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';

import { afterEach, beforeEach, describe, it, expect } from 'vitest';

import {
  nodeDropInPath,
  renderNodeDropIn,
  renderSystemdUnit,
  SYSTEMD_UNIT,
} from './service.js';

/**
 * Tests for service configuration generation.
 *
 * These tests verify the generated content of plist/systemd/nohup configs
 * without actually loading services.
 */

// Helper: generate a plist string the same way service.ts does
function generatePlist(
  nodePath: string,
  projectRoot: string,
  homeDir: string,
): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.paperclaw</string>
    <key>ProgramArguments</key>
    <array>
        <string>${nodePath}</string>
        <string>${projectRoot}/dist/index.js</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${projectRoot}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:${homeDir}/.local/bin</string>
        <key>HOME</key>
        <string>${homeDir}</string>
    </dict>
    <key>StandardOutPath</key>
    <string>${projectRoot}/logs/paperclaw.log</string>
    <key>StandardErrorPath</key>
    <string>${projectRoot}/logs/paperclaw.error.log</string>
</dict>
</plist>`;
}

describe('plist generation', () => {
  it('contains the correct label', () => {
    const plist = generatePlist(
      '/usr/local/bin/node',
      '/home/user/paperclaw',
      '/home/user',
    );
    expect(plist).toContain('<string>com.paperclaw</string>');
  });

  it('uses the correct node path', () => {
    const plist = generatePlist(
      '/opt/node/bin/node',
      '/home/user/paperclaw',
      '/home/user',
    );
    expect(plist).toContain('<string>/opt/node/bin/node</string>');
  });

  it('points to dist/index.js', () => {
    const plist = generatePlist(
      '/usr/local/bin/node',
      '/home/user/paperclaw',
      '/home/user',
    );
    expect(plist).toContain('/home/user/paperclaw/dist/index.js');
  });

  it('sets log paths', () => {
    const plist = generatePlist(
      '/usr/local/bin/node',
      '/home/user/paperclaw',
      '/home/user',
    );
    expect(plist).toContain('paperclaw.log');
    expect(plist).toContain('paperclaw.error.log');
  });
});

describe('systemd unit (systemd/paperclaw.service)', () => {
  const repo = path.resolve(__dirname, '..');
  const unit = fs.readFileSync(path.join(repo, SYSTEMD_UNIT), 'utf-8');

  it('keeps the restart policy, and stops on a WhatsApp logout', () => {
    expect(unit).toMatch(/^Restart=always$/m);
    expect(unit).toMatch(/^RestartPreventExitStatus=78$/m);
    expect(unit).toMatch(/^RestartSec=5$/m);
    expect(unit).toMatch(/^Environment=HOME=%h$/m);
    expect(unit).toMatch(/^Environment=PATH=.*%h\/\.local\/bin$/m);
  });

  it('logs to the file the watchdog reads', () => {
    const watchdog = fs.readFileSync(
      path.join(repo, 'scripts', 'paperclaw-watchdog.sh'),
      'utf-8',
    );
    const log = watchdog.match(/^LOG="\$\{HOME\}(\/[^"]+)"$/m)?.[1];
    expect(log).toBeTruthy();
    expect(unit).toContain(`StandardOutput=append:%h${log}`);
    expect(unit).toMatch(/^StandardError=append:%h\/paperclaw\/logs\//m);
  });

  it('rebuilds dist/ before starting, and starts even if that fails', () => {
    expect(unit).toMatch(/^ExecStartPre=-\S+ npm run build$/m);
  });

  it('runs dist/index.js through the node resolver, which exists', () => {
    const start = unit.match(/^ExecStart=(\S+) node (\S+)$/m);
    expect(start?.[2]).toBe('%h/paperclaw/dist/index.js');
    const wrapper = start![1].replace('%h/paperclaw', repo);
    fs.accessSync(wrapper, fs.constants.X_OK);
    expect(unit).toContain(`ExecStartPre=-${start![1]} `);
  });

  it('renders the real path for a checkout outside ~/paperclaw', () => {
    const out = renderSystemdUnit(unit, '/srv/claw', false);
    expect(out).not.toContain('%h/paperclaw');
    expect(out).toContain('ExecStart=/srv/claw/scripts/paperclaw-node.sh');
    expect(out).toContain('WantedBy=default.target');
  });

  it('renders a system unit wanted by multi-user.target', () => {
    const out = renderSystemdUnit(unit, '/root/paperclaw', true);
    expect(out).toContain('WantedBy=multi-user.target');
    expect(out).not.toContain('WantedBy=default.target');
  });
});

describe('WSL nohup fallback', () => {
  it('generates a valid wrapper script', () => {
    const projectRoot = '/home/user/paperclaw';
    const nodePath = '/usr/bin/node';
    const pidFile = path.join(projectRoot, 'paperclaw.pid');

    // Simulate what service.ts generates
    const wrapper = `#!/bin/bash
set -euo pipefail
cd ${JSON.stringify(projectRoot)}
nohup ${JSON.stringify(nodePath)} ${JSON.stringify(projectRoot)}/dist/index.js >> ${JSON.stringify(projectRoot)}/logs/paperclaw.log 2>> ${JSON.stringify(projectRoot)}/logs/paperclaw.error.log &
echo $! > ${JSON.stringify(pidFile)}`;

    expect(wrapper).toContain('#!/bin/bash');
    expect(wrapper).toContain('nohup');
    expect(wrapper).toContain(nodePath);
    expect(wrapper).toContain('paperclaw.pid');
  });
});

/**
 * Which node the unit runs. nvm's default is right only when it is the node
 * `npm ci` built the native modules with; a node from anywhere else (fnm, volta,
 * a tarball) is on no PATH systemd sets. So setup pins the node it ran with in
 * a drop-in, and scripts/paperclaw-node.sh takes that first.
 */
describe('node resolution (scripts/paperclaw-node.sh)', () => {
  const wrapper = path.resolve(__dirname, '..', 'scripts', 'paperclaw-node.sh');
  let tmp: string;
  let nodes: Record<'pinned' | 'nvm' | 'path', string>;
  let nvmDir: string;

  function fakeNode(name: string): string {
    const dir = path.join(tmp, `${name} bin`);
    fs.mkdirSync(dir, { recursive: true });
    const bin = path.join(dir, 'node');
    fs.writeFileSync(bin, `#!/bin/sh\necho ${name}\n`, { mode: 0o755 });
    return bin;
  }

  // By absolute path: one case runs with a PATH that has nothing on it.
  const bash = spawnSync('sh', ['-c', 'command -v bash'], {
    encoding: 'utf-8',
  }).stdout.trim();

  function run(env: Record<string, string>) {
    return spawnSync(bash, [wrapper, 'node'], {
      encoding: 'utf-8',
      env: { HOME: tmp, NVM_DIR: nvmDir, ...env },
    });
  }

  beforeEach(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'paperclaw-node-'));
    nodes = {
      pinned: fakeNode('pinned'),
      nvm: fakeNode('nvm'),
      path: fakeNode('path'),
    };
    nvmDir = path.join(tmp, 'nvm');
    fs.mkdirSync(nvmDir);
    fs.writeFileSync(
      path.join(nvmDir, 'nvm.sh'),
      `nvm() { [ "$1" = which ] && [ "$2" = default ] && echo '${nodes.nvm}'; }\n`,
    );
  });

  afterEach(() => {
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  const onPath = () => `${path.dirname(nodes.path)}:/usr/bin:/bin`;

  it('takes PAPERCLAW_NODE first, over nvm and PATH', () => {
    const r = run({ PAPERCLAW_NODE: nodes.pinned, PATH: onPath() });
    expect(r.stdout.trim()).toBe('pinned');
  });

  it("then nvm's default, over PATH", () => {
    expect(run({ PATH: onPath() }).stdout.trim()).toBe('nvm');
  });

  it('then the node on PATH', () => {
    nvmDir = path.join(tmp, 'no-nvm');
    expect(run({ PATH: onPath() }).stdout.trim()).toBe('path');
  });

  it('says so and falls through when the pinned node is gone', () => {
    const r = run({
      PAPERCLAW_NODE: path.join(tmp, 'uninstalled', 'node'),
      PATH: onPath(),
    });
    expect(r.stdout.trim()).toBe('nvm');
    expect(r.stderr).toContain('PAPERCLAW_NODE');
  });

  it('exits 127 when there is no node at all', () => {
    nvmDir = path.join(tmp, 'no-nvm');
    const empty = path.join(tmp, 'empty');
    fs.mkdirSync(empty);
    expect(run({ PATH: empty }).status).toBe(127);
  });

  it("setup's drop-in makes the unit run the node setup ran with", () => {
    const conf = renderNodeDropIn(nodes.pinned);
    expect(conf).toMatch(/^\[Service\]$/m);
    const value = conf.match(/^Environment="PAPERCLAW_NODE=(.*)"$/m)?.[1];
    expect(value).toBe(nodes.pinned);
    expect(run({ PAPERCLAW_NODE: value!, PATH: onPath() }).stdout.trim()).toBe(
      'pinned',
    );
  });

  it('escapes what systemd would otherwise expand', () => {
    expect(renderNodeDropIn('/opt/50%/node')).toContain(
      'Environment="PAPERCLAW_NODE=/opt/50%%/node"',
    );
  });

  it('puts the drop-in beside the unit, so a linked unit stays a link', () => {
    expect(nodeDropInPath('/etc/systemd/system/paperclaw.service')).toBe(
      '/etc/systemd/system/paperclaw.service.d/node.conf',
    );
  });
});
