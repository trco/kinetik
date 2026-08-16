"""Per-issue planning (§16.1): write the plan down before coding, grounded in the living docs.

Two paths, one artifact — `docs/plans/<YYYY-MM-DD>-issue-<N>-<slug>.md`:

  **default (autonomous-until-PR)** — the planning instruction rides in the agent's own prompt, so
  the plan file is part of the same edit pass as the code. It lands in the SAME PR, and it is
  gate-checked, secret-scanned and reviewed along with everything else. No extra agent run, and a
  retry simply rewrites it (`propose` resets the worktree between attempts).

  **`kontinuum:plan-first`** — K runs a plan-only pass and opens a plan PR, then stops. Approval is
  the merge: the plan is then on the base branch, so the next run finds it with `existing_plan` and
  implements against it instead of writing a new one.

**K owns the filename** (`plan_path`), the agent only writes the file. Discovery is by filename —
K has no shell and no index — so letting the agent invent a name would strand the plan.

**Triviality is the agent's call.** A one-line fix warrants no plan file; the prompt says so
explicitly, because ceremony on trivial issues is worse than no plan at all.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from glob import glob

logger = logging.getLogger("kontinuum")

PLANS_DIR = "docs/plans"
PLAN_FIRST_LABEL = "kontinuum:plan-first"      # human override: approve the plan before any code
LIVING_DOCS_HINT = (
    "Ground the plan in how this repo actually works: read `docs/living-docs/INDEX.md` if it exists "
    "and the one or two pages covering the area you are about to touch (they are context to verify, "
    "not gospel). Fall back to reading the code where no page covers it."
)


def plan_first(labels) -> bool:
    return PLAN_FIRST_LABEL in labels


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].rstrip("-") or "plan"


def plan_path(number: int, title: str, today: str | None = None) -> str:
    """Repo-relative path K assigns to this issue's plan. Dated, and greppable by issue number."""
    today = today or datetime.utcnow().date().isoformat()
    return f"{PLANS_DIR}/{today}-issue-{number}-{_slug(title)}.md"


def existing_plan(workdir: str, number: int) -> str | None:
    """Repo-relative path of a plan for this issue already in the worktree, else None.

    True after a `plan-first` plan PR is merged (the plan is on the base branch the worktree is cut
    from). The date differs from today's, hence the glob; `-` after the number keeps #5 out of #51.
    """
    hits = sorted(glob(os.path.join(workdir, PLANS_DIR, f"*issue-{number}-*.md")))
    return os.path.relpath(hits[-1], workdir) if hits else None


def plan_prompt(task, today: str | None = None) -> str:
    """The planning instruction appended to the agent's implement prompt (default path)."""
    return (
        "Before coding, decide whether this issue warrants a written plan. A trivial change — a "
        "one-liner, a typo, a rename — does NOT: skip the plan and just make the change.\n"
        f"If it does warrant one, write it to `{plan_path(task.number, task.title, today)}` FIRST: "
        "the goal, the steps in order, the files each step touches, how it is verified, and what "
        "you are deliberately not doing. Keep it under a page — it is a plan, not a design doc. "
        f"{LIVING_DOCS_HINT}\n"
        "Then implement it, editing the plan where reality disagrees. The plan file is part of your "
        "change: it ships in the same pull request as the code, for the same human to review."
    )


def follow_prompt(path: str) -> str:
    """Instruction when a human already approved a plan for this issue (the `plan-first` path)."""
    return (
        f"A human-approved plan for this issue is already in the repo at `{path}`. Read it FIRST "
        "and implement it. Do not write a new plan file; edit that one only where reality disagrees "
        f"with it, and say so in your summary. {LIVING_DOCS_HINT}"
    )


def draft(agent, workdir: str, task, today: str | None = None) -> str | None:
    """Plan-only agent pass for `kontinuum:plan-first`. Returns the plan path, or None if it failed.

    Prompted directly (not via a bundled slash command) so the default path and this one share one
    instruction — `plan_prompt` — and can never drift. Needs the `run_command` capability: a plan
    pass runs no commands, so it needs no sandbox, just the agent's editing tools.
    """
    run = getattr(agent, "run_command", None)
    if run is None:
        logger.warning("#%s: plan-first needs an agent that can run a plan pass", task.number)
        return None
    prompt = (
        f"Write an implementation plan for this GitHub issue. Do NOT implement it.\n\n"
        f"Issue #{task.number}: {task.title}\n\n{task.body}\n\n"
        f"Write ONLY the file `{plan_path(task.number, task.title, today)}` — change no other file, "
        "and run no commands: a human reviews and merges this plan before any code is written.\n"
        "It must hold the goal, the steps in order, the files each step touches, how it is verified, "
        f"and what you are deliberately not doing. Keep it under a page. {LIVING_DOCS_HINT}"
    )
    try:
        run(workdir, prompt)
    except Exception as e:
        logger.warning("#%s: plan pass failed: %s", task.number, e)
        return None
    return existing_plan(workdir, task.number)
