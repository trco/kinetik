"""Living-docs maintenance (§16.1): after a change, refresh the repo's living docs in the SAME PR.

The split honours K's containment. K computes *what* changed (git — the agent has no Bash); the
agent DRAFTS the pages (Read/Edit/Write, no shell); `open_pr` commits code + docs together, and the
human reviews both. Best-effort: a docs failure never blocks the code PR.

Updating an existing area keeps its page true; a repo with no living docs yet gets a minimal set
bootstrapped for the touched area (organic growth) — a full initial survey is the seed role (#3b).
"""

from __future__ import annotations

import logging

from kontinuum.gitcmd import git

logger = logging.getLogger("kontinuum")

_SKIP_PREFIXES = ("docs/living-docs/", ".claude/")   # never treat docs or injected plugins as source


def changed_paths(workdir: str) -> list[str]:
    """Repo-relative source paths the agent changed in the worktree (modified + new).

    Reads `git status --porcelain -z --untracked-files=all`, so it lists files individually (not
    collapsed dirs) and respects `.git/info/exclude` — injected plugins and installed deps are
    already excluded and never appear. Living docs and `.claude/` are dropped so a doc-only pass
    can't feed on itself.
    """
    out = git(workdir, "status", "--porcelain", "-z", "--untracked-files=all")
    paths = []
    for entry in out.split("\0"):
        if len(entry) < 4:
            continue
        path = entry[3:]                              # strip the 2-char status code + its space
        if not path.startswith(_SKIP_PREFIXES):
            paths.append(path)
    return paths


def maintain(agent, workdir: str, task) -> None:
    """Best-effort: ask the agent to refresh living docs for the change, editing them in the worktree.

    No-op if the agent has no `update_docs` capability or nothing source-level changed. Any failure
    is swallowed — living docs are never allowed to block the code PR.
    """
    update = getattr(agent, "update_docs", None)
    if update is None:
        return
    try:
        paths = changed_paths(workdir)
        if not paths:
            return
        update(workdir, task, paths)
    except Exception as e:
        logger.warning("living-docs update skipped: %s", e)
