"""Kontinuum: an autonomous agent that turns labelled GitHub issues into pull requests.

Each instance polls for ready issues (`loop`), claims one via an append-only lease log so
concurrent instances don't collide (`claim`, `protocol`), then runs the task pipeline
(`pipeline`): an agent edits an isolated worktree in a sandbox (`sandbox`, `agent`), a gate
verifies it (`verify`, `ci`), a reviewer judges it, and the Effect Broker opens the PR
(`effects`). `github` and `gitcmd` are the outside-world adapters; `__main__` is the CLI.
"""
