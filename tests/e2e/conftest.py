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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fixtures import cast
from harness import chain
from harness.backup_dir import BackupDir, ProbeError, verified_input
from harness.gramps_instance import (
    OWNER_PW,
    OWNER_USER,
    ROLE_OWNER,
    STARTED,
    GrampsInstance,
)
from harness.mcp_container import STARTED as MCP_STARTED
from harness.mcp_container import ImageRef, McpContainer, container_name, image_from_env
from harness.rest import GrampsRest
from harness.runid import new_runid

E2E_ROOT = Path(__file__).parent
SEED_FILE = "synthetic-tree.gramps"


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


# -- the shared substrate the Stage-1 matrices run on (plan D5, D17, T4.0) -----------------


@dataclass
class SeededTree:
    """What one matrix class is handed: the oracle, the container, the mount, its own scratch.

    `state` is a field of this object and never a class attribute. pytest re-instantiates a test
    class for every method, so a class attribute is the only thing that *would* survive between
    them — which is exactly why reaching for one is tempting and wrong: it is module-global
    mutable state, shared with every other class in the file, and it outlives the reseed that is
    supposed to have cleaned up after it. This object is rebuilt per class, so is `state`.
    """

    rest: GrampsRest
    mcp: McpContainer
    backup: BackupDir
    state: dict[str, Any] = field(default_factory=dict)


class Seeder:
    """Puts the canonical fixture back, and only when it is actually gone.

    Reseeding costs a wipe plus an import. Doing it per class regardless would pay that for
    every read-only group in the suite; doing it never would hand each class whatever the
    previous one left behind. So it is conditional on `content_fingerprint` — the *content*
    digest, because the identity one is byte-identical after every field write this server has
    (see `harness/records.py`), and a people-names matrix would drift under it invisibly.

    The digest is re-taken after each seed rather than compared to a stored constant: an import
    mints new handles, and family and source signatures carry handles. What is being asserted is
    "nothing has changed since we last seeded", not "the tree equals a value from last week".
    """

    def __init__(self, rest: GrampsRest) -> None:
        self.rest = rest
        self.baseline: str | None = None
        self.seeds = 0

    def ensure(self) -> bool:
        """True if it had to reseed. Returns rather than logs, so a test can assert on it."""
        if self.baseline is not None and self.rest.content_fingerprint() == self.baseline:
            return False
        self.rest.wipe()
        self.rest.import_bytes(verified_input(SEED_FILE))
        self._self_test()
        self.baseline = self.rest.content_fingerprint()
        self.seeds += 1
        return True

    def _self_test(self) -> None:
        """A mis-seeded substrate has to fail here, not as twenty mysterious red cases.

        The import is **additive** (measured, plan §5): a wipe that silently did nothing leaves
        a doubled tree that still looks like a tree, and every count assertion in every matrix
        would then be wrong by exactly a factor nobody would think to look for.
        """
        counts = self.rest.object_counts()
        if counts != cast.COUNTS:
            raise ProbeError(f"reseed left {counts}, the fixture declares {cast.COUNTS}")


@pytest.fixture(scope="session")
def _e2e_instance(e2e_runid: str) -> Iterator[GrampsInstance]:
    """One live instance for the whole session (A5): 135 cases cannot each pay a bring-up."""
    started = GrampsInstance(e2e_runid)
    started.start()
    assert started.add_user(OWNER_USER, OWNER_PW, ROLE_OWNER)["rc"] == 0
    try:
        yield started
    finally:
        started.teardown()


@pytest.fixture(scope="session")
def _e2e_rest(_e2e_instance: GrampsInstance) -> GrampsRest:
    """One oracle, so one token. A per-class client would mint inside the 1/second window and
    429 the run — the limiter is keyed by client IP, not by object (`harness/rest.py:21-24`)."""
    return GrampsRest(_e2e_instance.url, OWNER_USER, OWNER_PW)


@pytest.fixture(scope="session")
def _e2e_backup(tmp_path_factory: pytest.TempPathFactory) -> BackupDir:
    directory = BackupDir(tmp_path_factory.mktemp("mount") / "data")
    directory.reset()
    return directory


@pytest.fixture(scope="session")
def _e2e_mcp(
    _e2e_instance: GrampsInstance, mcp_image: ImageRef, _e2e_backup: BackupDir
) -> Iterator[McpContainer]:
    """One container, destructive tools registered — a matrix that cannot call them cannot test
    that they refuse."""
    container = McpContainer(
        container_name(_e2e_instance.runid, "matrix"),
        mcp_image.run_reference,
        {
            "GRAMPS_BASE_URL": _e2e_instance.internal_url,
            "GRAMPS_USERNAME": OWNER_USER,
            "GRAMPS_PASSWORD": OWNER_PW,
            "GRAMPS_BACKUP_DIR": "/data",
            "GRAMPS_ENABLE_DESTRUCTIVE": "1",
        },
        network=_e2e_instance.network,
        mounts=((str(_e2e_backup.path), "/data"),),
        runid=_e2e_instance.runid,
    )
    try:
        yield container
    finally:
        container.close()


@pytest.fixture(scope="session")
def _e2e_seeder(_e2e_rest: GrampsRest) -> Seeder:
    return Seeder(_e2e_rest)


@pytest.fixture(scope="class")
def seeded_tree(
    _e2e_rest: GrampsRest,
    _e2e_mcp: McpContainer,
    _e2e_backup: BackupDir,
    _e2e_seeder: Seeder,
) -> SeededTree:
    """The canonical fixture, in a known state, once per class.

    Order matters and is D17's: the backup directory is step 0, because three assertion sets
    read it and a leftover export makes "the model exported a file" pass for a call that never
    ran. Then the tree, if it moved.
    """
    _e2e_backup.reset()
    _e2e_seeder.ensure()
    return SeededTree(rest=_e2e_rest, mcp=_e2e_mcp, backup=_e2e_backup)


# -- ordered chains: one red and N skipped (plan D5) ---------------------------------------


def _chain_stash(item: pytest.Item) -> pytest.Stash | None:
    """The class collector's stash, for a method of a class marked `chain` — else None.

    Opt-in by marker rather than "every class is a chain". A class is also how the matrices
    group *independent* read-only cases so they share one reseed, and skipping the siblings of
    a failure there would hide coverage rather than reduce noise. The asymmetry is the same one
    the `refusal` marker is linted for: a missing marker costs noise, a wrong one costs silence.
    """
    if item.cls is None or item.parent is None:  # type: ignore[attr-defined]
        return None
    return item.parent.stash if item.get_closest_marker("chain") else None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    report = yield
    stash = _chain_stash(item)
    if stash is not None:
        chain.note_outcome(stash, name=item.name, when=report.when, failed=report.failed)
    return report


def pytest_runtest_setup(item: pytest.Item) -> None:
    stash = _chain_stash(item)
    if stash is None:
        return
    reason = chain.skip_reason(stash)
    if reason:
        pytest.skip(reason)
