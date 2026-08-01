"""T1.3 acceptance: the disposable instance must be repeatable and leave nothing behind.

Two consecutive bring-ups in one session, each torn down, INT untouched, no `gwe2e-*`
container, network or volume left over. This is the property every later phase leans on —
if it does not hold, a red test tomorrow means "the previous run polluted the host", not
"the code is broken".

Needs Docker. Auto-marked `e2e` by conftest.py, so `pytest -q` never runs it.
"""

from __future__ import annotations

import pytest
from harness.docker_util import INT_CONTAINERS, NAME_PREFIX, docker, inspect_container
from harness.gramps_instance import (
    OWNER_PW,
    OWNER_USER,
    READINESS_BUDGET_S,
    ROLE_OWNER,
    GrampsInstance,
)
from harness.runid import LABEL_KEY, new_runid

# Needs a live Gramps Web instance, not merely Docker. Declared rather than inferred: the
# required CI leg selects `-m 'e2e and not gramps'`, and a module that forgets this marker
# would drag a whole instance bring-up into the job that is meant to need no services.
pytestmark = pytest.mark.gramps


def _ours(kind: str) -> set[str]:
    listing = {
        "container": ("ps", "-a", "--filter", f"label={LABEL_KEY}", "--format", "{{.Names}}"),
        "network": ("network", "ls", "--filter", f"label={LABEL_KEY}", "--format", "{{.Name}}"),
        "volume": ("volume", "ls", "--filter", f"label={LABEL_KEY}", "--format", "{{.Name}}"),
    }[kind]
    return {
        line.strip()
        for line in docker(*listing, check=False).splitlines()
        if line.strip().startswith(NAME_PREFIX)
    }


def _int_state() -> list[str]:
    return [inspect_container(name, "{{.Id}} {{.State.StartedAt}}") for name in INT_CONTAINERS]


def test_two_consecutive_bring_ups_leave_nothing_behind() -> None:
    int_before = _int_state()
    leftovers_before = {kind: _ours(kind) for kind in ("container", "network", "volume")}
    readiness = []

    for _ in range(2):
        instance = GrampsInstance(new_runid())
        try:
            instance.start()
            readiness.append(instance.readiness_s)
            assert instance.readiness_s < READINESS_BUDGET_S
            assert instance.add_user(OWNER_USER, OWNER_PW, ROLE_OWNER)["rc"] == 0
            assert instance.unauthenticated_status("/api/openapi.json") == 200
        finally:
            instance.teardown()

        assert instance.leftovers == [], "teardown reported resources it could not remove"
        for kind, before in leftovers_before.items():
            assert _ours(kind) == before, f"{kind}s left behind after teardown"

    assert len(readiness) == 2
    assert _int_state() == int_before, "the INT instance must never be touched"
