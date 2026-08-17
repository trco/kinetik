"""Per-repo test recipe (§9/§14): how to build/test THIS repo. Lives in <repo>/.kinetik/verify.yaml.

Read from the clone during execute, so each repo brings its own gate command + toolchain image.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml


@dataclass
class Recipe:
    image: str            # sandbox image carrying the repo's toolchain
    gate: str             # the local gate command (build / lint / test), run OFFLINE
    # Trusted install step run WITH network BEFORE the agent, into /work (e.g. `npm ci`,
    # `pip install --target /work/.deps -r requirements.txt`). Deterministic and repo-authored,
    # so it's safe online — while the untrusted agent + gate then run with no network.
    setup: str = ""
    # Network for the agent+gate box. 'none' = fully isolated (best). Prefer `setup` + baking deps
    # into `image`; only open this to 'bridge' if the agent itself must reach the network.
    network: str = "none"


def load_recipe(workdir: str) -> Recipe:
    path = os.path.join(workdir, ".kinetik", "verify.yaml")
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    missing = [k for k in ("image", "gate") if not data.get(k)]
    if missing:
        # a plain error (NOT SystemExit) so the daemon's per-issue/per-repo guards catch it
        raise ValueError(f"{path} missing: {', '.join(missing)}")
    return Recipe(image=data["image"], gate=data["gate"],
                  setup=data.get("setup", ""), network=data.get("network", "none"))
