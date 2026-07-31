"""Run provenance: what was measured, against which artifacts.

Separate from the driver because a measurement is worthless without it — a committed
`observed.json` has to say which image ids, which `mcp` version and which commit produced
it, or a later reader cannot tell whether it still applies.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from harness.docker_util import docker, run
from harness.gramps_instance import GRAMPSWEB_IMAGE, GUNICORN_CMD
from harness.mcp_session import McpSession


def _git(*args: str) -> str:
    return run(["git", *args], check=False).stdout.strip()


def describe(runid: str, mcp_image: str) -> dict[str, Any]:
    return {
        "runid": runid,
        "probe": "tests/e2e/probes/probe_bringup.py",
        "plan": "docs/superpowers/plans/2026-07-30-e2e-test-suite.md",
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "grampsweb_image": GRAMPSWEB_IMAGE,
        "grampsweb_image_id": docker("image", "inspect", GRAMPSWEB_IMAGE, "--format", "{{.Id}}"),
        "mcp_image": mcp_image,
        "mcp_image_id": docker("image", "inspect", mcp_image, "--format", "{{.Id}}"),
        "mcp_lib_version": run(
            [sys.executable, "-c", "import importlib.metadata as m; print(m.version('mcp'))"],
            check=False,
        ).stdout.strip(),
        "python": sys.version.split()[0],
        "gunicorn_cmd_override": GUNICORN_CMD,
        "client_protocol_version": McpSession.PROTOCOL_VERSION,
    }
