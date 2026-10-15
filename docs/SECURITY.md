# PaperClaw Security Model

## Trust Model

| Entity | Trust Level | Rationale |
|--------|-------------|-----------|
| Main group | Trusted | Private self-chat, admin control |
| Non-main groups | Untrusted | Other users may be malicious |
| Container agents | Sandboxed | Isolated execution environment |
| WhatsApp messages | User input | Potential prompt injection |

## Security Boundaries

### 1. Container Isolation (Primary Boundary)

Agents execute in containers (lightweight Linux VMs), providing:
- **Process isolation** - Container processes cannot affect the host
- **Filesystem isolation** - Only explicitly mounted directories are visible
- **Non-root execution** - Runs as unprivileged `node` user (uid 1000)
- **Ephemeral containers** - Fresh environment per invocation (`--rm`)

This is the primary security boundary. Rather than relying on application-level permission checks, the attack surface is limited by what's mounted.

### 2. Mount Security

**External Allowlist** - Mount permissions stored at `~/.config/paperclaw/mount-allowlist.json`, which is:
- Outside project root
- Never mounted into containers
- Cannot be modified by agents

**Default Blocked Patterns:**
```
.ssh, .gnupg, .aws, .azure, .gcloud, .kube, .docker,
credentials, .env, .netrc, .npmrc, id_rsa, id_ed25519,
private_key, .secret
```

**Protections:**
- Symlink resolution before validation (prevents traversal attacks)
- Container path validation (rejects `..` and absolute paths)
- `nonMainReadOnly` option forces read-only for non-main groups

**Read-Only Project Root:**

The main group's project root is mounted read-only. Writable paths the agent needs (group folder, IPC, `.claude/`) are mounted separately. This prevents the agent from modifying host application code (`src/`, `dist/`, `package.json`, etc.) which would bypass the sandbox entirely on next restart.

### 3. Session Isolation

Each group has isolated Claude sessions at `data/sessions/{group}/.claude/`:
- Groups cannot see other groups' conversation history
- Session data includes full message history and file contents read
- Prevents cross-group information disclosure

### 4. IPC Authorization

Messages and task operations are verified against group identity:

| Operation | Main Group | Non-Main Group |
|-----------|------------|----------------|
| Send message to own chat | ✓ | ✓ |
| Send message to other chats | ✓ | ✗ |
| Schedule task for self | ✓ | ✓ |
| Schedule task for others | ✓ | ✗ |
| View all tasks | ✓ | Own only |
| Manage other groups | ✓ | ✗ |

### 5. Credential Handling

**Passed to Containers:**
A subset of `.env` (`CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, `NOTION_TOKEN`, `NOTION_RESEARCH_DB`, `OUTPUT_LANGUAGE`) is read by the host at spawn time and piped to the container over stdin. Secrets are never written to a mounted file and never set on the host `process.env`.

**Masked Inside the Project Root Mount (main only):**
The main group's project-root bind mount hides three paths by overlaying an empty replacement. Without this overlay the agent could `cat` the file off disk and trivially bypass the Bash PreToolUse hook below.
- `/workspace/project/.env` → empty file. Would otherwise leak `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY`.
- `/workspace/project/store` → empty directory. Would otherwise leak Baileys WhatsApp session credentials — full account takeover on WhatsApp — and every copy of them set aside next to `auth/` (a revoked session is renamed, never deleted, and a backup may still be valid), plus `messages.db`, the full message history. The whole directory is masked, not just `auth/`, so a copy under a new name is covered too. The agent gets the group list from `/workspace/ipc/available_groups.json` instead.
- `/workspace/project/logs` → empty directory. Would otherwise leak the service log (see Logs below).

**NOT Mounted at All:**
- Mount allowlist (`~/.config/paperclaw/mount-allowlist.json`) — external, agent cannot modify mount policy.
- Any path matching the blocked-pattern list (`.ssh`, `.aws`, `.netrc`, etc.).

**Bash Subprocess Hardening:**
A `PreToolUse` hook in the agent runner prepends `unset ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN` to every Bash invocation, so subprocesses the agent spawns cannot read Claude auth tokens from `printenv` even though the SDK itself needs them. Non-auth secrets (e.g. `NOTION_TOKEN`) stay visible — the agent calls Notion's API on the user's behalf.

> **Residual risk:** the Claude Agent SDK still needs `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN` in its own process environment to authenticate, so an in-process exfil (e.g. an SDK extension that read `process.env` and posted it somewhere) would defeat the Bash hook. The mount masking + Bash hook combo only blocks the easy `cat .env` and `echo $ANTHROPIC_API_KEY` paths. **PRs welcome** for tighter in-process credential isolation.

### 6. Logs

`logs/paperclaw.log` and `logs/paperclaw.error.log` are the service's stdout and stderr, so whatever a dependency prints with `console.*` lands in one of them, not only what goes through the app logger. libsignal's `console.info` lines go to the first, its `console.warn("Session already closed", …)` to the second.

- **Signal session keys are redacted.** libsignal, under baileys, prints whole session objects (`Closing session:`, `Opening session:`, `Removing old closed session:`), and Node expands them down to the ratchet private key, the root key and the chain keys. `src/libsignal-redact.ts` gives those objects a one-line form, `SessionEntry <redacted closed=…>`. Every module that imports baileys loads it first (the service, `npm run auth`, the setup group sync); a test fails if one stops, or if a libsignal upgrade leaves the patch with nothing to attach to.
- **baileys logs at `warn`** (`BAILEYS_LOG_LEVEL`), so its info lines, the account's phone number on every login among them, are not written by default.
- **The agent cannot read it.** `logs/` is masked out of the main group's project mount (above). The log still carries phone numbers and chat JIDs from the app's own lines.
- **Logs written before the redaction can still hold key dumps.** It only changes new output. Treat older `logs/paperclaw.log` and `logs/paperclaw.error.log` content, and any copy or backup of either, as secret: do not share it or attach it to an issue, and truncate or delete it once it is no longer needed.

## Privilege Comparison

| Capability | Main Group | Non-Main Group |
|------------|------------|----------------|
| Project root access | `/workspace/project` (ro) | None |
| Group folder | `/workspace/group` (rw) | `/workspace/group` (rw) |
| Global memory | Implicit via project | `/workspace/global` (ro) |
| Additional mounts | Configurable | Read-only unless allowed |
| Network access | Unrestricted | Unrestricted |
| MCP tools | All | All |

## Security Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        UNTRUSTED ZONE                             │
│  WhatsApp Messages (potentially malicious)                        │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼ Trigger check, input escaping
┌──────────────────────────────────────────────────────────────────┐
│                     HOST PROCESS (TRUSTED)                        │
│  • Message routing                                                │
│  • IPC authorization                                              │
│  • Mount validation (external allowlist)                          │
│  • Container lifecycle                                            │
│  • Credential filtering                                           │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼ Explicit mounts only
┌──────────────────────────────────────────────────────────────────┐
│                CONTAINER (ISOLATED/SANDBOXED)                     │
│  • Agent execution                                                │
│  • Bash commands (sandboxed)                                      │
│  • File operations (limited to mounts)                            │
│  • Network access (unrestricted)                                  │
│  • Cannot modify security config                                  │
└──────────────────────────────────────────────────────────────────┘
```
