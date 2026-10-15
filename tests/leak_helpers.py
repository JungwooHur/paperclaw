"""Shared setup for the leak-gate tests.

Every git call runs with the GIT_* variables removed: git exports GIT_DIR and
GIT_INDEX_FILE to a hook, and they override cwd, so under a pre-push hook a
"temporary" repository would be the real one (root CLAUDE.md). The private
value sources point at files in the test's own directory, so the real .env,
messages.db and Notion are never read. Fake secrets are assembled at run time,
so this file passes the commit hook that it tests.
"""
import importlib.util
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")
SCRUBBED = ("NOTION_TOKEN", "NOTION_RESEARCH_DB",
            "PAPERCLAW_ALLOW_SENSITIVE", "PAPERCLAW_REDACT",
            "PAPERCLAW_ENV_FILE", "PAPERCLAW_PRIVATE_VALUES",
            "PAPERCLAW_MESSAGES_DB")


def pattern_secret():
    """A string check-sensitive.sh flags as a token."""
    return "sk-ant-" + "x" * 24


def known_value():
    """A private value with no shape any pattern knows."""
    return "zq" + "Wv" * 6 + "93"


# A commit identity the scanners accept: GitHub's noreply form.
NOREPLY = "1+test@users.noreply.github.com"


def fake_email():
    """A real-looking personal address."""
    return "real.person" + "@" + "gmail.com"


def fake_arxiv():
    """A real-looking arxiv id that is none of the documented dummies."""
    return "23" + "10.06825"


def fake_mobile():
    """A KR mobile number in the digits-only form a WhatsApp JID carries."""
    return "8210" + "9999" + "8888"


def fake_group_jid():
    """A real-looking group JID whose number is no phone number."""
    return "1203" + "6344556677" + "88@g.us"


def fake_uuid():
    """A real-looking UUID (a Notion page id has this shape)."""
    return "-".join(("3f2b9c1e", "4d5a", "4b6c", "9e8f", "1a2b3c4d5e6f"))


def clean_env(tmp_path, **extra):
    """An environment with no git state, no Notion and private-value sources
    that are files under tmp_path (absent unless the test writes them)."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k not in SCRUBBED}
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "PAPERCLAW_ENV_FILE": str(tmp_path / "test.env"),
        "PAPERCLAW_PRIVATE_VALUES": str(tmp_path / "private-values"),
        "PAPERCLAW_MESSAGES_DB": str(tmp_path / "messages.db"),
    })
    env.update(extra)
    return env


def apply_env(monkeypatch, env):
    """Make `env` this process's environment, for in-process calls."""
    for key in list(os.environ):
        if key not in env:
            monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def load_script(name):
    """Import scripts/<name> as a module, whatever its file name."""
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_").removesuffix(".py"), os.path.join(SCRIPTS, name))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up there
    spec.loader.exec_module(module)
    return module


class Repo:
    """A throwaway repository on branch main."""

    def __init__(self, path, env):
        self.path, self.env = str(path), env
        os.makedirs(self.path, exist_ok=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test Author")
        self.git("config", "user.email", NOREPLY)
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", os.devnull)

    def git(self, *args, check=True):
        return subprocess.run(["git", *args], cwd=self.path, env=self.env,
                              capture_output=True, text=True,
                              check=check).stdout.strip()

    def write(self, rel, text):
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)

    def write_bytes(self, rel, data):
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)

    def commit(self, files=None, message="change", **git_env):
        for rel, text in (files or {}).items():
            if isinstance(text, bytes):
                self.write_bytes(rel, text)
            else:
                self.write(rel, text)
            self.git("add", "-f", "--", rel)
        env = dict(self.env, **git_env)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", message],
                       cwd=self.path, env=env, check=True, capture_output=True)
        return self.git("rev-parse", "HEAD")

    def run(self, argv, stdin="", **extra_env):
        return subprocess.run(argv, cwd=self.path,
                              env=dict(self.env, **extra_env), input=stdin,
                              capture_output=True, text=True,
                              errors="replace")
