"""Kontinuum: a persistent orchestrator that turns curated GitHub issues into pull requests.

It polls a shared issue queue, claims one issue via an epoch-leased claim log so no
other instance double-works it, runs a coding agent inside a credential-free sandbox,
verifies the result with a local gate plus the repo's CI, and opens a PR for a human
to review. It never merges or deploys.
"""
