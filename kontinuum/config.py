"""Machine-level run config (§14). Lives on the developer's box, NOT in a target repo.

One K watches several repos, so `repos` is a list. Flags override the file for one-offs.
Per-repo test recipes (verify.yaml) live inside each repo's .kontinuum/ — that's separate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

DEFAULT_PATH = os.path.expanduser("~/.kontinuum/config.yaml")


@dataclass
class Config:
    instance_id: str
    bot_login: str
    repos: list[str]
    assignee: str | None = None      # personal queue; omit for the shared queue
    lease_min: int = 60
    poll_sec: int = 300


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
        repos=list(data["repos"]),
        assignee=data.get("assignee"),
        lease_min=int(data.get("lease_min", 60)),
        poll_sec=int(data.get("poll_sec", 300)),
    )
