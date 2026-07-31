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

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from harness.gramps_instance import STARTED
from harness.mcp_container import STARTED as MCP_STARTED
from harness.mcp_container import ImageRef, image_from_env
from harness.runid import new_runid

E2E_ROOT = Path(__file__).parent


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if item.path is not None and item.path.is_relative_to(E2E_ROOT):
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session")
def e2e_runid() -> str:
    """One id for the whole run, so the image and every container carry the same label."""
    return new_runid()


@pytest.fixture(scope="session")
def mcp_image(e2e_runid: str) -> ImageRef:
    """The image under test (D8). Built from the working tree unless the environment says
    otherwise — so a local or PR run tests the branch, and CI can point at the artifact."""
    return image_from_env(e2e_runid, os.environ)


@pytest.fixture(scope="session", autouse=True)
def _no_leaked_containers() -> Iterator[None]:
    """Fail the run if anything this session started is still standing.

    A full `-m e2e` run once left a redis container, its network and both volumes behind while
    every test reported success — the teardown swallowed the failure and nobody looked. This
    turns that into a red run: it reaps what survived, then says what it had to reap, so the
    leak cannot quietly become somebody's disk.

    MCP containers go first: docker refuses to remove a network while a container still sits
    on it, so sweeping the instances first would turn one leak into three.
    """
    yield
    survivors = []
    for mcp in list(MCP_STARTED):
        mcp.close()
        survivors.append(f"{mcp.name} (leftovers: {mcp.leftovers or 'none on retry'})")
    MCP_STARTED.clear()
    for instance in list(STARTED):
        instance.teardown()
        survivors.append(f"{instance.web} (leftovers: {instance.leftovers or 'none on retry'})")
    STARTED.clear()
    if survivors:
        raise AssertionError("not torn down by their own test: " + "; ".join(survivors))
