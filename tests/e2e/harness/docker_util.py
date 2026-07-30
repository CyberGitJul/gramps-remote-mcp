"""Subprocess and Docker plumbing, plus the tripwires that keep INT out of reach.

Every container the suite creates is named ``gwe2e-<runid>-*``. `assert_ours` is the
single choke point: nothing that is not prefixed can be stopped, removed or addressed,
so a bug in a caller cannot reach the INT instance.
"""

from __future__ import annotations

import base64
import json
import socket
import subprocess
from typing import Any

NAME_PREFIX = "gwe2e-"

# The per-run image built from the working tree (plan D8). The prefix lives here rather than
# in `mcp_container.py` because the reaper globs for it: if the two ever disagree, every run
# leaks an image and nothing says so.
E2E_IMAGE_PREFIX = "gramps-remote-mcp:e2e-"
E2E_IMAGE_GLOB = f"{E2E_IMAGE_PREFIX}*"

# The INT instance. Named explicitly so "don't touch it" is a check, not a convention.
INT_CONTAINERS = ("gramps-grampsweb-1", "grampsweb_celery", "grampsweb_redis")
INT_PORT = 5055


class ProbeError(RuntimeError):
    """A measurement could not be taken; the run aborts and tears down."""


def run(
    args: list[str], *, timeout: float = 120.0, check: bool = True
) -> subprocess.CompletedProcess:
    """Run a command from an explicit argv — never a shell string."""
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if check and proc.returncode != 0:
        raise ProbeError(f"{args[:3]}… exited {proc.returncode}: {proc.stderr.strip()[:400]}")
    return proc


def docker(*args: str, timeout: float = 120.0, check: bool = True) -> str:
    return run(["docker", *args], timeout=timeout, check=check).stdout.strip()


def assert_ours(name: str) -> str:
    """Refuse to address any container that is not one of ours."""
    if not name.startswith(NAME_PREFIX) or name in INT_CONTAINERS:
        raise ProbeError(f"refusing to touch container {name!r} — not a {NAME_PREFIX}* container")
    return name


def free_port() -> int:
    """Ask the kernel for an unused port, and never hand back the INT port."""
    with socket.socket() as sock:
        sock.bind(("", 0))
        port = int(sock.getsockname()[1])
    if port == INT_PORT:  # pragma: no cover - the kernel does not hand out a bound port
        raise ProbeError("refusing to bind the INT port")
    return port


def resource_exists(kind: str, name: str) -> bool:
    """Is this container / network / volume still there?

    Removal commands run with `check=False` so a teardown never masks the failure it is
    cleaning up after — which means the only way to know a removal worked is to look.
    """
    listing = {
        "container": ("ps", "-a", "--format", "{{.Names}}"),
        "network": ("network", "ls", "--format", "{{.Name}}"),
        "volume": ("volume", "ls", "--format", "{{.Name}}"),
    }[kind]
    return name in docker(*listing, check=False).split()


def inspect_container(name: str, template: str) -> str:
    """Read-only inspect that works on foreign containers too (INT state recording)."""
    return docker("inspect", name, "--format", template, check=False)


def jwt_payload(token: str) -> dict[str, Any]:
    body = token.split(".")[1]
    body += "=" * (-len(body) % 4)
    return json.loads(base64.urlsafe_b64decode(body))
