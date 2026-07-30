"""Reaps leftovers from earlier runs — without ever killing a live one (plan D18).

A killed run leaves containers behind, and two terminals (or a deliberate `--keep` instance)
can legitimately have a run in flight. A reaper that simply removes everything it recognises
destroys the first run mid-suite. So the decision is a *pure* function over what `docker
inspect` reports, testable without Docker, and it prints every skip instead of staying silent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .docker_util import INT_CONTAINERS, NAME_PREFIX, ProbeError, docker
from .runid import LABEL_KEY

LIVE_RUN_GRACE_MIN = 60


@dataclass(frozen=True)
class Candidate:
    """One reapable resource, as observed — not as assumed."""

    name: str
    runid: str
    running: bool
    started_at: datetime | None


def parse_docker_time(value: str) -> datetime | None:
    """Docker prints RFC 3339 with nanoseconds, which `fromisoformat` rejects before 3.11."""
    text = value.strip()
    if not text or text.startswith("0001-01-01"):
        return None
    if "." in text:
        head, _, tail = text.partition(".")
        fraction = "".join(char for char in tail if char.isdigit())[:6]
        suffix = "+00:00" if tail.endswith("Z") or text.endswith("Z") else ""
        text = f"{head}.{fraction or '0'}{suffix}"
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def should_reap(
    candidate: Candidate,
    *,
    now: datetime,
    grace_min: int = LIVE_RUN_GRACE_MIN,
    force: bool = False,
) -> tuple[bool, str]:
    """Decide, and say why. The reason is printed, so it is part of the contract."""
    if candidate.name.lstrip("/") in INT_CONTAINERS:
        return False, "INT container — never reaped, not even with force"
    if not candidate.name.lstrip("/").startswith(NAME_PREFIX):
        return False, f"not a {NAME_PREFIX}* resource"
    if not candidate.running:
        return True, "stopped"
    if force:
        return True, "running, but --e2e-force was given"
    if candidate.started_at is None:
        return False, "running with an unreadable start time — treated as live"
    age_min = (now - candidate.started_at).total_seconds() / 60
    if age_min < grace_min:
        return False, f"running and only {age_min:.0f} min old — probably a live run"
    return True, f"running but {age_min:.0f} min old (grace {grace_min} min)"


def _candidates() -> list[Candidate]:
    names = [
        line.strip()
        for line in docker(
            "ps", "-a", "--filter", f"label={LABEL_KEY}", "--format", "{{.Names}}", check=False
        ).splitlines()
        if line.strip()
    ]
    found = []
    for name in names:
        raw = docker(
            "inspect",
            name,
            "--format",
            '{{.State.Running}}|{{.State.StartedAt}}|{{index .Config.Labels "' + LABEL_KEY + '"}}',
            check=False,
        )
        parts = raw.split("|")
        if len(parts) != 3:
            continue
        running, started, runid = parts
        found.append(
            Candidate(
                name=name,
                runid=runid.strip(),
                running=running.strip() == "true",
                started_at=parse_docker_time(started),
            )
        )
    return found


def reap(*, force: bool = False, grace_min: int = LIVE_RUN_GRACE_MIN) -> dict[str, list[str]]:
    """Remove stale containers, then their networks, then stale `:e2e-*` images."""
    now = datetime.now(UTC)
    report: dict[str, list[str]] = {"removed": [], "skipped": [], "images": [], "networks": []}
    live_runids = set()

    for candidate in _candidates():
        decision, reason = should_reap(candidate, now=now, grace_min=grace_min, force=force)
        if not decision:
            report["skipped"].append(f"{candidate.name}: {reason}")
            live_runids.add(candidate.runid)
            continue
        docker("rm", "-f", candidate.name, check=False)
        report["removed"].append(f"{candidate.name}: {reason}")

    for line in docker(
        "network", "ls", "--filter", f"label={LABEL_KEY}", "--format", "{{.Name}}", check=False
    ).splitlines():
        network = line.strip()
        if not network or not network.startswith(NAME_PREFIX):
            continue
        if any(runid and runid in network for runid in live_runids):
            report["skipped"].append(f"{network}: network of a live run")
            continue
        docker("network", "rm", network, check=False)
        report["networks"].append(network)

    for line in docker(
        "images",
        "--filter",
        "reference=gramps-remote-mcp:e2e-*",
        "--format",
        "{{.Repository}}:{{.Tag}}",
        check=False,
    ).splitlines():
        image = line.strip()
        if not image:
            continue
        if any(runid and runid in image for runid in live_runids):
            report["skipped"].append(f"{image}: image of a live run")
            continue
        docker("image", "rm", "-f", image, check=False)
        report["images"].append(image)

    return report


def format_report(report: dict[str, list[str]]) -> str:
    lines = [
        f"reaper: {len(report['removed'])} container(s) removed, {len(report['skipped'])} skipped"
    ]
    for key in ("removed", "networks", "images", "skipped"):
        lines += [f"  {key}: {item}" for item in report[key]]
    return "\n".join(lines)


def main() -> int:  # pragma: no cover - thin CLI over reap()
    import argparse

    parser = argparse.ArgumentParser(description="Reap stale gwe2e-* resources (plan D18)")
    parser.add_argument("--e2e-force", action="store_true", dest="force")
    parser.add_argument("--grace-min", type=int, default=LIVE_RUN_GRACE_MIN)
    args = parser.parse_args()
    try:
        print(format_report(reap(force=args.force, grace_min=args.grace_min)))
    except ProbeError as exc:
        print(f"reaper failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
