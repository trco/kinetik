"""Per-repo recipe loads from .kontinuum/verify.yaml; base defaults; missing required fails fast."""

from __future__ import annotations

import pytest

from kontinuum.recipe import load_recipe


def _repo(tmp_path, verify_yaml: str) -> str:
    d = tmp_path / ".kontinuum"
    d.mkdir()
    (d / "verify.yaml").write_text(verify_yaml)
    return str(tmp_path)


def test_loads_recipe(tmp_path):
    r = load_recipe(_repo(tmp_path, "image: python:3.11\ngate: pytest -q\n"))
    assert (r.image, r.gate) == ("python:3.11", "pytest -q")
    assert r.setup == "" and r.network == "none"          # no install step, isolated by default


def test_setup_and_network_are_read(tmp_path):
    r = load_recipe(_repo(tmp_path, "image: node:20\ngate: npm test\nsetup: npm ci\nnetwork: bridge\n"))
    assert r.setup == "npm ci"
    assert r.network == "bridge"


def test_missing_required_raises(tmp_path):
    with pytest.raises(ValueError):                       # not SystemExit — must be catchable in the loop
        load_recipe(_repo(tmp_path, "image: alpine\n"))   # no gate
