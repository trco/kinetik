"""The local gate passes/fails on the command's exit code, running inside the sandbox. Docker-gated."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from kontinuum.sandbox import Sandbox
from kontinuum.verify import run_gate

IMAGE = "alpine:latest"


def _docker_ok() -> bool:
    return bool(shutil.which("docker")) and \
        subprocess.run(["docker", "info"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ok(), reason="docker daemon not available")


def test_gate_passes_on_zero_exit(tmp_path):
    res = run_gate(Sandbox(IMAGE, str(tmp_path)), "echo building && true")
    assert res.passed and "building" in res.log


def test_gate_fails_on_nonzero_exit(tmp_path):
    res = run_gate(Sandbox(IMAGE, str(tmp_path)), "echo boom >&2; exit 1")
    assert not res.passed and "boom" in res.log
