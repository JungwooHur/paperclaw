#!/usr/bin/env bash
# Run a command with PaperClaw's node on PATH: `paperclaw-node.sh node dist/index.js`.
#
# The service unit cannot name node itself: the right one is the node `npm ci`
# built the native modules (better-sqlite3) against, and another one fails to
# load them. So it is resolved here, in this order:
#   1. PAPERCLAW_NODE, the node setup ran with. setup writes it into a drop-in,
#      paperclaw.service.d/node.conf, beside the unit (setup/service.ts).
#   2. nvm's default. nvm installs node under a version-numbered directory that
#      is on no PATH systemd sets.
#   3. Whatever node is already on PATH.
# A PAPERCLAW_NODE that no longer exists (that version was uninstalled) is
# reported and skipped rather than fatal.
#
# npm and npx are node scripts (`#!/usr/bin/env node`), so putting node's
# directory on PATH, rather than calling it by path, makes them work as well.

node_bin=""
if [ -n "${PAPERCLAW_NODE:-}" ]; then
    if [ -x "$PAPERCLAW_NODE" ]; then
        node_bin="$PAPERCLAW_NODE"
    else
        echo "paperclaw-node: PAPERCLAW_NODE=$PAPERCLAW_NODE is not an executable; trying nvm, then PATH" >&2
    fi
fi
nvm_dir="${NVM_DIR:-$HOME/.nvm}"
if [ -z "$node_bin" ] && [ -s "$nvm_dir/nvm.sh" ]; then
    # shellcheck disable=SC1091
    . "$nvm_dir/nvm.sh" --no-use
    node_bin=$(nvm which default 2>/dev/null)
fi
if [ ! -x "$node_bin" ]; then
    node_bin=$(command -v node)
fi
if [ ! -x "$node_bin" ]; then
    echo "paperclaw-node: no node found (PAPERCLAW_NODE, nvm default under $nvm_dir, then PATH)" >&2
    exit 127
fi

PATH="$(dirname "$node_bin"):$PATH"
export PATH
exec "$@"
