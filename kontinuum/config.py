"""Machine-level run config (§14). Lives on the developer's box, NOT in a target repo.

One K watches several repos, so `repos` is a list. Flags override the file for one-offs.
An entry is a plain `owner/name` (all defaults) or a mapping carrying per-repo settings (§16.1):
which universal plugins are on and which agent drives that repo.
Per-repo test recipes (verify.yaml) live inside each repo's .kontinuum/ — that's separate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

from kontinuum.plugins import available

DEFAULT_PATH = os.path.expanduser("~/.kontinuum/config.yaml")

REPO_KEYS = ("repo", "plugins", "agent")


@dataclass
class Repo:
    """One watched repo. A `repos:` entry is either `owner/name` or a mapping with these keys."""

    repo: str
    plugins: list[str] | None = None  # universal K-bundled plugins on here; None = all of them
    agent: str | None = None          # overrides the machine-level agent for this repo


@dataclass
class Config:
    instance_id: str
    bot_login: str
    repos: list[Repo]
    assignee: str | None = None      # personal queue; omit for the shared queue
    agent: str | None = None         # which agent drives the pipeline ("demo"); unset = stub (no PRs)
    lease_min: int = 60
    poll_sec: int = 300


def _repo(entry) -> Repo:
    """Normalize one `repos:` entry. A plain string keeps meaning "this repo, all defaults"."""
    if isinstance(entry, str):
        return Repo(entry)
    if not isinstance(entry, dict) or not entry.get("repo"):
        raise SystemExit(f"kontinuum: bad repos entry {entry!r} — want 'owner/name' or a mapping with `repo`")
    unknown = [k for k in entry if k not in REPO_KEYS]
    if unknown:
        raise SystemExit(f"kontinuum: unknown key(s) {', '.join(unknown)} for {entry['repo']} "
                         f"— allowed: {', '.join(REPO_KEYS)}")
    plugins = entry.get("plugins")
    if plugins is not None and (not isinstance(plugins, list) or set(plugins) - set(available())):
        raise SystemExit(f"kontinuum: bad `plugins` for {entry['repo']}: {plugins!r} "
                         f"— pick from {available()} (omit for all; repo-native .claude/ needs no entry)")
    return Repo(entry["repo"], list(plugins) if plugins is not None else None, entry.get("agent"))


def load_config(path: str | None = None, overrides: dict | None = None) -> Config:
    """Read the YAML config, then apply non-None flag overrides. Fail fast if required keys missing."""
    path = path or DEFAULT_PATH
    data: dict = {}
    if os.path.exists(path):
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    for key, value in (overrides or {}).items():
        if value is not None:
            data[key] = value

    missing = [k for k in ("instance_id", "bot_login", "repos") if not data.get(k)]
    if missing:
        raise SystemExit(f"kontinuum: missing config: {', '.join(missing)} (set in {path} or via flags)")

    return Config(
        instance_id=data["instance_id"],
        bot_login=data["bot_login"],
        repos=[_repo(e) for e in data["repos"]],
        assignee=data.get("assignee"),
        agent=data.get("agent"),
        lease_min=int(data.get("lease_min", 60)),
        poll_sec=int(data.get("poll_sec", 300)),
    )
