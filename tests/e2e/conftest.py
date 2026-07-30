"""The gate for the e2e tree (plan D1).

`addopts = "-m 'not e2e'"` in `pyproject.toml` deselects these tests, but a deselection is
only as good as the marker it filters on. Rather than trust every future test file to
remember `@pytest.mark.e2e`, this stamps the marker on everything collected below this
directory: forgetting it cannot leak Docker into `pytest -q`.

The path check matters — `pytest_collection_modifyitems` is handed the *whole* item list,
not just the items under this conftest, so an unfiltered loop would mark the 259 unit tests
as e2e and silently empty the default run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from harness.gramps_instance import STARTED

E2E_ROOT = Path(__file__).parent


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if item.path is not None and item.path.is_relative_to(E2E_ROOT):
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session", autouse=True)
def _no_leaked_instances() -> object:
    """Fail the run if any instance this session started is still standing.

    A full `-m e2e` run once left a redis container, its network and both volumes behind while
    every test reported success — the teardown swallowed the failure and nobody looked. This
    turns that into a red run: it reaps what survived, then says what it had to reap, so the
    leak cannot quietly become somebody's disk.
    """
    yield
    if not STARTED:
        return
    survivors = []
    for instance in list(STARTED):
        instance.teardown()
        survivors.append(f"{instance.web} (leftovers: {instance.leftovers or 'none on retry'})")
    STARTED.clear()
    raise AssertionError("instances were not torn down by their own test: " + "; ".join(survivors))
