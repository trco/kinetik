"""Inject Kontinuum's bundled, repo-agnostic plugins into a worktree's `.claude/` (§16.1).

Claude Code auto-loads `.claude/` from its cwd, so dropping the bundled plugin files into the
worktree makes them available for the run. The plugins ship *with Kontinuum* (same version on every
machine), not with the target repo — so `execute()` git-excludes the injected paths and the target
repo stays clean. Repo-native `.claude/` files always win: an existing file is never overwritten.

The bundle mirrors the contents of `.claude/` (so `bundled_plugins/skills/x.md` lands at
`<worktree>/.claude/skills/x.md`). The living-docs plugin (#3) drops into this same bundle.
"""

from __future__ import annotations

import os
import shutil

BUNDLE = os.path.join(os.path.dirname(__file__), "bundled_plugins")


def inject(workdir: str, bundle: str = BUNDLE) -> list[str]:
    """Copy bundled plugin files into `<workdir>/.claude/`, never overwriting repo-native files.

    Returns the injected paths (relative to `workdir`, e.g. `.claude/skills/x.md`) so the caller
    can add them to the git exclude and keep them out of the commit.
    """
    injected: list[str] = []
    if not os.path.isdir(bundle):
        return injected
    for root, _dirs, files in os.walk(bundle):
        rel = os.path.relpath(root, bundle)
        for name in files:
            relpath = os.path.join(".claude", name if rel == "." else os.path.join(rel, name))
            dst = os.path.join(workdir, relpath)
            if os.path.exists(dst):
                continue                                  # repo-native wins — never overwrite
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(root, name), dst)
            injected.append(relpath)
    return injected
