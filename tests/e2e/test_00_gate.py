"""Pins the gate mechanism itself (plan D1, T1.1).

This file deliberately carries **no** `@pytest.mark.e2e`. If `conftest.py` stops stamping the
marker, the first test still passes under `-m e2e` — but it starts running in a plain
`pytest -q`, which is precisely the leak the gate exists to prevent. The regression therefore
shows up as the default run's count changing from `259 passed, 2 deselected`.

Both tests need no Docker and no instance, so `-m e2e` stays cheap as a smoke test.
"""

from __future__ import annotations

import pytest


def test_marker_is_auto_stamped(request: pytest.FixtureRequest) -> None:
    assert request.node.get_closest_marker("e2e") is not None, (
        "tests/e2e/conftest.py must stamp the e2e marker on every item it collects"
    )


def test_default_run_deselects_e2e(request: pytest.FixtureRequest) -> None:
    """The stamp is half the gate; the deselection in `addopts` is the other half."""
    addopts = request.config.getini("addopts")
    assert any("not e2e" in str(opt) for opt in addopts), (
        f"pyproject.toml must keep deselecting the e2e marker by default, got {addopts}"
    )
