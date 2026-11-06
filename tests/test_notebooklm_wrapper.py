"""The container's `notebooklm` wrapper keeps parallel subagents off each other's
notebooks.

Every call of the CLI shares one context file, and `ask` resumes the conversation
cached there whenever `--notebook` names the cached notebook. flock serializes
single commands, not a subagent's sequence of them, so another subagent's ask
could leave a conversation of ITS notebook in the cache and the next ask was
answered from the wrong paper's sources. The wrapper therefore clears the context
before every call and refuses the ways around the shared cookie jar.

The wrapper runs here as the container runs it, with HOME pointed at a scratch
directory and the real CLI replaced by a fake that records each call.
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRAPPER = os.path.join(REPO, "container", "bin", "notebooklm")

pytestmark = pytest.mark.skipif(
    shutil.which("flock") is None or shutil.which("bash") is None,
    reason="the wrapper needs bash and flock")

# Records each call: its argv, and whether either context file was there when
# the real CLI would have read it.
_FAKE_CLI = f"""#!{sys.executable}
import json, os, sys
home = os.path.join(os.environ["HOME"], ".notebooklm")
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({{
        "argv": sys.argv[1:],
        "profile_context": os.path.exists(
            os.path.join(home, "profiles", "default", "context.json")),
        "legacy_context": os.path.exists(os.path.join(home, "context.json")),
    }}) + "\\n")
