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


def test_missing_required_fails_fast(tmp_path):
    with pytest.raises(SystemExit):
        load_recipe(_repo(tmp_path, "image: alpine\n"))   # no gate
