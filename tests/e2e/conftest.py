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

E2E_ROOT = Path(__file__).parent


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if item.path is not None and item.path.is_relative_to(E2E_ROOT):
            item.add_marker(pytest.mark.e2e)
