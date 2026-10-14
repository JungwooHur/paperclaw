#!/usr/bin/env bash
# Run a command with PaperClaw's node on PATH: `paperclaw-node.sh node dist/index.js`.
#
# The service unit cannot name node itself. nvm installs it under a
# version-numbered directory that is on no PATH systemd sets, and a path written
# into the unit breaks on the next `nvm install`. So it is resolved here, the way
# an interactive shell would: nvm's default first — the node `npm ci` built the
# native modules (better-sqlite3) against, so another one would fail to load
# them — then whatever node is already on PATH.
#
# npm and npx are node scripts (`#!/usr/bin/env node`), so putting node's
# directory on PATH, rather than calling it by path, makes them work as well.

node_bin=""
nvm_dir="${NVM_DIR:-$HOME/.nvm}"
if [ -s "$nvm_dir/nvm.sh" ]; then
    # shellcheck disable=SC1091
    . "$nvm_dir/nvm.sh" --no-use
    node_bin=$(nvm which default 2>/dev/null)
fi
if [ ! -x "$node_bin" ]; then
    node_bin=$(command -v node)
fi
if [ ! -x "$node_bin" ]; then
    echo "paperclaw-node: no node found (nvm default under $nvm_dir, then PATH)" >&2
    exit 127
fi

PATH="$(dirname "$node_bin"):$PATH"
export PATH
exec "$@"
