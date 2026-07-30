"""D18: the reaper must never kill a live run. Pure logic, no Docker (T1.2).

`should_reap` is a function over what `docker inspect` reported precisely so this can be
tested without starting anything. The scenario that motivates D18 — two terminals, or a
deliberate `--keep` instance, plus a fresh run — is the first test below.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from harness.reaper import Candidate, parse_docker_time, should_reap

NOW = datetime(2026, 7, 30, 16, 0, tzinfo=UTC)


def candidate(**kwargs: object) -> Candidate:
    base = {"name": "gwe2e-abc123-web", "runid": "abc123", "running": True, "started_at": NOW}
    return Candidate(**{**base, **kwargs})  # type: ignore[arg-type]


def test_live_young_run_is_never_reaped() -> None:
    decision, reason = should_reap(candidate(started_at=NOW - timedelta(minutes=5)), now=NOW)
    assert decision is False
    assert "live run" in reason


def test_stopped_container_is_reaped() -> None:
    decision, reason = should_reap(candidate(running=False), now=NOW)
    assert decision is True
    assert reason == "stopped"


def test_running_past_the_grace_window_is_reaped() -> None:
    decision, reason = should_reap(candidate(started_at=NOW - timedelta(minutes=90)), now=NOW)
    assert decision is True
    assert "90 min old" in reason


def test_force_reaps_a_live_run() -> None:
    decision, reason = should_reap(
        candidate(started_at=NOW - timedelta(minutes=1)), now=NOW, force=True
    )
    assert decision is True
    assert "--e2e-force" in reason


def test_unreadable_start_time_counts_as_live() -> None:
    """Fail safe: an unparseable timestamp must not license a deletion."""
    decision, reason = should_reap(candidate(started_at=None), now=NOW)
    assert decision is False
    assert "unreadable" in reason


def test_int_containers_survive_even_force() -> None:
    for name in ("gramps-grampsweb-1", "grampsweb_celery", "grampsweb_redis"):
        decision, reason = should_reap(
            candidate(name=name, started_at=NOW - timedelta(days=2)), now=NOW, force=True
        )
        assert decision is False, f"{name} must never be reapable"
        assert "INT" in reason


def test_foreign_containers_are_left_alone() -> None:
    decision, reason = should_reap(candidate(name="some-other-project"), now=NOW, force=True)
    assert decision is False
    assert "not a gwe2e-" in reason


def test_docker_nanosecond_timestamps_parse() -> None:
    parsed = parse_docker_time("2026-07-30T11:21:02.370803374Z")
    assert parsed == datetime(2026, 7, 30, 11, 21, 2, 370803, tzinfo=UTC)


def test_zero_timestamp_of_a_never_started_container_is_none() -> None:
    assert parse_docker_time("0001-01-01T00:00:00Z") is None
    assert parse_docker_time("") is None
