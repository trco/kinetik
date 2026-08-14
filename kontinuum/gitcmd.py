"""Thin wrapper to run git in a working directory (shared by pipeline + effects)."""

from __future__ import annotations

import subprocess


def git(workdir: str, *args: str) -> str:
    return subprocess.run(["git", "-C", workdir, *args], check=True, capture_output=True, text=True).stdout
