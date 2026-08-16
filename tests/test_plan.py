"""Per-issue planning (§16.1): K owns the plan path, the agent judges whether to write one."""

from __future__ import annotations

from datetime import datetime, timedelta

from kontinuum import loop
from kontinuum.agent import _prompt
from kontinuum.pipeline import Task
from kontinuum.plan import draft, existing_plan, plan_first, plan_path, plan_prompt
from kontinuum.protocol import attempt_claim
from tests.fakes import FakeIssueQueue

TASK = Task(7, "Add a retry to the fetcher", "body")
NOW = datetime(2026, 8, 16, 12, 0, 0)
LEASE = timedelta(hours=1)


def _write_plan(tmp_path, name):
    (tmp_path / "docs" / "plans").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "plans" / name).write_text("# plan\n")


def test_plan_path_is_dated_slugged_and_carries_the_issue_number():
    assert plan_path(7, "Add a retry to the fetcher", "2026-08-16") == \
        "docs/plans/2026-08-16-issue-7-add-a-retry-to-the-fetcher.md"


def test_plan_path_survives_a_title_of_pure_punctuation():
    assert plan_path(7, "!!! ???", "2026-08-16") == "docs/plans/2026-08-16-issue-7-plan.md"


def test_existing_plan_finds_a_plan_written_on_another_day(tmp_path):
    _write_plan(tmp_path, "2026-01-02-issue-7-old.md")
    assert existing_plan(str(tmp_path), 7) == "docs/plans/2026-01-02-issue-7-old.md"


def test_existing_plan_does_not_confuse_issue_7_with_issue_71(tmp_path):
    _write_plan(tmp_path, "2026-08-16-issue-71-other.md")
    assert existing_plan(str(tmp_path), 7) is None        # the trailing dash is what separates them
    assert existing_plan(str(tmp_path), 71) is not None


def test_existing_plan_is_none_without_a_plans_dir(tmp_path):
    assert existing_plan(str(tmp_path), 7) is None


def test_plan_prompt_lets_the_agent_skip_ceremony_and_grounds_it_in_living_docs():
    p = plan_prompt(TASK, "2026-08-16")
    assert "trivial" in p and "docs/living-docs/INDEX.md" in p
    assert plan_path(7, TASK.title, "2026-08-16") in p    # K assigns the path, the agent just writes it
    assert "same pull request" in p                       # default: the plan ships with the code


def test_implement_prompt_asks_for_a_plan_when_none_exists(tmp_path):
    p = _prompt(TASK)
    assert "Before coding, decide whether this issue warrants a written plan" in p


def test_implement_prompt_follows_an_approved_plan_instead_of_writing_one():
    p = _prompt(TASK, "docs/plans/2026-01-02-issue-7-old.md")
    assert "docs/plans/2026-01-02-issue-7-old.md" in p
    assert "Do not write a new plan file" in p


def test_plan_first_label():
    assert plan_first(["kontinuum:ready", "kontinuum:plan-first"])
    assert not plan_first(["kontinuum:ready"])


def test_draft_needs_an_agent_that_can_run_a_plan_pass(tmp_path):
    class Agent:                                          # no run_command -> cannot plan
        pass

    assert draft(Agent(), str(tmp_path), TASK) is None


def test_draft_returns_the_plan_the_agent_wrote(tmp_path):
    seen = {}

    class Agent:
        def run_command(self, workdir, command):
            seen["command"] = command
            _write_plan(tmp_path, "2026-08-16-issue-7-x.md")

    assert draft(Agent(), str(tmp_path), TASK) == "docs/plans/2026-08-16-issue-7-x.md"
    assert "Do NOT implement it" in seen["command"]       # plan-only: a human approves before code
    assert "Issue #7: Add a retry to the fetcher" in seen["command"]


def test_draft_reports_failure_when_the_agent_wrote_nothing(tmp_path):
    class Agent:
        def run_command(self, workdir, command):
            pass

    assert draft(Agent(), str(tmp_path), TASK) is None


def test_draft_swallows_agent_errors(tmp_path):
    class Agent:
        def run_command(self, workdir, command):
            raise RuntimeError("boom")

    assert draft(Agent(), str(tmp_path), TASK) is None


ISSUE = Task(1, "t", "b")


def _planning_agent(tmp_path):
    class Agent:
        def run_command(self, workdir, command):
            _write_plan(tmp_path, "2026-08-16-issue-1-x.md")

    return Agent()


def _owned_queue():
    q = FakeIssueQueue()
    attempt_claim(q, "me", NOW, LEASE)                    # so still_owns passes the pre-effect guard
    return q


def _patch_effects(monkeypatch, existing_pr=None, secrets=()):
    monkeypatch.setattr(loop, "_find_open_pr", lambda repo, branch: existing_pr)
    monkeypatch.setattr(loop, "scan_worktree", lambda workdir: list(secrets))
    monkeypatch.setattr(loop, "open_pr", lambda *a: "https://pr/plan")


def test_plan_first_pr_opens_a_plan_pr_and_hands_back_to_a_human(monkeypatch, tmp_path):
    _patch_effects(monkeypatch)
    q = _owned_queue()
    url = loop.plan_first_pr(q, "me", _planning_agent(tmp_path), str(tmp_path), ISSUE, "main")
    assert url == "https://pr/plan"
    assert q.label == "needs-triage"                      # stops being polled until a human promotes
    assert "Merge it to approve" in q.comments[-1]


def test_plan_first_pr_reuses_an_open_plan_pr(monkeypatch, tmp_path):
    _patch_effects(monkeypatch, existing_pr="https://pr/old")

    class Agent:
        def run_command(self, workdir, command):
            raise AssertionError("must not redraft while a plan PR is open")

    q = _owned_queue()
    assert loop.plan_first_pr(q, "me", Agent(), str(tmp_path), ISSUE, "main") == "https://pr/old"
    assert q.label == "needs-triage"


def test_plan_first_pr_blocks_when_no_plan_was_produced(monkeypatch, tmp_path):
    _patch_effects(monkeypatch)

    class Agent:
        pass

    q = _owned_queue()
    assert loop.plan_first_pr(q, "me", Agent(), str(tmp_path), ISSUE, "main") is None
    assert q.label == "blocked"


def test_plan_first_pr_never_pushes_a_plan_carrying_a_secret(monkeypatch, tmp_path):
    _patch_effects(monkeypatch, secrets=["private key in added line"])
    q = _owned_queue()
    assert loop.plan_first_pr(q, "me", _planning_agent(tmp_path), str(tmp_path), ISSUE, "main") is None
    assert q.label == "blocked"


def test_plan_first_pr_opens_nothing_once_the_lease_is_lost(monkeypatch, tmp_path):
    _patch_effects(monkeypatch)
    q = FakeIssueQueue()                                  # never claimed -> still_owns is False
    assert loop.plan_first_pr(q, "me", _planning_agent(tmp_path), str(tmp_path), ISSUE, "main") is None
    assert q.label is None


def test_plan_pr_does_not_close_the_issue():
    body = loop.plan_pr_body(4)
    assert "Closes #" not in body                         # merging the plan approves it, nothing more
    assert "Refs #4" in body
