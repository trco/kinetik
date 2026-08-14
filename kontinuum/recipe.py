"""Per-repo test recipe (§9/§14): how to build/test THIS repo. Lives in <repo>/.kontinuum/verify.yaml.

Read from the clone during execute, so each repo brings its own gate command + toolchain image.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml


@dataclass
class Recipe:
    image: str            # sandbox image carrying the repo's toolchain
    gate: str             # the local gate command (build / lint / test)
    # 'none' = fully isolated (best). Prefer baking test deps into `image`. If the gate genuinely
    # must fetch (pip/npm install), a repo can opt into 'bridge' here — less isolation, its choice.
    # ponytail: the proper fix is an egress allowlist proxy (registries only), not open network.
    network: str = "none"


def load_recipe(workdir: str) -> Recipe:
    path = os.path.join(workdir, ".kontinuum", "verify.yaml")
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    missing = [k for k in ("image", "gate") if not data.get(k)]
    if missing:
        raise SystemExit(f"kontinuum: {path} missing: {', '.join(missing)}")
    return Recipe(image=data["image"], gate=data["gate"], network=data.get("network", "none"))
