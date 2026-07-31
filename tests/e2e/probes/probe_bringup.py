"""Consolidated bring-up probe — task T0.1 of the E2E plan.

There is exactly one probe by design (plan D10): one disposable instance, one run, one
committed answer file. Once `observed.json` exists, no test may ship with an either/or
assertion about the platform's behaviour.

It answers the six open `[U]` items of §6 and re-measures the control mechanisms the
suite's non-vacuity depends on (D6, D11, D14, D15, D20).

Usage::

    .venv/bin/python tests/e2e/probes/probe_bringup.py --json
    .venv/bin/python tests/e2e/probes/probe_bringup.py --json --keep --verbose

Safety: every container is named `gwe2e-<runid>-*`, and `assert_ours` refuses to address
anything else. The INT containers are recorded before and after the run and asserted
unchanged — a mismatch exits non-zero even if every measurement succeeded.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

E2E_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = E2E_ROOT.parents[1]
sys.path.insert(0, str(E2E_ROOT))

import environment  # noqa: E402
import measure_controls  # noqa: E402
import measure_unknowns  # noqa: E402
from harness import docker_util as du  # noqa: E402
from harness import gramps_instance as gi  # noqa: E402
from harness.mcp_container import McpContainer, container_name  # noqa: E402
from harness.rest import GrampsRest  # noqa: E402

MCP_IMAGE_DEFAULT = "gramps-remote-mcp:latest"
FIXTURE_DEFAULT = REPO_ROOT / ".claude/worknotes/e2e-suite/e2e-fixture.gramps"
JWT_OVERRIDE_S = 45


class BringUpProbe:
    """Drives the measurement and owns every container's lifecycle."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.runid = uuid.uuid4().hex[:8]
        self.observed: dict[str, Any] = {}
        self.instances: list[gi.GrampsInstance] = []
        self.sessions: list[McpContainer] = []
        self.fixture = Path(args.fixture)
        self.backup_dir = Path(args.artifacts) / f"run-{self.runid}" / "backup"

    def merge(self, records: dict[str, dict]) -> None:
        for key, value in records.items():
            self.observed[key] = value
            if self.args.verbose:
                print(f"  [{key}] {json.dumps(value, ensure_ascii=False)[:280]}", flush=True)

    def step(self, title: str) -> None:
        print(f"\n== {title}", flush=True)

    def mcp_factory(self, instance: gi.GrampsInstance) -> Any:
        """Give the measurement functions sessions without teaching them the wiring."""

        def factory(
            *,
            destructive: bool = False,
            label: str = "",
            user: str = gi.OWNER_USER,
            password: str = gi.OWNER_PW,
        ) -> McpContainer:
            env = {
                "GRAMPS_BASE_URL": instance.internal_url,
                "GRAMPS_USERNAME": user,
                "GRAMPS_PASSWORD": password,
                "GRAMPS_BACKUP_DIR": "/data",
            }
            if destructive:
                env["GRAMPS_ENABLE_DESTRUCTIVE"] = "1"
            session = McpContainer(
                container_name(self.runid, f"{label.strip('-') or 'probe'}-{len(self.sessions)}"),
                self.args.mcp_image,
                env,
                network=instance.network,
                mounts=((str(self.backup_dir), "/data"),),
                runid=self.runid,
            )
            session.start()
            self.sessions.append(session)
            return session

        return factory

    def int_state(self) -> list[dict[str, str]]:
        template = "{{.Id}} {{.State.StartedAt}} {{.State.Status}}"
        return [
            {"name": name, "inspect": du.inspect_container(name, template)}
            for name in du.INT_CONTAINERS
        ]

    def bring_up(self) -> gi.GrampsInstance:
        self.step("bring-up — sharp instance (Redis limiter storage, 8 workers)")
        instance = gi.GrampsInstance(self.runid)
        instance.start()
        self.instances.append(instance)

        before = instance.unauthenticated_status("/api/token/create_owner/")
        owner = instance.add_user(gi.OWNER_USER, gi.OWNER_PW, gi.ROLE_OWNER)
        editor = instance.add_user(gi.EDITOR_USER, gi.EDITOR_PW, gi.ROLE_EDITOR)
        after = instance.unauthenticated_status("/api/token/create_owner/")

        self.merge(
            {
                "s5_readiness": {
                    "signal": "GET /api/openapi.json == 200",
                    "seconds": instance.readiness_s,
                    "budget_s": gi.READINESS_BUDGET_S,
                    "reference_2026_07_30": 5.7,
                    "port": instance.port,
                },
                "s5_bootstrap": {
                    "create_owner_before_first_user": before,
                    "create_owner_after_user_add": after,
                    "owner_add": owner,
                    "editor_add": editor,
                    "metadata_unauthenticated": instance.unauthenticated_status("/api/metadata/"),
                    "note": "rc is the only success signal; the Gtk-CRITICAL on stderr is harmless",
                },
            }
        )
        if owner["rc"] != 0:
            raise RuntimeError(f"owner creation failed: {owner}")
        return instance

    def seed(self, rest: GrampsRest, mcp: Any) -> None:
        self.step("seed the tree through the real MCP container")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.chmod(0o777)
        shutil.copy2(self.fixture, self.backup_dir / self.fixture.name)

        session = mcp(label="-seed")
        started = time.monotonic()
        imported = session.call("gramps_import_file", {"filename": self.fixture.name}, timeout=300)
        seconds = round(time.monotonic() - started, 2)
        session.close()

        counts = rest.object_counts()
        self.merge(
            {
                "s5_seed": {
                    "fixture": self.fixture.name,
                    "fixture_bytes": self.fixture.stat().st_size,
                    "import_seconds": seconds,
                    "import_is_error": imported.is_error,
                    "import_text_head": imported.text[:200],
                    "object_counts": counts,
                    "object_counts_keys": sorted(counts),
                    "total": sum(counts.values()),
                    "tags_is_a_counted_namespace": "tags" in counts,
                }
            }
        )
        if imported.is_error or sum(counts.values()) == 0:
            raise RuntimeError(f"seeding produced an empty tree: {imported.text[:200]}")

    def jwt_control(self) -> gi.GrampsInstance:
        control = gi.GrampsInstance(
            self.runid,
            suffix="-jwt",
            with_redis=False,
            extra_env={"GRAMPSWEB_JWT_ACCESS_TOKEN_EXPIRES": str(JWT_OVERRIDE_S)},
        )
        control.start()
        self.instances.append(control)
        control.add_user(gi.OWNER_USER, gi.OWNER_PW, gi.ROLE_OWNER)
        return control

    def execute(self) -> None:
        self.merge({"meta": environment.describe(self.runid, self.args.mcp_image)})
        instance = self.bring_up()
        rest = GrampsRest(instance.url, gi.OWNER_USER, gi.OWNER_PW)
        mcp = self.mcp_factory(instance)
        self.seed(rest, mcp)

        self.step("[U1] Name.type read shape")
        self.merge(measure_unknowns.name_type_shape(rest, mcp))

        self.step("[U3] structuredContent per return type")
        self.merge(measure_unknowns.structured_content(rest, mcp))

        self.step("[U4/D20] tools/list pagination and the gated-off tool shape")
        self.merge(measure_unknowns.tools_list(mcp))

        self.step("[U5] trailing slash and partial-PUT semantics")
        self.merge(measure_unknowns.put_semantics(rest))

        self.step("[U6] Celery worker command (read-only, from the running INT worker)")
        self.merge(measure_unknowns.celery(instance))

        self.step("[D6/D15] access log and token-oracle positive control")
        self.merge(measure_controls.access_log_and_oracle(instance, rest))

        self.step("[D14] limiter sharpness burst")
        self.merge(measure_controls.limiter_burst(instance))

        self.step("[U2] JWT lifetime and the shortening env var")
        self.merge(measure_unknowns.jwt(instance, rest, self.jwt_control(), JWT_OVERRIDE_S))

        self.step("[U7] 401-relogin trigger via a rotated secret key")
        self.merge(measure_unknowns.relogin_trigger(instance, rest))
        # The rotation invalidated this client's cached token; drop it so the next call mints
        # a new one. Constructing a second client here instead would mint immediately and, at
        # 1 login per second per IP, collide with the one the measurement just made.
        rest.invalidate()

        self.step("[§5/F1/F2] shapes, additive import, coerced confirm, wipe")
        self.merge(
            measure_controls.cycle(rest, mcp, self.fixture.name, self.backup_dir, self.runid)
        )

        self.step("[U8/D12] EDITOR role profile — everyday writes yes, destructive no")
        self.merge(measure_unknowns.editor_profile(rest, mcp))

    def run(self) -> int:
        int_before = self.int_state()
        status, error = "complete", None
        try:
            self.execute()
        except Exception as exc:  # noqa: BLE001 - the probe reports its own failure
            status, error = "failed", f"{type(exc).__name__}: {exc}"
            print(f"\n!! {error}", file=sys.stderr, flush=True)
        finally:
            for session in self.sessions:
                session.close()
            removed: list[str] = []
            if self.args.keep:
                print("\n-- --keep: containers left running", flush=True)
            else:
                self.step("teardown")
                for instance in self.instances:
                    removed += instance.teardown()
            int_after = self.int_state()
            self.merge(
                {
                    "safety": {
                        "int_containers_before": int_before,
                        "int_containers_after": int_after,
                        "int_untouched": int_before == int_after,
                        "created": [
                            item for instance in self.instances for item in instance.created
                        ],
                        "removed": removed,
                        "kept": bool(self.args.keep),
                        "ports": [instance.port for instance in self.instances],
                        "int_port_avoided": all(
                            instance.port != du.INT_PORT for instance in self.instances
                        ),
                    }
                }
            )
            self.observed["status"] = status
            self.observed["error"] = error

        self.publish()
        ok = status == "complete" and self.observed["safety"]["int_untouched"]
        print(f"\nstatus: {status}{'' if ok else '  (exit 1)'}", flush=True)
        return 0 if ok else 1

    def publish(self) -> None:
        payload = json.dumps(self.observed, indent=2, ensure_ascii=False) + "\n"
        if not self.args.json:
            print(payload)
            return
        out = Path(self.args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload)
        print(f"\nwrote {out}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Consolidated E2E bring-up probe (plan T0.1)")
    parser.add_argument("--json", action="store_true", help="write --out instead of printing")
    parser.add_argument("--out", default=str(Path(__file__).with_name("observed.json")))
    parser.add_argument("--mcp-image", default=MCP_IMAGE_DEFAULT)
    parser.add_argument("--fixture", default=str(FIXTURE_DEFAULT))
    parser.add_argument("--artifacts", default=str(REPO_ROOT / ".e2e-artifacts"))
    parser.add_argument("--keep", action="store_true", help="leave containers up for inspection")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if not Path(args.fixture).exists():
        parser.error(f"fixture not found: {args.fixture}")
    return BringUpProbe(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
