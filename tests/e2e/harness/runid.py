"""Run identity and per-run artifact directories.

One runid per suite run, stamped into every container name *and* as a Docker label. The name
prefix is what `assert_ours` checks; the label is what the reaper filters on, so a container
whose name was mangled is still findable.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

LABEL_KEY = "gramps-e2e"
ARTIFACTS_DIRNAME = ".e2e-artifacts"
DEFAULT_KEEP_RUNS = 3


def new_runid() -> str:
    return uuid.uuid4().hex[:8]


def label_args(runid: str) -> list[str]:
    """`docker run`/`network create` arguments that tag a resource as ours."""
    return ["--label", f"{LABEL_KEY}={runid}"]


def artifacts_root(repo_root: Path) -> Path:
    return repo_root / ARTIFACTS_DIRNAME


def run_dir(repo_root: Path, runid: str) -> Path:
    return artifacts_root(repo_root) / f"run-{runid}"


def prune_run_dirs(repo_root: Path, keep: int = DEFAULT_KEEP_RUNS) -> list[str]:
    """Delete all but the newest `keep` run directories. Returns what it removed.

    `keep <= 0` is treated as "keep nothing" rather than "keep everything", so a mistyped
    flag cannot quietly disable the pruning it was meant to tighten.
    """
    root = artifacts_root(repo_root)
    if not root.exists():
        return []
    runs = sorted(
        (path for path in root.iterdir() if path.is_dir() and path.name.startswith("run-")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for path in runs[max(keep, 0) :]:
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path.name)
    return removed
