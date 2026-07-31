"""Command-line surface for the e2e suite (plan D18).

pytest honours `pytest_addoption` only in an *initial* conftest, and `tests/e2e/conftest.py`
is not one when pytest is invoked from the repo root. The flags therefore live here, so
`pytest -m e2e --e2e-force` works from anywhere; nothing else in this file touches the unit
suite, and the harness is imported lazily so a plain `pytest -q` never loads it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

E2E_ROOT = Path(__file__).parent / "e2e"
REPO_ROOT = Path(__file__).parents[1]


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("e2e", "end-to-end suite")
    group.addoption(
        "--e2e-reap-only",
        action="store_true",
        help="reap stale gwe2e-* containers/networks/images, prune artifact dirs, then exit",
    )
    group.addoption(
        "--e2e-force",
        action="store_true",
        help="let the reaper remove a *running* instance — it refuses by default (D18)",
    )
    group.addoption(
        "--e2e-keep-runs",
        type=int,
        default=None,
        help="artifact run directories to keep when reaping (default: 3)",
    )


def pytest_configure(config: pytest.Config) -> None:
    if not config.getoption("--e2e-reap-only"):
        return

    sys.path.insert(0, str(E2E_ROOT))
    from harness.reaper import format_report, reap
    from harness.runid import DEFAULT_KEEP_RUNS, prune_run_dirs

    keep = config.getoption("--e2e-keep-runs")
    report = reap(force=bool(config.getoption("--e2e-force")))
    pruned = prune_run_dirs(REPO_ROOT, keep=DEFAULT_KEEP_RUNS if keep is None else keep)

    print("\n" + format_report(report))
    if pruned:
        print(f"  artifacts pruned: {', '.join(pruned)}")
    pytest.exit("--e2e-reap-only: reaped, nothing else to do", returncode=0)
