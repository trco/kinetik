"""Inject Kontinuum's bundled, repo-agnostic plugins into a worktree's `.claude/` (§16.1).

Claude Code auto-loads `.claude/` from its cwd, so dropping the bundled plugin files into the
worktree makes them available for the run. The plugins ship *with Kontinuum* (same version on every
machine), not with the target repo — so `execute()` git-excludes the injected paths and the target
repo stays clean. Repo-native `.claude/` files always win: an existing file is never overwritten.

The bundle holds one directory per plugin, each mirroring `.claude/` (so
`bundled_plugins/living-docs/skills/x.md` lands at `<worktree>/.claude/skills/x.md`). Which plugins
are on is per-repo config (§14, `repos: [{repo, plugins}]`); `core` is not a plugin and always ships.
"""

from __future__ import annotations

import contextlib
import os
import shutil

from kontinuum.worktree import _git_exclude_path

BUNDLE = os.path.join(os.path.dirname(__file__), "bundled_plugins")
CORE = "core"                          # baseline agent context — always injected, not toggleable


def available(bundle: str = BUNDLE) -> list[str]:
    """The toggleable plugin names: one bundle directory each. Adding a plugin is adding a directory."""
    if not os.path.isdir(bundle):
        return []
    return sorted(n for n in os.listdir(bundle)
                  if n != CORE and os.path.isdir(os.path.join(bundle, n)))


def inject(workdir: str, plugins: list[str] | None = None, bundle: str = BUNDLE) -> list[str]:
    """Copy `core` + the selected plugins into `<workdir>/.claude/`, never overwriting repo-native files.

    `plugins=None` means every bundled plugin (the default); `[]` means core only. Returns the
    injected paths (relative to `workdir`, e.g. `.claude/skills/x.md`) so the caller can add them to
    the git exclude and keep them out of the commit.
    """
    injected: list[str] = []
    for plugin in [CORE] + (available(bundle) if plugins is None else list(plugins)):
        base = os.path.join(bundle, plugin)
        for root, _dirs, files in os.walk(base):
            rel = os.path.relpath(root, base)
            for name in files:
                relpath = os.path.join(".claude", name if rel == "." else os.path.join(rel, name))
                dst = os.path.join(workdir, relpath)
                if os.path.exists(dst):
                    continue                              # repo-native wins — never overwrite
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(root, name), dst)
                injected.append(relpath)
    return injected


@contextlib.contextmanager
def injected(workdir: str):
    """Inject the bundled plugins for the duration, kept out of the commit, exclude restored on exit.

    For the edit-only flows (`onboard`, `seed-docs`) that need `/docs-seed` available but have no
    deps-exclude machinery of their own. `execute()` folds injection into its own exclude instead.
    Restoring the exclude matters: the info/exclude is shared across worktrees of a cached clone, so a
    lingering entry would wrongly hide a target repo's own `.claude/` file from later commits.
    """
    paths = inject(workdir)
    exclude_path = backup = None
    if paths:
        exclude_path = _git_exclude_path(workdir)
        backup = open(exclude_path).read() if os.path.exists(exclude_path) else ""
        with open(exclude_path, "a") as f:
            f.write("\n".join(sorted(paths)) + "\n")
    try:
        yield paths
    finally:
        if exclude_path is not None:
            with open(exclude_path, "w") as f:
                f.write(backup)
