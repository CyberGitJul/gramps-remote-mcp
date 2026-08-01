"""REST oracle — the suite's only way to observe tree state (plan D3).

`min_login_gap` (D22) exists because the disposable instance runs a *sharp* 1/second
limiter on `/api/token/`: the harness must never spend the token budget belonging to the
code under test. The token is minted, never refreshed — `POST /api/objects/delete/` is a
`FreshProtectedResource`, and a refreshed token is not `fresh`.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import requests

from harness import records

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

    def request(
        self, method: str, path: str, *, retry_401: bool = True, **kwargs: Any
    ) -> requests.Response:
        """One request. A 401 buys exactly one re-mint and retry (D22).

        Only 401 — a signature failure answers **422** (measured, `u7_relogin_trigger`), and
        re-minting on that would paper over a rotated server key instead of surfacing it.
        """
        headers = {"Authorization": f"Bearer {self.token()}"}
        headers.update(kwargs.pop("headers", {}))
        reply = self.session.request(
            method, f"{self.base_url}{path}", headers=headers, timeout=60, **kwargs
        )
        if reply.status_code == 401 and retry_401:
            self.invalidate()
            return self.request(
                method, path, retry_401=False, headers=kwargs.pop("headers", {}), **kwargs
            )
        return reply

    def get_json(self, path: str) -> Any:
        reply = self.request("GET", path)
        reply.raise_for_status()
        return reply.json()

    def object_counts(self) -> dict[str, int]:
        return self.get_json("/api/metadata/")["object_counts"]

    def total(self) -> int:
        return sum(self.object_counts().values())

    # -- the object vocabulary (D3) --------------------------------------------

    def objects(self, namespace: str, query: str = "") -> list[dict[str, Any]]:
        """Every object of one namespace. Unpaginated on purpose: the fixture is small and
        `count_*` in the product is `len(list)`, so the oracle has to see the same thing."""
        return self.get_json(f"/api/{namespace}/{query}")

    def people(self) -> list[dict[str, Any]]:
        return self.objects("people")

    def families(self) -> list[dict[str, Any]]:
        return self.objects("families")

    def sources(self) -> list[dict[str, Any]]:
        return self.objects("sources")

    def notes(self) -> list[dict[str, Any]]:
        return self.objects("notes")

    def tags(self) -> list[dict[str, Any]]:
        return self.objects("tags")

    def by_gramps_id(self, namespace: str, gramps_id: str) -> dict[str, Any]:
        for record in self.objects(namespace):
            if record.get("gramps_id") == gramps_id:
                return self.get_json(f"/api/{namespace}/{record['handle']}")
        raise LookupError(f"no {namespace[:-1]} with gramps_id {gramps_id!r}")

    def person(self, gramps_id: str) -> dict[str, Any]:
        return self.by_gramps_id("people", gramps_id)

    def put_merge(self, namespace: str, handle: str, changes: dict[str, Any]) -> requests.Response:
        """Read, modify, write **the whole record** — the only PUT this suite may perform.

        Measured (`u5_put_semantics`): a partial body answers 200 and *blanks every omitted
        field* — `tag_list` 1 → 0, the surname gone. A trailing slash answers 405, so the path
        deliberately has none.
        """
        record = self.get_json(f"/api/{namespace}/{handle}")
        record.update(changes)
        return self.request("PUT", f"/api/{namespace}/{handle}", json=record)

    # -- state observation ------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Counts plus the identity of every object, which is what assertions compare."""
        counts = self.object_counts()
        objects = {
            namespace: sorted(
                (record.get("gramps_id", ""), record.get("handle", ""))
                for record in self.objects(namespace)
            )
            for namespace in sorted(counts)
        }
        return {"counts": counts, "total": sum(counts.values()), "objects": objects}

    def fingerprint(self, snapshot: dict[str, Any] | None = None) -> str:
        """A stable digest of *identity*, not of content or timestamps.

        Deliberately excludes `change`: a fingerprint that moves on every touch cannot answer
        "did this call add or remove anything", which is the question the guards ask.
        """
        state = snapshot or self.snapshot()
        material = json.dumps(state["objects"], sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(material.encode()).hexdigest()[:16]

    def content_snapshot(self) -> dict[str, Any]:
        """Counts of every namespace, plus a content signature per record in the signed ones.

        Six reads: one `/api/metadata/` and one listing per signed namespace. The tag map comes
        out of the listing it already fetched rather than a seventh call.
        """
        counts = self.object_counts()
        fetched = {namespace: self.objects(namespace) for namespace in records.SIGNED}
        tags = {str(tag.get("handle", "")): str(tag.get("name", "")) for tag in fetched["tags"]}
        signed = {
            namespace: sorted(
                (
                    (
                        str(record.get("gramps_id", "")),
                        records.content_signature(namespace, record, tags),
                    )
                    for record in fetched[namespace]
                ),
                key=lambda pair: pair[0],
            )
            for namespace in records.SIGNED
        }
        return {"counts": counts, "signed": signed}

    def content_fingerprint(self, snapshot: dict[str, Any] | None = None) -> str:
        """A digest of *content*, which is the question `fingerprint` deliberately does not ask.

        `fingerprint` digests `(gramps_id, handle)` pairs, so it is byte-identical after
        `set_surname`, `set_gender`, `confirm_person` or `update_blog_post` — every field write
        this server has. That is right for "did this call add or remove anything" and useless
        for "is this still the tree we seeded", which is what a class-scoped reseed has to
        decide. The two are separate rather than merged because a reset that fired on every
        field write would be a reset that fires constantly, and one that fires on none is the
        one that hands the next class somebody else's tree.
        """
        state = snapshot or self.content_snapshot()
        material = json.dumps(state, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(material.encode()).hexdigest()[:16]
