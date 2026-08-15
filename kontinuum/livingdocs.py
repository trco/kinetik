"""Living-docs maintenance (§16.1): after a change, refresh the repo's living docs in the SAME PR.

The split honours K's containment. K computes *what* changed (git — the agent has no Bash); the
agent DRAFTS the pages (Read/Edit/Write, no shell); `open_pr` commits code + docs together, and the
human reviews both. Best-effort: a docs failure never blocks the code PR.

Updating an existing area keeps its page true; a repo with no living docs yet gets a minimal set
bootstrapped for the touched area (organic growth) — a full initial survey is the seed role (#3b).
"""

from __future__ import annotations

import logging
import os

from kontinuum.gitcmd import git

logger = logging.getLogger("kontinuum")

_SKIP_PREFIXES = ("docs/living-docs/", ".claude/")   # never treat docs or injected plugins as source
LIVING_DOCS = "docs/living-docs"


def has_living_docs(workdir: str) -> bool:
    return os.path.isdir(os.path.join(workdir, LIVING_DOCS))


def seed(agent, workdir: str) -> bool:
    """Best-effort: author an initial living-docs set via the injected `/docs-seed` command.

    The prompt lives in the bundled command, not here — this just invokes it. Returns whether living
    docs exist after. No-op (returns current state) if the agent can't run commands. Used by
    `onboard` and the `seed-docs` command; the plugin must be injected into the worktree first.
    """
    run = getattr(agent, "run_command", None)
    if run is None:
        return has_living_docs(workdir)
    try:
        run(workdir, "/docs-seed")
    except Exception as e:
        logger.warning("living-docs seed skipped: %s", e)
    return has_living_docs(workdir)


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


def maintain(agent, workdir: str) -> None:
    """Best-effort: refresh living docs for the change via the injected `/docs-update` command.

    K computes the changed paths (the command has no shell) and passes them as arguments; the prompt
    lives in the bundled command. No-op if the agent can't run commands or nothing source-level
    changed. Failures are swallowed — living docs never block the code PR. (`execute()` injects the
    plugin, so the command resolves in the worktree.)
    """
    run = getattr(agent, "run_command", None)
    if run is None:
        return
    try:
        paths = changed_paths(workdir)
        if not paths:
            return
        run(workdir, "/docs-update " + " ".join(paths[:100]))
    except Exception as e:
        logger.warning("living-docs update skipped: %s", e)