"""

# Stands in for notebook_registry.py: answers `--get-or-create` the way the real
# one does for a notebook it already knows.
_FAKE_REGISTRY = """import json
print(json.dumps({"notebook": {"id": "nb-registered", "title": "T"}}))
"""


@pytest.fixture
def box(tmp_path):
    home = tmp_path / "home"
    (home / ".notebooklm" / "profiles" / "default").mkdir(parents=True)
    fake = tmp_path / "notebooklm-real"
    fake.write_text(_FAKE_CLI)
    fake.chmod(0o755)
    log = tmp_path / "calls.jsonl"

    def wrapper_env(registry, env):
        full_env = {
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "NOTEBOOKLM_REAL_BIN": str(fake),
            "NOTEBOOK_REGISTRY_PY": registry or str(tmp_path / "no-registry.py"),
            "FAKE_LOG": str(log),
        }
        full_env.update(env)
        return full_env

    def run(*args, registry=None, **env):
        return subprocess.run(["bash", WRAPPER, *args],
                              env=wrapper_env(registry, env),
                              capture_output=True, text=True, timeout=30)

    def start(*args, registry=None, **env):
        return subprocess.Popen(["bash", WRAPPER, *args],
                                env=wrapper_env(registry, env),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)

    def calls():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    run.calls = calls
    run.start = start
    run.home = home / ".notebooklm"
    run.lock = home / "notebooklm.lock"
    run.tmp = tmp_path
    return run


def test_context_is_cleared_before_the_call(box):
    profile_ctx = box.home / "profiles" / "default" / "context.json"
    legacy_ctx = box.home / "context.json"
    for ctx in (profile_ctx, legacy_ctx):
        ctx.write_text('{"notebook_id": "nb-other", "conversation_id": "c-other"}')

    proc = box("ask", "q", "-n", "nb-mine", "--json")

    assert proc.returncode == 0, proc.stderr
    [call] = box.calls()
    assert call["profile_context"] is False
    assert call["legacy_context"] is False


def test_an_ordinary_ask_passes_through_unchanged(box):
    proc = box("ask", "a question", "-n", "nb-mine", "--json")

    assert proc.returncode == 0, proc.stderr
    assert [c["argv"] for c in box.calls()] == [
        ["ask", "a question", "-n", "nb-mine", "--json"]]


@pytest.mark.parametrize("args", [
    ["use", "nb-mine"],
    ["-v", "use", "nb-mine"],
], ids=["use", "use-after-root-option"])
def test_use_is_refused(box, args):
    proc = box(*args)

    assert proc.returncode == 2
    assert box.calls() == []
    assert len(proc.stderr.strip().splitlines()) == 1
    assert "--notebook <id>" in proc.stderr


@pytest.mark.parametrize("args", [
    ["-p", "x", "list"],
    ["-px", "list"],
    ["-vp", "x", "list"],
    ["--profile", "x", "list"],
    ["--profile=x", "list"],
    ["--storage", "x", "list"],
    ["--storage=x", "list"],
], ids=lambda args: " ".join(args))
def test_a_second_cookie_jar_is_refused(box, args):
    proc = box(*args)

    assert proc.returncode == 2
    assert box.calls() == []
    assert len(proc.stderr.strip().splitlines()) == 1
    assert "--notebook <id>" in proc.stderr
    assert "storage_state.json" in proc.stderr


@pytest.mark.parametrize("args", [
    ["--backend", "android", "use", "x"],
    ["--backend", "android", "list"],
    ["--backend=android", "list"],
    ["-v", "--backend", "android", "source", "list", "-n", "nb-mine"],
], ids=lambda args: " ".join(args))
def test_the_android_backend_is_refused(box, args):
    # Android signs in with master_token.json, a second credential beside the
    # shared storage_state.json.
    proc = box(*args)

    assert proc.returncode == 2
    assert box.calls() == []
    assert len(proc.stderr.strip().splitlines()) == 1
    assert "master_token.json" in proc.stderr
    assert "storage_state.json" in proc.stderr


def test_the_android_backend_by_env_is_refused(box):
    proc = box("list", "--json", NOTEBOOKLM_BACKEND="android")

    assert proc.returncode == 2
    assert box.calls() == []
    assert len(proc.stderr.strip().splitlines()) == 1
    assert "master_token.json" in proc.stderr


@pytest.mark.parametrize("value", ["web", ""], ids=["web", "empty"])
def test_the_web_backend_by_env_passes(box, value):
    # The CLI fails on an empty NOTEBOOKLM_BACKEND by itself.
    proc = box("list", "--json", NOTEBOOKLM_BACKEND=value)

    assert proc.returncode == 0, proc.stderr
    assert [c["argv"] for c in box.calls()] == [["list", "--json"]]


@pytest.mark.parametrize("args", [
    # --backend takes a value: `web` is not the subcommand, `use` is.
    ["--backend", "web", "use", "nb-mine"],
    ["--backend", "web", "profile", "switch", "x"],
    ["--quiet", "--backend", "web", "use", "nb-mine"],
], ids=lambda args: " ".join(args))
def test_the_backend_value_is_not_the_subcommand(box, args):
    proc = box(*args)

    assert proc.returncode == 2
    assert box.calls() == []
    assert len(proc.stderr.strip().splitlines()) == 1


_CLI_ROOT = "vendor/notebooklm-py/src/notebooklm/notebooklm_cli.py"


def _root_options():
    """(name, takes_value) for every option of the vendored CLI's root group.

    Read from the decorators of `cli`, so an upgrade that adds a root option
    shows up here without anyone remembering to look.
    """
    with open(os.path.join(REPO, _CLI_ROOT), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    [cli] = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == "cli"]
    options = []
    for deco in cli.decorator_list:
        if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
            continue
        if deco.func.attr == "version_option":
            options.append(("--version", False))
        elif deco.func.attr == "option":
            keywords = {k.arg: k.value for k in deco.keywords}
            flag = any(isinstance(keywords.get(k), ast.Constant)
                       and keywords[k].value is True for k in ("is_flag", "count"))
            options += [(arg.value, not flag) for arg in deco.args
                        if isinstance(arg, ast.Constant)
                        and str(arg.value).startswith("-")]
    return options


def test_the_root_options_are_read():
    names = {name for name, _ in _root_options()}

    assert {"--storage", "-p", "--backend", "-v", "--quiet"} <= names


@pytest.mark.parametrize("name,takes_value", _root_options(),
                         ids=[name for name, _ in _root_options()])
def test_no_root_option_hides_the_subcommand(box, name, takes_value):
    # The scan must step over each root option, and over the value of one that
    # takes a value; otherwise the value reads as the subcommand and the `use`
    # after it runs.
    args = [name, "web", "use", "nb-mine"] if takes_value else [name, "use", "nb-mine"]

    proc = box(*args)

    assert proc.returncode == 2
    assert box.calls() == []


@pytest.mark.parametrize("args", [
    ["--backend", "web", "list", "--json"],
    ["--backend=web", "list", "--json"],
    ["--quiet", "source", "list", "-n", "nb-mine", "--json"],
    ["--quiet", "--backend", "web", "usage", "--json"],
], ids=lambda args: " ".join(args))
def test_the_web_backend_and_quiet_pass(box, args):
    proc = box(*args)

    assert proc.returncode == 0, proc.stderr
    assert [c["argv"] for c in box.calls()] == [args]


@pytest.mark.parametrize("var", [
    "NOTEBOOKLM_PROFILE", "NOTEBOOKLM_HOME", "NOTEBOOKLM_AUTH_JSON"])
def test_a_second_cookie_jar_by_env_is_refused(box, var):
    # NOTEBOOKLM_AUTH_JSON is the jar itself, inline: a copy that rotates apart
    # from the shared file the same way a copied profile does.
    proc = box("list", "--json", **{var: "x"})

    assert proc.returncode == 2
    assert box.calls() == []
    assert len(proc.stderr.strip().splitlines()) == 1
    assert "--notebook <id>" in proc.stderr


@pytest.mark.parametrize("var", [
    "NOTEBOOKLM_PROFILE", "NOTEBOOKLM_HOME", "NOTEBOOKLM_AUTH_JSON"])
def test_an_empty_env_var_is_not_a_second_jar(box, var):
    # The CLI ignores an empty NOTEBOOKLM_PROFILE/NOTEBOOKLM_HOME and fails on an
    # empty NOTEBOOKLM_AUTH_JSON by itself, so none of them reaches another jar.
    proc = box("list", "--json", **{var: ""})

    assert proc.returncode == 0, proc.stderr
    assert [c["argv"] for c in box.calls()] == [["list", "--json"]]


@pytest.mark.parametrize("args", [
    # `share add` has its own -p (--permission); only root options are checked.
    ["share", "add", "reader", "-p", "editor", "-n", "nb-mine"],
    ["-v", "source", "list", "-n", "nb-mine", "--json"],
], ids=["subcommand-p", "root-verbose"])
def test_options_after_the_subcommand_are_not_root_options(box, args):
    proc = box(*args)

    assert proc.returncode == 0, proc.stderr
    assert [c["argv"] for c in box.calls()] == [args]


def test_create_use_never_calls_use(box):
    registry = box.tmp / "registry.py"
    registry.write_text(_FAKE_REGISTRY)

    proc = box("create", "T", "--use", "--json", registry=str(registry))

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["notebook"]["id"] == "nb-registered"
    assert [c["argv"] for c in box.calls() if c["argv"][:1] == ["use"]] == []


@pytest.mark.parametrize("args", [
    ["profile", "switch", "x"],
    ["profile", "create", "x"],
    ["profile", "rename", "default", "x"],
    ["profile", "delete", "x"],
    ["-v", "profile", "switch", "x"],
    ["profile", "--", "switch", "x"],
    ["profile"],
], ids=lambda args: " ".join(args))
def test_profile_is_refused_except_list(box, args):
    # A new profile is a second jar, and a switch moves every later call to
    # another jar and a context the wrapper does not clear.
    proc = box(*args)

    assert proc.returncode == 2
    assert box.calls() == []
    assert len(proc.stderr.strip().splitlines()) == 1
    assert "storage_state.json" in proc.stderr


@pytest.mark.parametrize("args", [
    ["profile", "list"],
    ["profile", "list", "--json"],
], ids=lambda args: " ".join(args))
def test_profile_list_passes(box, args):
    proc = box(*args)

    assert proc.returncode == 0, proc.stderr
    assert [c["argv"] for c in box.calls()] == [args]


def test_a_switched_default_profile_is_refused(box):
    # A `profile switch` on the host writes config.json, which the container
    # shares: every call would then run in another profile.
    (box.home / "config.json").write_text(json.dumps({"default_profile": "x"}))

    proc = box("ask", "q", "-n", "nb-mine", "--json")

    assert proc.returncode == 2
    assert box.calls() == []
    assert len(proc.stderr.strip().splitlines()) == 1


@pytest.mark.parametrize("config", [
    {"default_profile": "default"},
    {"default_profile": ""},
    {"language": "en"},
], ids=["default", "empty", "unset"])
def test_the_default_profile_passes(box, config):
    (box.home / "config.json").write_text(json.dumps(config))

    proc = box("ask", "q", "-n", "nb-mine", "--json")

    assert proc.returncode == 0, proc.stderr
    assert [c["argv"] for c in box.calls()] == [
        ["ask", "q", "-n", "nb-mine", "--json"]]


# Answers `--get-or-create` with the title it was given, so a test can tell which
# token the wrapper read as the title.
_ECHO_REGISTRY = """import json, sys
title = sys.argv[sys.argv.index("--title") + 1]
print(json.dumps({"notebook": {"id": "nb-registered", "title": title}}))
"""


@pytest.mark.parametrize("args", [
    ["--quiet", "create", "T", "--json"],
    ["--backend", "web", "create", "T", "--json"],
    ["-v", "create", "-u", "T", "--json"],
    ["create", "--json", "T"],
])
def test_create_goes_through_the_registry_wherever_its_title_sits(box, args):
    registry = box.tmp / "registry.py"
    registry.write_text(_ECHO_REGISTRY)

    proc = box(*args, registry=str(registry))

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["notebook"] == {"id": "nb-registered", "title": "T"}
    assert box.calls() == []


def test_a_failed_registry_falls_through_with_the_original_argv(box):
    registry = box.tmp / "registry.py"
    registry.write_text("import sys\nsys.exit(1)\n")

    proc = box("create", "T", "--use", "--json", registry=str(registry))

    assert proc.returncode == 0, proc.stderr
    assert [c["argv"] for c in box.calls()] == [
        ["create", "T", "--use", "--json"]]


def _call_behind_the_lock(box, args, meanwhile):
    """Runs the wrapper while another process holds its lock.

    `meanwhile` runs once the wrapper is waiting on the lock, before the lock is
    released.

    Returns:
        The wrapper's subprocess.CompletedProcess.
    """
    holder = subprocess.Popen(
        ["flock", str(box.lock), "-c", "echo held; read _"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    wrapper = None
    try:
        assert holder.stdout.readline().strip() == "held"
        wrapper = box.start(*args)
        time.sleep(0.5)  # ample time for the wrapper to reach the lock

        assert wrapper.poll() is None
        meanwhile()
    finally:
        holder.communicate("\n", timeout=10)
        if wrapper is not None:
            out, err = wrapper.communicate(timeout=30)
    return subprocess.CompletedProcess(args, wrapper.returncode, out, err)


def test_the_context_is_cleared_only_under_the_lock(box):
    # Cleared before the lock, a waiting call would delete the context out from
    # under the call that holds it, and the holder could write it again before
    # the waiter runs.
    ctx = box.home / "profiles" / "default" / "context.json"
    ctx.write_text('{"notebook_id": "nb-other", "conversation_id": "c-other"}')

    def still_there():
        assert ctx.exists()

    proc = _call_behind_the_lock(
        box, ["ask", "q", "-n", "nb-mine", "--json"], still_there)

    assert proc.returncode == 0, proc.stderr
    assert not ctx.exists()
    [call] = box.calls()
    assert call["profile_context"] is False


def test_the_default_profile_is_read_only_under_the_lock(box):
    # Read before the lock, a `profile switch` made on the host while this call
    # queued would be missed, and the CLI, which reads config.json when it runs,
    # would answer from the other jar with a context the wrapper never cleared.
    config = box.home / "config.json"

    proc = _call_behind_the_lock(
        box, ["ask", "q", "-n", "nb-mine", "--json"],
        lambda: config.write_text(json.dumps({"default_profile": "x"})))

    assert proc.returncode == 2
    assert box.calls() == []
    assert "default_profile 'x'" in proc.stderr


# Every file that tells an agent what a call without --notebook prints.
_NO_NOTEBOOK_DOCS = [
    "container/bin/notebooklm",
    "container/skills/notebooklm/SKILL.md",
    "groups/main/CLAUDE.md",
    "CLAUDE.md",
]
_CLI_RESOLVER = "vendor/notebooklm-py/src/notebooklm/cli/resolve.py"


def test_the_docs_quote_the_clis_real_no_notebook_message():
    # An agent that meets the message must recognise it, so every doc quotes what
    # the installed CLI prints; a reworded message after an upgrade fails here,
    # and so does a doc that drops the quote or keeps the wording it replaced.
    with open(os.path.join(REPO, _CLI_RESOLVER), encoding="utf-8") as f:
        source = f.read()
    for doc in _NO_NOTEBOOK_DOCS:
        with open(os.path.join(REPO, doc), encoding="utf-8") as f:
            text = f.read()
        quoted = re.findall(r'["`](No notebook [^"`]*)["`]', text)

        assert quoted, doc
        assert "No notebook selected" not in text, doc
        for quote in quoted:
            assert quote.rstrip(" .\u2026") in source, (doc, quote)
