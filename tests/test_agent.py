"""The agent seam: real adapters and test fakes must satisfy the documented contracts."""

from __future__ import annotations

import pytest

from kinetik.agent import AgentRunner, ClaudeAgentRunner, ClaudeReviewer, Reviewer
from tests.fakes import FakeAgentRunner, FakeReviewer


@pytest.mark.parametrize("runner", [ClaudeAgentRunner(), FakeAgentRunner(lambda wd: None)])
def test_runners_satisfy_agent_runner(runner):
    assert isinstance(runner, AgentRunner)      # names only — signatures are the docstring's job


@pytest.mark.parametrize("reviewer", [ClaudeReviewer(), FakeReviewer()])
def test_reviewers_satisfy_reviewer(reviewer):
    assert isinstance(reviewer, Reviewer)
