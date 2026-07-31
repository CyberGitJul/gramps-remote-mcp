"""Shared harness for the end-to-end suite.

Phase 0 creates the modules the bring-up probe needs; Phase 1 fleshes them out into
the full harness described in `docs/superpowers/plans/2026-07-30-e2e-test-suite.md`.
Nothing in here is imported by the unit suite — these files are not `test_*.py`, so
`pytest -q` never collects them.
"""
