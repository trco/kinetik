"""Ephemeral, credential-free, network-restricted container for a task's commands (§3, §8).

Docker forwards no host env by default, so the container starts credential-free. `--network none`
blocks all egress; `--cap-drop ALL` + no-new-privileges stop in-container escalation. The task's
git worktree is mounted at /work — the only thing the agent can see or change. Each `run` is a
fresh `--rm` container sharing that worktree, so files persist between steps but nothing else does.
"""

from __future__ import annotations

import subprocess


class Sandbox:
    def __init__(self, image: str, workdir: str, network: str = "none"):
        self.image = image        # the repo's toolchain image (a base like alpine for probes)
        self.workdir = workdir    # absolute host path (the git worktree), mounted at /work
        # ponytail: 'none' = zero egress. Swap for an allowlist proxy when the agent needs
        # package registries / the Anthropic API (step 5) — don't widen it before then.
        self.network = network

    def run(self, *cmd: str, timeout: int = 600) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", "run", "--rm",
             "--network", self.network,
             "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
             "-v", f"{self.workdir}:/work", "-w", "/work",
             self.image, *cmd],
            capture_output=True, text=True, timeout=timeout)
        # ponytail: root-in-container writes root-owned files on Linux; add --user or chown on
        # cleanup when we run on Linux. On macOS Docker Desktop the mount maps to the host user.
