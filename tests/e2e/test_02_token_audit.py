"""The token-audit parser (D15) — pure, no Docker (T1.4).

The 429 regression test asserts a *count*, not a pass/fail: reverting only the mint guard
leaves the retry in place, so the wipe still succeeds with three token POSTs instead of one.
That makes this parser load-bearing, and a parser that quietly returns 0 is exactly how a
count assertion turns vacuous.
"""

from __future__ import annotations

from harness.token_audit import TokenAudit, parse_access_line

HOST_IP, CONTAINER_IP = "172.24.0.1", "172.24.0.4"

LOG = [
    "ACCESS 172.24.0.1 POST /api/token/ HTTP/1.1 200",
    "ACCESS 172.24.0.4 POST /api/token/ HTTP/1.1 200",
    "ACCESS 172.24.0.4 POST /api/token/ HTTP/1.1 429",
    "ACCESS 172.24.0.4 GET /api/metadata/ HTTP/1.1 200",
    "[2026-07-30 12:00:00 +0000] [7] [INFO] Booting worker with pid: 7",
]


def audit(lines: list[str] | None = None) -> TokenAudit:
    payload = LOG if lines is None else lines
    return TokenAudit(lambda: payload)


def test_only_token_posts_are_counted() -> None:
    assert audit().count() == 3


def test_counts_split_by_ip_and_status() -> None:
    parsed = audit()
    assert parsed.count(ip=HOST_IP) == 1
    assert parsed.count(ip=CONTAINER_IP) == 2
    assert parsed.count(ip=CONTAINER_IP, status=429) == 1
    assert parsed.by_ip() == {HOST_IP: 1, CONTAINER_IP: 2}


def test_mark_and_since_report_only_what_came_after() -> None:
    lines = list(LOG)
    parsed = TokenAudit(lambda: lines)
    mark = parsed.mark()
    lines.append("ACCESS 172.24.0.9 POST /api/token/ HTTP/1.1 200")

    fresh = parsed.since(mark)
    assert [post.ip for post in fresh] == ["172.24.0.9"]
    assert parsed.count(mark=mark) == 1


def test_noise_never_raises() -> None:
    """The log is a stream we do not control; a crash there would read as a test failure."""
    noise = ["", "ACCESS", "ACCESS 1.2.3.4 POST /api/token/ HTTP/1.1 not-a-number", "garbage"]
    assert audit(noise).count() == 0
    assert all(parse_access_line(line) is None for line in noise)


def test_an_empty_log_is_zero_not_an_error() -> None:
    """This is the failure mode the positive control exists for: silently zero."""
    assert audit([]).count() == 0


def test_positive_control_fails_when_the_log_stays_empty() -> None:
    parsed = audit([])
    control = parsed.positive_control(lambda: None)
    assert control["passed"] is False


def test_positive_control_passes_when_the_mint_shows_up() -> None:
    lines: list[str] = []
    parsed = TokenAudit(lambda: lines)

    def mint() -> None:
        lines.append(f"ACCESS {HOST_IP} POST /api/token/ HTTP/1.1 200")

    control = parsed.positive_control(mint)
    assert control["passed"] is True
    assert control["ips"] == [HOST_IP]
