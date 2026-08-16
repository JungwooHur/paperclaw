# Third-party code

Everything under `vendor/` is **not** covered by this repository's root `LICENSE`
(MIT, Copyright (c) 2026 Gavriel). Each vendored project keeps its own licence file
and copyright, which is what permits redistributing it here.

| Directory | Upstream | Version | Licence |
|---|---|---|---|
| `notebooklm-py/` | https://github.com/teng-lin/notebooklm-py | 0.8.1 | MIT — `notebooklm-py/LICENSE`, Copyright (c) 2026 Teng Lin |

Why it is vendored rather than installed from PyPI: the agent container builds
offline from the repo, and the exact version matters — this client drives the
NotebookLM web app, so it breaks whenever Google changes it, and pinning the tree
makes "which version is actually running" answerable from the commit alone. See the
root `CLAUDE.md` for the upgrade procedure and for the failure modes a stale copy
produces.

`tests/`, `examples/` and `PKG-INFO` are dropped from the sdist; MIT does not require
redistributing the complete package, only the copyright and permission notice, which
`notebooklm-py/LICENSE` carries verbatim.
