"""A disposable Gramps Web instance: own network, own Redis, own port (plan D6, D14).

Two properties are load-bearing and measured, not assumed:

* **Redis-backed limiter storage.** Without `GRAMPSWEB_RATELIMIT_STORAGE_URI` flask-limiter
  falls back to `memory://` = one counter per gunicorn worker, which makes the 1/second
  token limit *leaky and nondeterministic* (measured: 2 of 10 got through). A 429
  regression test would then be flaky-green — worse than absent.
* **The access log.** The image's default CMD has no `--access-logfile`, so counting token
  POSTs from `docker logs` silently returns 0. The CMD override adds it and nothing else;
  it stays shell form so the image's own env defaults keep working.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from .docker_util import NAME_PREFIX, ProbeError, assert_ours, docker, free_port, run

GRAMPSWEB_IMAGE = "ghcr.io/gramps-project/grampsweb:latest"
REDIS_IMAGE = "redis:alpine"
TREE_NAME = "gwe2e"

OWNER_USER, OWNER_PW = "owner", "e2e-owner-not-a-real-secret"
EDITOR_USER, EDITOR_PW = "editor", "e2e-editor-not-a-real-secret"
ROLE_OWNER, ROLE_EDITOR = 4, 3

READINESS_BUDGET_S = 30.0
READINESS_POLL_S = 0.5

GUNICORN_CMD = (
    "gunicorn -w ${GUNICORN_NUM_WORKERS:-8} -b 0.0.0.0:5000 gramps_webapi.wsgi:app "
    "--timeout ${GUNICORN_TIMEOUT:-300} --limit-request-line 8190 "
    '--access-logfile - --access-logformat "ACCESS %(h)s %(r)s %(s)s"'
)


class GrampsInstance:
    """Brings one instance up, hands out its facts, tears it down again."""

    def __init__(
        self,
        runid: str,
        *,
        suffix: str = "",
        with_redis: bool = True,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        tag = f"{NAME_PREFIX}{runid}{suffix}"
        self.runid = runid
        self.with_redis = with_redis
        self.extra_env = extra_env or {}
        self.network = f"{tag}-net"
        self.web = f"{tag}-web"
        self.redis = f"{tag}-redis"
        self.port = 0
        self.readiness_s = 0.0
        self.created: list[str] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def internal_url(self) -> str:
        """How a sibling container on the same network reaches this instance."""
        return f"http://{self.web}:5000"

    def start(self) -> None:
        self.port = free_port()
        docker("network", "create", self.network)
        self.created.append(f"network:{self.network}")

        env = {
            "GRAMPSWEB_TREE": TREE_NAME,
            "GRAMPSWEB_SECRET_KEY": f"e2e-{self.runid}-not-a-real-secret",
        }
        if self.with_redis:
            docker(
                "run",
                "-d",
                "--rm",
                "--name",
                assert_ours(self.redis),
                "--network",
                self.network,
                REDIS_IMAGE,
            )
            self.created.append(f"container:{self.redis}")
            env["GRAMPSWEB_RATELIMIT_STORAGE_URI"] = f"redis://{self.redis}:6379/0"
        env.update(self.extra_env)

        args = [
            "run",
            "-d",
            "--rm",
            "--name",
            assert_ours(self.web),
            "--network",
            self.network,
            "-p",
            f"{self.port}:5000",
        ]
        for key, value in env.items():
            args += ["-e", f"{key}={value}"]
        args += [GRAMPSWEB_IMAGE, "sh", "-c", GUNICORN_CMD]
        docker(*args)
        self.created.append(f"container:{self.web}")
        self.readiness_s = self._await_ready()

    def _await_ready(self) -> float:
        """Poll `GET /api/openapi.json`; the tree is created before the port binds."""
        started = time.monotonic()
        last = ""
        while time.monotonic() - started < READINESS_BUDGET_S:
            try:
                reply = requests.get(f"{self.url}/api/openapi.json", timeout=5)
                if reply.status_code == 200:
                    return round(time.monotonic() - started, 2)
                last = f"HTTP {reply.status_code}"
            except requests.RequestException as exc:
                last = type(exc).__name__
            time.sleep(READINESS_POLL_S)
        raise ProbeError(f"{self.web} not ready within {READINESS_BUDGET_S}s (last: {last})")

    def unauthenticated_status(self, path: str) -> int:
        try:
            return requests.get(f"{self.url}{path}", timeout=15).status_code
        except requests.RequestException:
            return -1

    def add_user(self, name: str, password: str, role: int) -> dict[str, Any]:
        """`user add` in its simple form. Only `rc` is a success signal — the command
        prints a harmless `Gtk-CRITICAL` on stderr."""
        proc = run(
            [
                "docker",
                "exec",
                assert_ours(self.web),
                "python3",
                "-m",
                "gramps_webapi",
                "user",
                "add",
                name,
                password,
                "--role",
                str(role),
            ],
            check=False,
        )
        return {
            "rc": proc.returncode,
            "stdout": proc.stdout.strip()[:300],
            "stderr": proc.stderr.strip()[:300],
        }

    def exec_out(self, *args: str) -> str:
        return run(["docker", "exec", assert_ours(self.web), *args], check=False).stdout.strip()

    def access_log(self) -> list[str]:
        out = docker("logs", assert_ours(self.web), timeout=60, check=False)
        return [line for line in out.splitlines() if line.startswith("ACCESS ")]

    def token_post_log(self) -> list[str]:
        return [line for line in self.access_log() if "POST /api/token/" in line]

    def redis_keys(self, pattern: str = "LIMIT*") -> list[str]:
        if not self.with_redis:
            return []
        out = docker(
            "exec",
            assert_ours(self.redis),
            "redis-cli",
            "--scan",
            "--pattern",
            pattern,
            check=False,
        )
        return [line for line in out.splitlines() if line.strip()]

    def teardown(self) -> list[str]:
        removed = []
        for name in (self.web, self.redis):
            if f"container:{name}" in self.created:
                docker("rm", "-f", assert_ours(name), check=False)
                removed.append(name)
        if f"network:{self.network}" in self.created:
            docker("network", "rm", self.network, check=False)
            removed.append(self.network)
        return removed
