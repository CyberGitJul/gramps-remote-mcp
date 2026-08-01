"""Counts `POST /api/token/` per client IP, from the instance's access log (plan D15).

This exists because the 429 regression test cannot be a pass/fail check. Reverting only the
mint guard leaves the retry in place, so the wipe still succeeds — with three token POSTs
instead of one. Only the *count* catches that.

Two things make the counter honest rather than decorative:

* the log has to exist at all — the image's default CMD has no `--access-logfile`, so
  `docker logs | grep` silently returns 0 (`GrampsInstance` overrides the CMD for exactly this);
* the parser has to be proven to find a request it should find. `positive_control()` is that
  proof, and it is meant to be treated as session-fatal: without it, every `count(ip) == 0`
  assertion is `0 == 0` and stays green with the fix reverted, with the guard deleted, with
  anything.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from .docker_util import ProbeError

TOKEN_PATH = "/api/token/"


@dataclass(frozen=True)
class Mark:
    """Where a count starts, and the line that proves it is still the same log."""

    index: int
    witness: str | None


@dataclass(frozen=True)
class TokenPost:
    """One `POST /api/token/` as the access log recorded it."""

    ip: str
    status: int
    raw: str


def parse_access_line(line: str) -> TokenPost | None:
    """Parse one `ACCESS <ip> <method> <path> <proto> <status>` line, or return None.

    Anything that is not a token POST — a different route, a truncated line, a stray log
    message — is None rather than an exception: the log is a stream we do not control, and a
    crash there would be indistinguishable from a genuine test failure.
    """
    parts = line.split()
    if len(parts) != 6 or parts[0] != "ACCESS":
        return None
    _, ip, method, path, _proto, status = parts
    if method != "POST" or path != TOKEN_PATH:
        return None
    try:
        return TokenPost(ip=ip, status=int(status), raw=line)
    except ValueError:
        return None


class TokenAudit:
    """A view over the access log, refreshed on demand.

    Takes a callable rather than a container name so the same class serves a live instance, a
    captured log from an artifact directory, and the parser's own tests.
    """

    def __init__(self, read_log: Callable[[], list[str]]) -> None:
        self._read_log = read_log
        self.posts: list[TokenPost] = []
        self.refresh()

    def refresh(self) -> list[TokenPost]:
        self.posts = [post for post in map(parse_access_line, self._read_log()) if post]
        return self.posts

    def mark(self) -> Mark:
        """A cursor into the log, so a later `since()` reports only what a call caused."""
        self.refresh()
        return Mark(len(self.posts), self.posts[-1].raw if self.posts else None)

    def _after(self, mark: Mark | None) -> list[TokenPost]:
        """Everything past the cursor — or a refusal, if the cursor no longer means anything.

        `restart_web()` removes the web container and starts a new one, and `docker logs` for a
        new container starts empty. A plain index into that stream survives the restart looking
        perfectly valid and silently addresses a different log: `posts[mark:]` on a shorter list
        is `[]`, and `[]` reads as "nothing happened" — which is the answer a 429 regression
        test would most like to hear and least deserve. So the cursor carries the line it was
        taken at, and a cursor that no longer finds it refuses instead of counting to zero.
        """
        if mark is None:
            return self.posts
        if len(self.posts) < mark.index or (
            mark.witness is not None
            and (mark.index == 0 or self.posts[mark.index - 1].raw != mark.witness)
        ):
            raise ProbeError(
                "the access log no longer contains the line this cursor was taken at — "
                "the web container was replaced, so a count against it would be meaningless"
            )
        return self.posts[mark.index :]

    def since(self, mark: Mark) -> list[TokenPost]:
        self.refresh()
        return self._after(mark)

    def count(
        self, ip: str | None = None, status: int | None = None, *, mark: Mark | None = None
    ) -> int:
        return sum(
            1
            for post in self._after(mark)
            if (ip is None or post.ip == ip) and (status is None or post.status == status)
        )

    def by_ip(self, *, mark: Mark | None = None) -> dict[str, int]:
        return dict(Counter(post.ip for post in self._after(mark)))

    def positive_control(self, mint: Callable[[], object]) -> dict[str, object]:
        """Prove the parser sees a known-good request: mint one token, then find it.

        Returns the evidence rather than asserting, so the caller decides how fatal it is —
        but a caller that ignores `passed` has re-created the vacuity this guards against.
        """
        mark = self.mark()
        mint()
        found = self.since(mark)
        return {
            "passed": any(post.status == 200 for post in found),
            "found": [post.raw for post in found],
            "ips": sorted({post.ip for post in found}),
        }
