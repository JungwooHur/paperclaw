#!/usr/bin/env bash
# Records how the CLI the runner pins acknowledges input, end to end.
#
# Compiles container/agent-runner/src inside the agent image the way its
# entrypoint does, then runs it with no network against a fake Messages API
# (container/agent-runner/test/probe/turn-attribution.mjs) and writes the
# `turn` of every output it produced to the fixture below, then drives the SDK
# directly to record what the CLI does with a reused uuid.
# container/agent-runner/test/turn-attribution-probe.test.ts checks the fixture
# came from the pinned SDK and holds what the protocol promises, so rerun this
# after an SDK bump, once the image is rebuilt. Takes about 100 s: one case
# waits out the runner's 60 s background-task nudge.
#
#   scripts/probe-turn-attribution.sh            # writes the fixture
#   PROBE_VERBOSE=1 scripts/probe-turn-attribution.sh   # also logs the runner
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="${CONTAINER_RUNTIME:-docker}"
IMAGE="${CONTAINER_IMAGE:-paperclaw-agent:latest}"
FIXTURE="$ROOT/container/agent-runner/test/turn-attribution-probe.json"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

"$RUNTIME" run --rm --network none --entrypoint bash \
  -e PROBE_VERBOSE="${PROBE_VERBOSE:-}" \
  -v "$ROOT/container/agent-runner/src:/app/src:ro" \
  -v "$ROOT/container/agent-runner/test/probe:/probe:ro" \
  "$IMAGE" -c 'set -e
    cd /app && npx tsc --outDir /tmp/dist >&2
    ln -s /app/node_modules /tmp/dist/node_modules
    node /probe/turn-attribution.mjs' >"$tmp"

mv "$tmp" "$FIXTURE"
trap - EXIT
echo "wrote $FIXTURE"
