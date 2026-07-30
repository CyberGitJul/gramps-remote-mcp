"""REST oracle — the suite's only way to observe tree state (plan D3).

`min_login_gap` (D22) exists because the disposable instance runs a *sharp* 1/second
limiter on `/api/token/`: the harness must never spend the token budget belonging to the
code under test. The token is minted, never refreshed — `POST /api/objects/delete/` is a
`FreshProtectedResource`, and a refreshed token is not `fresh`.
"""

from __future__ import annotations

import time
from typing import Any

import requests

MIN_LOGIN_GAP_S = 1.1
TOKEN_MAX_AGE_S = 13 * 60

# The 1/second limit is keyed by client IP, so the gap has to hold across *every* client in
# this process, not per instance. Measured the hard way: two freshly constructed clients each
# had an empty per-object history, minted inside the same second, and the second one got a 429.
_LAST_LOGIN_AT: dict[str, float] = {}


class GrampsRest:
    """Authenticated REST access to one Gramps Web instance."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.tokens_minted = 0
        self._token: str | None = None
        self._minted_at = 0.0

    def invalidate(self) -> None:
        """Drop the cached token — after a secret-key rotation it no longer verifies."""
        self._token = None

    def token(self, *, force: bool = False) -> str:
        if self._token and not force and time.monotonic() - self._minted_at < TOKEN_MAX_AGE_S:
            return self._token
        last_login = _LAST_LOGIN_AT.get(self.base_url, 0.0)
        gap = MIN_LOGIN_GAP_S - (time.monotonic() - last_login)
        if last_login and gap > 0:
            time.sleep(gap)
        reply = self.session.post(
            f"{self.base_url}/api/token/",
            json={"username": self.username, "password": self.password},
            timeout=30,
        )
        reply.raise_for_status()
        self._token = reply.json()["access_token"]
        self._minted_at = time.monotonic()
        _LAST_LOGIN_AT[self.base_url] = self._minted_at
        self.tokens_minted += 1
        return self._token

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        headers = {"Authorization": f"Bearer {self.token()}"}
        headers.update(kwargs.pop("headers", {}))
        return self.session.request(
            method, f"{self.base_url}{path}", headers=headers, timeout=60, **kwargs
        )

    def get_json(self, path: str) -> Any:
        reply = self.request("GET", path)
        reply.raise_for_status()
        return reply.json()

    def object_counts(self) -> dict[str, int]:
        return self.get_json("/api/metadata/")["object_counts"]

    def total(self) -> int:
        return sum(self.object_counts().values())
