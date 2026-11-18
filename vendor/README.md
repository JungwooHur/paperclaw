# Third-party code

Everything under `vendor/` is **not** covered by this repository's root `LICENSE`
(MIT, Copyright (c) 2026 Gavriel). Each vendored project keeps its own licence file
and copyright, which is what permits redistributing it here.

| Directory | Upstream | Version | Licence |
|---|---|---|---|
| `notebooklm-py/` | https://github.com/teng-lin/notebooklm-py | 0.8.6 | MIT — `notebooklm-py/LICENSE`, Copyright (c) 2026 Teng Lin |

Why it is vendored rather than installed from PyPI: the agent container builds
offline from the repo, and the exact version matters — this client drives the
NotebookLM web app, so it breaks whenever Google changes it, and pinning the tree
makes "which version is actually running" answerable from the commit alone.

`tests/`, `examples/`, `PKG-INFO` and the generated `notebooklm-py/docs/diagrams/`
are dropped from the sdist; MIT does not require redistributing the complete
package, only the copyright and permission notice, which `notebooklm-py/LICENSE`
carries verbatim.

## A stale notebooklm-py lies about auth (keep it current)

`notebooklm-py` is an **unofficial CLI that drives the NotebookLM web app** with a
Playwright cookie session, so it breaks whenever Google changes the app. The
container installs it from the tracked copy in `vendor/notebooklm-py`.

A stale CLI reports a perfectly valid session as `Authentication expired or invalid
… Run 'notebooklm login' to re-authenticate`. Every paper in a batch then fails
within seconds, and each is recorded as permanently `failed`. The error message is
confidently wrong and sends you down an auth rabbit hole: interactive `notebooklm
login` rounds leave the cookies untouched, because they were never the problem.

**The decisive test — do this before ever blaming auth:** run the SAME
`~/.notebooklm/storage_state.json` against a current CLI in a throwaway venv:
```bash
python3 -m venv /tmp/nlm && /tmp/nlm/bin/pip install -q notebooklm-py
/tmp/nlm/bin/notebooklm list --json     # works here + fails on the installed one = the CLI is stale, not the session
```
**`notebooklm doctor` is not trustworthy for this** — it only checks that an SID
cookie exists in the file and happily prints `Auth ✓ pass` while every real request
redirects to the Google sign-in page. `notebooklm list --json` is the only honest
check.

**The auth error can also mean the SERVICE MOVED.** Google renamed the app host from
`notebooklm.google.com` to `notebook.google.com` (the old name is now a 301), and a
CLI pinned to the old host reports it as `Authentication expired or invalid.
Redirected to: accounts.google.com` — the same sentence a real expiry produces. The
A/B test above does NOT separate them when BOTH versions predate the move, and the
tell is different: `notebooklm login` opens the browser, the user signs in
successfully, and the CLI still times out with `Login not detected within 5
minutes`, because it is waiting for `wait_for_url("<old host>/**")` on a URL that no
longer exists. **When login "works" but is never detected, compare the expected host
with the real one** — that is a one-line check:
```bash
python3 -c "from notebooklm.config import get_base_host; print(get_base_host())"
curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}\n' https://notebooklm.google.com/
```

**Two more traps, both wearing an auth costume.** (1) The session path MOVED between
versions: 0.3.4 kept `~/.notebooklm/storage_state.json`, 0.7.3+ keeps
`~/.notebooklm/profiles/<profile>/storage_state.json`. Checking the old path shows
"no session" on a host that has one. **Check the file's mtime after a login** — if
it did not change, the login never saved, whatever the terminal said. (2) The
persistent `browser_profile/` directory can go bad and take Chrome down with it: the
login window dies instantly with `signal=SIGTRAP`, which surfaces only as "The
browser window was closed during login". Isolate it by launching a persistent
context on a FRESH temp dir — if that works and the CLI's dir does not, delete
`browser_profile/` (`--fresh` did not fully clear it). **Deleting it also deletes the
Google session that made login automatic**, so the next login is interactive; say so
before doing it. A failed login also leaves its Chrome running and holding the
profile, so the next attempt dies with `Opening in existing browser session` — kill
the process whose `--user-data-dir` is that profile (never the user's own Chrome) and
remove the stale `Singleton*` links.

## Upgrading

Replace `vendor/notebooklm-py` with the new sdist (drop `tests/`, `examples/`,
`PKG-INFO`, and the generated `notebooklm-py/docs/diagrams/`, about 29 MB of HTML in
0.8.6 that would otherwise stay in the public history and in the image's COPY
layer), `pip install --user --break-system-packages --upgrade ./vendor/notebooklm-py`
on the host so host-side probes agree with the container, then prune the builder and
rebuild ([container/README.md](../container/README.md#build-cache)). Verify the CLI
both *inside* the image and on the host with `list --json`. The image's ENTRYPOINT
speaks the agent JSON protocol, so probe it with `--entrypoint`. The vendored version
is the one in `vendor/notebooklm-py/pyproject.toml`; 0.8.6 added `usage --json` and
the root `--backend`. The wrapper finds the subcommand by stepping over root options,
so a new root option that takes a value would make its value read as the subcommand.
`tests/test_notebooklm_wrapper.py` reads the root options from the vendored CLI
source and fails for one the scan does not step over.
