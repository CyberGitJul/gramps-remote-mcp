import copy
import time
import unicodedata

import requests

from gramps_blog import BlogMixin

# Tag applied to tentative records; the exact contract that couples add_person
# (applies it) to confirm_person (removes it). Keep as a single source of truth.
UNCONFIRMED_TAG = "Unbestätigt"


class PersonNotFoundError(Exception):
    pass


class PersonCountMismatchError(Exception):
    pass


class FamilyNotFoundError(Exception):
    pass


class PersonCreateCountMismatchError(Exception):
    pass


class FamilyCreateCountMismatchError(Exception):
    pass


class ChildAlreadyInFamilyError(Exception):
    pass


class ChildNotInFamilyError(Exception):
    pass


class PersonDeleteCountMismatchError(Exception):
    pass


class FamilyNotEmptyError(Exception):
    pass


class FamilyDeleteCountMismatchError(Exception):
    pass


class ImportTimeoutError(Exception):
    pass


class DeleteAllCountMismatchError(Exception):
    pass


class DeleteAllTimeoutError(Exception):
    pass


class DeleteAllStateUnknownError(Exception):
    pass


EXPORT_TIMEOUT = 300
IMPORT_HTTP_TIMEOUT = 300
IMPORT_POLL_INTERVAL = 2.0
IMPORT_STABILITY_WINDOW = 2
IMPORT_MAX_TIMEOUT = 300
# How long an import that has not changed any count must hold steady before it counts as
# finished rather than not-yet-started. Only gates the no-growth path: once counts grow and
# settle, the import is done immediately.
IMPORT_MIN_SETTLE = 30

DELETE_ALL_POLL_INTERVAL = 2.0
DELETE_ALL_STABILITY_WINDOW = 2
DELETE_ALL_MAX_TIMEOUT = 300
# Long, like EXPORT_TIMEOUT/IMPORT_HTTP_TIMEOUT: a synchronous deployment answers only
# once the wipe (including a full search reindex) has finished.
DELETE_ALL_HTTP_TIMEOUT = 300


_DATE_MODIFIERS = {
    "exact": 0,
    "about": 3,
    "before": 1,
    "after": 2,
    "estimated": 0,
    "between": 4,
}


def _build_date(year, quality, year_to=None):
    """Build a Gramps Date dict from a year and a quality keyword.

    `quality` is one of "exact", "about", "before", "after", "estimated",
    "between" (or None, treated as "exact"). "between" requires `year_to`.
    """
    quality = quality or "exact"
    if quality not in _DATE_MODIFIERS:
        raise ValueError(f"Unknown birth_quality: {quality!r}")
    modifier = _DATE_MODIFIERS[quality]
    date_quality = 1 if quality == "estimated" else 0
    if quality == "between":
        if year_to is None:
            raise ValueError("birth_year_to is required when birth_quality='between'")
        dateval = [0, 0, year, False, 0, 0, year_to, False]
    else:
        dateval = [0, 0, year, False]
    return {
        "_class": "Date",
        "calendar": 0,
        "modifier": modifier,
        "quality": date_quality,
        "dateval": dateval,
        "text": "",
        "sortval": 0,
        "newyear": 0,
    }


def _assign_parent_handles(person_a, person_b):
    """Decide father_handle/mother_handle for a new Family.

    Whichever person has gender Female (0) takes mother_handle; the other
    takes father_handle. If genders don't disambiguate (equal, or person_b
    is None and person_a isn't female), falls back to call order:
    person_a -> father_handle, person_b -> mother_handle.
    """
    handle_a = person_a["handle"]
    if person_b is None:
        if person_a["gender"] == 0:
            return None, handle_a
        return handle_a, None
    handle_b = person_b["handle"]
    if person_a["gender"] == 0 and person_b["gender"] != 0:
        return handle_b, handle_a
    if person_b["gender"] == 0 and person_a["gender"] != 0:
        return handle_a, handle_b
    return handle_a, handle_b


def _name_strings(name):
    """Searchable strings from a Name dict: first, surname, 'first surname', nick.

    Used by search_person so a query can match a full name, a nickname, or an
    alternate/maiden name — not just a bare first- or surname substring.
    """
    if not name:
        return []
    first = name.get("first_name") or ""
    nick = name.get("nick") or ""
    surname_list = name.get("surname_list") or []
    surname = (surname_list[0].get("surname") or "") if surname_list else ""
    return [first, surname, f"{first} {surname}".strip(), nick]


def _gender_mutation(gender):
    """Build a person-mutation that sets gender. Shared by single + bulk writes."""

    def mutate(person):
        person["gender"] = gender

    return mutate


def _surname_mutation(surname, name_type=None):
    """Build a person-mutation that sets the primary surname (+ optional name type)."""

    def mutate(person):
        person["primary_name"]["surname_list"][0]["surname"] = surname
        if name_type is not None:
            person["primary_name"]["type"] = name_type

    return mutate


def _first_name_mutation(first_name):
    """Build a person-mutation that sets the primary given (first) name."""

    def mutate(person):
        person["primary_name"]["first_name"] = first_name

    return mutate


class GrampsClient(BlogMixin):
    def __init__(self, base_url, username, password, blog_body_format=None):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._access_token = None
        # Fail-safe (like GRAMPS_ENABLE_DESTRUCTIVE): only the exact string "html"
        # enables HTML bodies; anything else falls back to safe plain text.
        self.blog_body_format = "html" if blog_body_format == "html" else "text"

    def _login(self):
        resp = requests.post(
            f"{self.base_url}/api/token/",
            json={"username": self.username, "password": self.password},
            timeout=10,
        )
        resp.raise_for_status()
        self._access_token = resp.json()["access_token"]

    def _authed_request(self, method, path, *, timeout, extra_headers=None, **body_kwargs):
        """Send an authenticated request, retrying once through a fresh login on 401.

        Each call site passes its own timeout, body kwarg (json=/data=) and extra headers;
        nothing is inherited from another caller. The header dict is built ONCE and the
        retry replaces only the Authorization value, so caller-supplied headers survive
        the second attempt. That matters for the import POST: rebuilding the dict would
        drop its Content-Type, and only when a token expires mid-import.
        """
        if self._access_token is None:
            self._login()
        headers = {"Authorization": f"Bearer {self._access_token}"}
        if extra_headers:
            headers.update(extra_headers)
        url = f"{self.base_url}{path}"
        resp = requests.request(method, url, headers=headers, timeout=timeout, **body_kwargs)
        if resp.status_code == 401:
            self._login()
            headers["Authorization"] = f"Bearer {self._access_token}"
            resp = requests.request(method, url, headers=headers, timeout=timeout, **body_kwargs)
        resp.raise_for_status()
        return resp

    def _request(self, method, path, json_body=None):
        # json= is passed unconditionally (including None) — the existing suite pins that.
        resp = self._authed_request(method, path, timeout=10, json=json_body)
        return resp.json() if resp.content else None

    def _raw_get_bytes(self, path):
        """GET raw bytes (binary download); 401 relogin+retry handled by _authed_request."""
        return self._authed_request("GET", path, timeout=EXPORT_TIMEOUT).content

    def _raw_post_bytes(self, path, data):
        """POST a raw octet-stream body; returns (status_code, json-or-None). 201 and 202 both ok."""
        resp = self._authed_request(
            "POST",
            path,
            timeout=IMPORT_HTTP_TIMEOUT,
            extra_headers={"Content-Type": "application/octet-stream"},
            data=data,
        )
        return resp.status_code, (resp.json() if resp.content else None)

    def export_tree(self, extension="gramps"):
        """Download the whole tree as raw (gzip) bytes. GET /api/exporters/{ext}/file (synchronous)."""
        return self._raw_get_bytes(f"/api/exporters/{extension}/file")

    def _wait_for_counts(
        self,
        is_done,
        on_timeout,
        *,
        initial=None,
        poll_interval,
        stability_window,
        max_timeout,
        _sleep,
        _now,
    ):
        """Poll object_counts() until the counts hold still and `is_done` accepts them.

        Done means the same counts came back for `stability_window` consecutive polls AND
        `is_done(cur, elapsed)` is true, where `elapsed` is the seconds since this call
        started. `on_timeout(last)` gets the last counts seen (or `initial`, if the
        deadline was already past) and returns the exception to raise, so each caller
        keeps its own error type. Completion is never derived from /api/tasks/ — that
        endpoint is TTL-reaped and unreliable.
        """
        start = _now()
        deadline = start + max_timeout
        prev = None
        stable = 0
        last = initial
        while _now() < deadline:
            _sleep(poll_interval)
            cur = self.object_counts()
            last = cur
            if cur == prev:
                stable += 1
            else:
                stable = 0
                prev = cur
            if stable >= stability_window and is_done(cur, _now() - start):
                return cur
        raise on_timeout(last)

    def import_file(
        self,
        data,
        extension="gramps",
        *,
        poll_interval=IMPORT_POLL_INTERVAL,
        stability_window=IMPORT_STABILITY_WINDOW,
        max_timeout=IMPORT_MAX_TIMEOUT,
        min_settle=IMPORT_MIN_SETTLE,
        _sleep=time.sleep,
        _now=time.monotonic,
    ):
        """Import a file into the tree (additive). POST octet-stream, confirm via object_counts.

        Completion is detected from object_counts (GET /api/metadata/), never from
        /api/tasks/ (unreliable: TTL-reaped). Done once the counts are identical for
        `stability_window` consecutive confirmation polls AND either the total has grown
        beyond `before`, or `min_settle` seconds have passed. The second case is a real
        import that added nothing (a duplicate-free or empty file) — reporting it as a
        timeout would call a success a failure. The settle floor keeps an async import
        that has not started writing yet from being mistaken for one: unchanged counts
        alone are not enough until it has held that way for `min_settle` seconds.
        Works for sync (201) and async (202) alike.
        """
        before = self.object_counts()
        before_total = sum(before.values())
        self._raw_post_bytes(f"/api/importers/{extension}/file", data)

        after = self._wait_for_counts(
            lambda cur, elapsed: sum(cur.values()) > before_total or elapsed >= min_settle,
            lambda last: ImportTimeoutError(
                f"Import did not stabilize within {max_timeout}s; before={before}, last={last}"
            ),
            initial=before,
            poll_interval=poll_interval,
            stability_window=stability_window,
            max_timeout=max_timeout,
            _sleep=_sleep,
            _now=_now,
        )
        return {
            "before": before,
            "after": after,
            "added": {k: after.get(k, 0) - before.get(k, 0) for k in after},
        }

    def delete_all_objects(
        self,
        *,
        expected_count,
        poll_interval=DELETE_ALL_POLL_INTERVAL,
        stability_window=DELETE_ALL_STABILITY_WINDOW,
        max_timeout=DELETE_ALL_MAX_TIMEOUT,
        _sleep=time.sleep,
        _now=time.monotonic,
    ):
        """Delete every object in the tree. DESTRUCTIVE AND IRREVERSIBLE.

        `expected_count` must equal the tree's current total object count, so a caller
        has to have read the tree before destroying it; a mismatch — or a counts read
        that comes back empty, which would otherwise let expected_count=0 pass without
        the tree's real size ever being established — raises DeleteAllCountMismatchError
        before anything is written. That same error also covers "the counts could not be
        read at all" (an empty object_counts() result): retrying with a different
        expected_count will not help in that case, since there was no real count to
        compare against in the first place. The only HTTP call made before that check
        passes is the object_counts() read used for the comparison. One POST to
        /api/objects/delete/ (no ?namespaces= = every namespace) does the work — the
        server owns the deletion order, referential integrity, the default_person reset
        and the search reindex, and answers 200 (sync) or 202 (Celery) alike.

        A fresh login is forced first: the endpoint is a FreshProtectedResource and
        accepts only tokens minted by /api/token/, so a long-lived session must not
        present whatever token it happens to be holding. The POST carries a long
        timeout — a synchronous deployment answers only once the wipe, including a full
        search reindex, has finished. If it still times out on the read (the request was
        nonetheless delivered — a ConnectTimeout, meaning the connection never even
        opened, is NOT treated this way), or a reverse proxy in front of a synchronous
        deployment answers 502/503/504 (a gateway giving up before the server does — that
        status describes the gateway, not the tree), the error is swallowed and
        completion is read the same way it always is: object_counts() until the total is
        0 and stable, never from /api/tasks/. Any other failure of the POST itself (401,
        403, 500, ...) still fails fast.

        Returns {before, after, deleted}; raises DeleteAllTimeoutError at the deadline,
        carrying `before` and the last counts seen so a partial wipe is diagnosable. If
        the POST is delivered (or waved through by the fallthrough above) but something
        then goes wrong while confirming completion — a transient error from
        object_counts() itself — raises DeleteAllStateUnknownError instead, carrying
        `before`: in that one case the delete was accepted but this call cannot tell the
        caller what happened to their data, and gramps_get_object_counts must be
        re-read before doing anything else.
        """
        before = self.object_counts()
        if not before:
            raise DeleteAllCountMismatchError(
                "object_counts() returned no counts; refusing to wipe because the "
                "tree's size could not be established. Nothing was deleted."
            )
        before_total = sum(before.values())
        if expected_count != before_total:
            raise DeleteAllCountMismatchError(
                f"expected_count={expected_count} does not match the live total "
                f"{before_total}; nothing was deleted. Counts: {before}"
            )
        self._login()
        try:
            self._authed_request("POST", "/api/objects/delete/", timeout=DELETE_ALL_HTTP_TIMEOUT)
        except requests.ReadTimeout:
            # The delete was delivered; a slow synchronous deployment merely held the
            # connection open past the deadline (the wipe includes a full search
            # reindex). Completion in this server always comes from the counts, never
            # from the response, so a wipe that is already running must be confirmed
            # there rather than treated as abandoned.
            #
            # This is deliberately requests.ReadTimeout, not the broader requests.Timeout:
            # requests.ConnectTimeout also subclasses Timeout, but it means the
            # connection was never established in the first place, so the request never
            # landed — that case must fail fast like any other delivery failure, not be
            # reported as "accepted and running".
            pass
        except requests.HTTPError as exc:
            # A reverse proxy in front of a synchronous deployment can give up on the
            # request before the server does — nginx's default proxy_read_timeout is
            # 60s, well under DELETE_ALL_HTTP_TIMEOUT (300s) — while the server keeps
            # deleting behind it. 502/503/504 describe the gateway, not the tree, so
            # they are treated exactly like the read timeout above: fall through to the
            # counts poll, the only authority on what actually happened. Every other
            # status (401, 403, 500, ...) is about the request itself — bad auth,
            # missing permissions, a genuine server error — and must still fail fast, so
            # only these three statuses are singled out here.
            if exc.response is None or exc.response.status_code not in (502, 503, 504):
                raise
        try:
            after = self._wait_for_counts(
                lambda cur, _elapsed: sum(cur.values()) == 0,
                lambda last: DeleteAllTimeoutError(
                    f"Tree did not empty within {max_timeout}s; before={before}, last={last}. "
                    "The delete was accepted and may still be running server-side; do not "
                    "retry with a stale expected_count."
                ),
                initial=before,
                poll_interval=poll_interval,
                stability_window=stability_window,
                max_timeout=max_timeout,
                _sleep=_sleep,
                _now=_now,
            )
        except DeleteAllTimeoutError:
            # Not an unknown state: it already carries `before` and the last counts
            # seen, which is the more specific and more useful error. Pass it through
            # unchanged rather than wrapping it in DeleteAllStateUnknownError below.
            raise
        except Exception as exc:
            # Anything else here means the POST was accepted (or waved through above)
            # but this call could not confirm what happened next — a transient 5xx from
            # /api/metadata/, a connection reset mid-poll, etc. That is the one state in
            # which the caller genuinely cannot tell what happened to their data, so it
            # must not escape bare: wrap it with `before` and point at the one source of
            # truth this server trusts.
            raise DeleteAllStateUnknownError(
                f"The delete request was accepted, but the tree's state could not be "
                f"confirmed afterwards ({type(exc).__name__}: {exc}); before={before}. "
                "Call gramps_get_object_counts before doing anything else to find out "
                "what actually happened."
            ) from exc
        return {
            "before": before,
            "after": after,
            "deleted": {k: before[k] - after.get(k, 0) for k in before},
        }

    def get_person(self, gramps_id):
        # The live API 404s on an unknown gramps_id rather than returning an empty
        # list, so map that to PersonNotFoundError; keep the empty-list guard too in
        # case a deployment answers 200 []. Other HTTP errors propagate unchanged.
        try:
            people = self._request("GET", f"/api/people/?gramps_id={gramps_id}")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                raise PersonNotFoundError(gramps_id) from exc
            raise
        if not people:
            raise PersonNotFoundError(gramps_id)
        return people[0]

    def count_people(self):
        people = self._request("GET", "/api/people/?keys=gramps_id")
        return len(people)

    def count_families(self):
        families = self._request("GET", "/api/families/?keys=gramps_id")
        return len(families)

    def object_counts(self):
        """Return the tree's object counts (people, families, events, ...).

        Thin read-only wrapper over GET /api/metadata/ -> object_counts.
        """
        metadata = self._request("GET", "/api/metadata/")
        return metadata["object_counts"]

    def list_people(self, keys=None, page=None, pagesize=None):
        """List people, optionally selecting fields and paginating.

        Thin wrapper over GET /api/people/. `keys` selects returned fields
        (?keys=a,b,c); `page` is 1-based and `pagesize` caps rows per page
        (the server's only pagination mechanism — there is no offset/limit).
        Omit page and pagesize to return every person.
        """
        # gramps-web-api only paginates when page >= 1; a bare pagesize is ignored
        # and the full list comes back. Default page to 1 so pagesize actually caps.
        if page is None and pagesize is not None:
            page = 1
        params = []
        if keys:
            params.append("keys=" + ",".join(keys))
        if page is not None:
            params.append(f"page={page}")
        if pagesize is not None:
            params.append(f"pagesize={pagesize}")
        query = ("?" + "&".join(params)) if params else ""
        return self._request("GET", f"/api/people/{query}")

    def get_family(self, family_id):
        families = self._request("GET", f"/api/families/?gramps_id={family_id}")
        if not families:
            raise FamilyNotFoundError(family_id)
        return families[0]

    def _create_object(self, resource, obj_dict):
        trans = self._request("POST", f"/api/{resource}/", json_body=obj_dict)
        for item in trans:
            if item["type"] == "add" and item["_class"] == obj_dict["_class"]:
                return item["new"]
        raise ValueError(f"No 'add' transaction item found for {obj_dict['_class']}")

    def _put_person(self, handle, obj):
        return self._request("PUT", f"/api/people/{handle}", json_body=obj)

    def _snapshot(self, person):
        return copy.deepcopy(
            {
                "gender": person.get("gender"),
                "primary_name": person.get("primary_name"),
                "alternate_names": person.get("alternate_names"),
                "tag_list": person.get("tag_list"),
            }
        )

    def _write_person(self, gramps_id, mutate_fn):
        """Fetch, snapshot, mutate, and PUT one person; return before/after.

        No count guard here — the caller decides how to guard (per write, or once
        around a whole batch). Shared by _guarded_write and _bulk_write.
        """
        person = self.get_person(gramps_id)
        before = self._snapshot(person)
        mutate_fn(person)
        self._put_person(person["handle"], person)
        after = self._snapshot(person)
        return {"gramps_id": gramps_id, "before": before, "after": after}

    def _guarded_write(self, gramps_id, mutate_fn):
        count_before = self.count_people()
        result = self._write_person(gramps_id, mutate_fn)
        count_after = self.count_people()
        if count_after != count_before:
            raise PersonCountMismatchError(f"Person count changed: {count_before} -> {count_after}")
        return result

    def _bulk_write(self, items, mutation_for):
        """Apply a mutation to many people under a SINGLE count-guard.

        Best-effort / not atomic: a failure on one item is captured in `errors`
        and does not abort the rest. The count-guard wraps the whole batch and is
        REPORTED via `count_guard_ok` (not raised) — raising after partial writes
        would discard the per-person results the caller needs.
        `mutation_for(item)` returns the mutate_fn for that item.
        """
        count_before = self.count_people()
        results = []
        errors = []
        for item in items:
            try:
                gramps_id = item["gramps_id"]
                results.append(self._write_person(gramps_id, mutation_for(item)))
            except Exception as exc:
                # item.get (not the local gramps_id) so a malformed item missing the
                # "gramps_id" key is recorded as an error instead of aborting the batch.
                errors.append(
                    {"gramps_id": item.get("gramps_id"), "error": f"{type(exc).__name__}: {exc}"}
                )
        count_after = self.count_people()
        return {
            "count_before": count_before,
            "count_after": count_after,
            "count_guard_ok": count_after == count_before,
            "results": results,
            "errors": errors,
        }

    def set_gender(self, gramps_id, gender):
        return self._guarded_write(gramps_id, _gender_mutation(gender))

    def set_surname(self, gramps_id, surname, name_type=None):
        return self._guarded_write(gramps_id, _surname_mutation(surname, name_type))

    def set_first_name(self, gramps_id, first_name):
        return self._guarded_write(gramps_id, _first_name_mutation(first_name))

    def set_gender_bulk(self, items):
        return self._bulk_write(items, lambda item: _gender_mutation(item["gender"]))

    def set_surname_bulk(self, items):
        return self._bulk_write(
            items, lambda item: _surname_mutation(item["surname"], item.get("name_type"))
        )

    def add_alternate_name(self, gramps_id, surname, first_name=None, name_type="Birth Name"):
        def mutate(person):
            primary_name = person["primary_name"]
            primary_surname = primary_name.get("surname_list", [{}])[0]

            # Build fresh name record with content fields from primary_name
            # and metadata fields set to Gramps Web API defaults
            alt_name = {
                "call": "",
                "citation_list": [],
                "date": {
                    "calendar": 0,
                    "dateval": [0, 0, 0, False],
                    "modifier": 0,
                    "newyear": 0,
                    "quality": 0,
                    "sortval": 0,
                },
                "display_as": 0,
                "famnick": "",
                "first_name": first_name
                if first_name is not None
                else primary_name.get("first_name", ""),
                "group_as": "",
                "nick": "",
                "note_list": [],
                "private": False,
                "sort_as": 0,
                "suffix": "",
                "surname_list": [{**primary_surname, "surname": surname}],
                "title": "",
                "type": name_type,
            }
            person.setdefault("alternate_names", []).append(alt_name)

        return self._guarded_write(gramps_id, mutate)

    def add_birth_name(self, gramps_id, surname, first_name=None):
        # Backward-compatible alias: a Birth-Name alternate.
        return self.add_alternate_name(gramps_id, surname, first_name, name_type="Birth Name")

    def swap_primary_name(self, gramps_id, alt_index=0):
        """Swap the primary name with an alternate name (default the first).

        The displaced primary becomes that alternate. Non-destructive (PUT);
        returns before/after. Raises ValueError if there is no alternate name at
        alt_index (nothing is written).
        """

        def mutate(person):
            alts = person.get("alternate_names") or []
            if alt_index < 0 or alt_index >= len(alts):
                raise ValueError(f"no alternate name at index {alt_index} to swap")
            alts = list(alts)
            primary = person["primary_name"]
            person["primary_name"] = alts[alt_index]
            alts[alt_index] = primary
            person["alternate_names"] = alts

        return self._guarded_write(gramps_id, mutate)

    def confirm_person(self, gramps_id):
        tag_handle = self._find_tag_handle(UNCONFIRMED_TAG)

        def mutate(person):
            if tag_handle is not None and tag_handle in person.get("tag_list", []):
                person["tag_list"].remove(tag_handle)

        return self._guarded_write(gramps_id, mutate)

    def search_person(self, query, limit=None):
        query_lower = (query or "").strip().lower()
        if not query_lower:
            return []  # empty/whitespace query must not match the whole tree
        people = self._request(
            "GET", "/api/people/?keys=gramps_id,primary_name,gender,alternate_names"
        )
        matches = []
        for person in people:
            primary_name = person.get("primary_name") or {}
            haystacks = _name_strings(primary_name)
            for alt in person.get("alternate_names") or []:
                haystacks.extend(_name_strings(alt))
            if any(query_lower in h.lower() for h in haystacks if h):
                surname_list = primary_name.get("surname_list") or []
                matches.append(
                    {
                        "gramps_id": person["gramps_id"],
                        "first_name": primary_name.get("first_name") or "",
                        "surname": surname_list[0]["surname"] if surname_list else "",
                        "gender": person.get("gender"),
                    }
                )
        # "at most `limit`": None -> whole list; a non-positive cap yields none.
        # (Guard the negative case so a slice like matches[:-1] can't silently drop
        # the tail of the results.)
        if limit is not None and limit < 0:
            limit = 0
        return matches[:limit]

    def _find_tag_handle(self, name):
        # Compare on NFC-normalized forms so a tag created outside this code
        # (e.g. in the Gramps Web UI with a decomposed umlaut) still matches.
        target = unicodedata.normalize("NFC", name)
        tags = self._request("GET", "/api/tags/?keys=handle,name")
        for tag in tags:
            if unicodedata.normalize("NFC", tag["name"]) == target:
                return tag["handle"]
        return None

    def _get_or_create_tag(self, name):
        handle = self._find_tag_handle(name)
        if handle is not None:
            return handle
        tag_dict = {"_class": "Tag", "name": name, "color": "#000000000000", "priority": 0}
        new_tag = self._create_object("tags", tag_dict)
        return new_tag["handle"]

    def _create_birth_event(self, year, quality, year_to=None):
        event_dict = {
            "_class": "Event",
            "type": {"_class": "EventType", "value": 12, "string": ""},  # Birth
            "date": _build_date(year, quality, year_to),
        }
        new_event = self._create_object("events", event_dict)
        return new_event["handle"]

    def _create_note(self, text):
        note_dict = {
            "_class": "Note",
            "text": {"_class": "StyledText", "tags": [], "string": text},
        }
        new_note = self._create_object("notes", note_dict)
        return new_note["handle"]

    def add_person(
        self,
        first_name,
        surname,
        gender,
        birth_year=None,
        birth_quality=None,
        birth_year_to=None,
        note=None,
    ):
        # refuse to create a nameless person or an empty note
        if not (first_name or "").strip() and not (surname or "").strip():
            raise ValueError("add_person requires a non-empty first_name or surname")

        count_before = self.count_people()

        event_handle = None
        if birth_year is not None:
            event_handle = self._create_birth_event(birth_year, birth_quality, birth_year_to)

        note_handle = None
        if note and note.strip():
            note_handle = self._create_note(note)

        tag_handle = self._get_or_create_tag(UNCONFIRMED_TAG)

        person_dict = {
            "_class": "Person",
            "gender": gender,
            "primary_name": {
                "_class": "Name",
                "first_name": first_name,
                "surname_list": [{"_class": "Surname", "surname": surname, "primary": True}],
            },
            "tag_list": [tag_handle],
            "note_list": [note_handle] if note_handle else [],
            "event_ref_list": (
                [
                    {
                        "_class": "EventRef",
                        "ref": event_handle,
                        "role": {"_class": "EventRoleType", "value": 1, "string": ""},
                    }
                ]
                if event_handle
                else []
            ),
            "birth_ref_index": 0 if event_handle else -1,
        }
        new_person = self._create_object("people", person_dict)

        count_after = self.count_people()
        if count_after != count_before + 1:
            raise PersonCreateCountMismatchError(
                f"Person count changed unexpectedly: {count_before} -> {count_after}"
            )
        return new_person["gramps_id"]

    def add_family(self, spouse_a_id, spouse_b_id=None):
        count_before = self.count_families()

        person_a = self.get_person(spouse_a_id)
        person_b = self.get_person(spouse_b_id) if spouse_b_id is not None else None
        father_handle, mother_handle = _assign_parent_handles(person_a, person_b)

        family_dict = {
            "_class": "Family",
            "father_handle": father_handle,
            "mother_handle": mother_handle,
        }
        new_family = self._create_object("families", family_dict)

        count_after = self.count_families()
        if count_after != count_before + 1:
            raise FamilyCreateCountMismatchError(
                f"Family count changed unexpectedly: {count_before} -> {count_after}"
            )
        return new_family["gramps_id"]

    def _put_family(self, handle, obj):
        return self._request("PUT", f"/api/families/{handle}", json_body=obj)

    def add_child_to_family(self, family_id, child_id):
        family = self.get_family(family_id)
        child = self.get_person(child_id)

        existing_refs = [ref["ref"] for ref in family.get("child_ref_list", [])]
        if child["handle"] in existing_refs:
            raise ChildAlreadyInFamilyError(family_id, child_id)

        child_ref = {
            "_class": "ChildRef",
            "ref": child["handle"],
            "private": False,
            "citation_list": [],
            "note_list": [],
            "frel": {"_class": "ChildRefType", "value": 1, "string": ""},
            "mrel": {"_class": "ChildRefType", "value": 1, "string": ""},
        }
        family.setdefault("child_ref_list", []).append(child_ref)
        self._put_family(family["handle"], family)
        return {"family_id": family_id, "child_id": child_id}

    def set_family_parent(self, family_id, gramps_id, role):
        """Set the father or mother slot of an EXISTING family to a person.

        `role` is an explicit bloodline slot ("father" or "mother"), NOT derived
        from gender: a person of any sex may occupy either slot, so — unlike
        add_family — this never reorders by sex. Overwrites the slot if already
        filled and reports the displaced parent as `previous` (a person summary,
        or None if the slot was empty). Refuses to put the same person into both
        parent slots. Non-destructive (PUTs the family); the API maintains the
        person's reverse family_list reference.
        """
        slots = {"father": "father_handle", "mother": "mother_handle"}
        slot = slots.get(role)
        if slot is None:
            raise ValueError(f"role must be 'father' or 'mother', got {role!r}")
        other_slot = "mother_handle" if slot == "father_handle" else "father_handle"
        family = self.get_family(family_id)
        person = self.get_person(gramps_id)
        if person["handle"] == family.get(other_slot):
            raise ValueError(
                f"person {gramps_id} already occupies the other parent slot; a "
                "family cannot have the same person as both father and mother"
            )
        previous = self._summary_for_handle(family.get(slot))
        family[slot] = person["handle"]
        self._put_family(family["handle"], family)
        return {
            "family_id": family_id,
            "gramps_id": gramps_id,
            "role": role,
            "previous": previous,
        }

    def remove_child_from_family(self, family_id, child_id):
        """Remove a child from an existing family (inverse of add_child_to_family).

        Refuses with ChildNotInFamilyError if the person is not a child of the
        family. Non-destructive (PUTs the family); the API maintains the child's
        reverse parent_family_list reference.
        """
        family = self.get_family(family_id)
        child = self.get_person(child_id)
        child_ref_list = family.get("child_ref_list", [])
        remaining = [ref for ref in child_ref_list if ref["ref"] != child["handle"]]
        if len(remaining) == len(child_ref_list):
            raise ChildNotInFamilyError(family_id, child_id)
        family["child_ref_list"] = remaining
        self._put_family(family["handle"], family)
        return {"family_id": family_id, "child_id": child_id}

    def delete_person(self, gramps_id, confirm=False):
        """Delete a person. DESTRUCTIVE — requires confirm=True.

        Intended for removing duplicates / erroneous entries. Guards the tree
        size: expects the people count to drop by exactly one and raises
        PersonDeleteCountMismatchError otherwise (e.g. an unexpected cascade).
        `confirm` must be the literal True, so a stray truthy value cannot delete.

        The Gramps API does not cascade-delete the person's own notes, so any note
        attached ONLY to this person would otherwise be left orphaned (inflating
        the notes count). After deleting, this cleans those up: each formerly
        attached note is removed iff it is now unreferenced; notes still shared
        with other objects are left intact. Deleted note handles are reported as
        `deleted_notes` (best-effort — a cleanup failure never fails the delete).
        """
        if confirm is not True:
            raise ValueError("delete_person requires confirm=True (destructive)")
        person = self.get_person(gramps_id)
        note_handles = person.get("note_list") or []
        count_before = self.count_people()
        self._request("DELETE", f"/api/people/{person['handle']}")
        count_after = self.count_people()
        if count_after != count_before - 1:
            raise PersonDeleteCountMismatchError(
                f"Person count did not drop by one: {count_before} -> {count_after}"
            )
        deleted_notes = self._delete_orphaned_notes(note_handles)
        return {
            "gramps_id": gramps_id,
            "deleted": True,
            "count_before": count_before,
            "count_after": count_after,
            "deleted_notes": deleted_notes,
        }

    def _delete_orphaned_notes(self, note_handles):
        """Delete each note (by handle) that no object references any more.

        Called after removing an owner (e.g. a person): a note that was attached
        only to that owner is now orphaned and would otherwise clutter the tree and
        inflate the notes count. A note is orphaned iff its `backlinks` are empty;
        notes still referenced elsewhere (shared) are left untouched. Best-effort
        and idempotent — a per-note failure is skipped, never raised, because the
        owner is already gone by the time this runs. Returns the deleted handles.
        """
        deleted = []
        for handle in note_handles:
            try:
                note = self._request("GET", f"/api/notes/{handle}?backlinks=1")
                if not note.get("backlinks"):
                    self._request("DELETE", f"/api/notes/{handle}")
                    deleted.append(handle)
            except requests.HTTPError:
                continue
        return deleted

    def delete_family(self, family_id, confirm=False):
        """Delete a family. DESTRUCTIVE — requires confirm=True.

        Intended for cleaning up an orphaned/childless family left behind after
        re-homing its children (e.g. remove_child_from_family emptied a partnerless
        family). Refuses with FamilyNotEmptyError if the family still has children,
        so no child is silently orphaned — remove them first. Guards the tree size:
        expects the family count to drop by exactly one and raises
        FamilyDeleteCountMismatchError otherwise. `confirm` must be the literal
        True, so a stray truthy value cannot delete. The API maintains the parents'
        reverse family_list references.
        """
        if confirm is not True:
            raise ValueError("delete_family requires confirm=True (destructive)")
        family = self.get_family(family_id)
        if family.get("child_ref_list"):
            raise FamilyNotEmptyError(family_id)
        count_before = self.count_families()
        self._request("DELETE", f"/api/families/{family['handle']}")
        count_after = self.count_families()
        if count_after != count_before - 1:
            raise FamilyDeleteCountMismatchError(
                f"Family count did not drop by one: {count_before} -> {count_after}"
            )
        return {
            "family_id": family_id,
            "deleted": True,
            "count_before": count_before,
            "count_after": count_after,
        }

    def _get_person_by_handle(self, handle):
        return self._request("GET", f"/api/people/{handle}")

    def _get_family_by_handle(self, handle):
        return self._request("GET", f"/api/families/{handle}")

    def _person_summary(self, person):
        """Flat identity summary of a person: gramps_id/first_name/surname/gender.

        `gender` is always carried explicitly so callers never have to infer sex
        from a family role slot (father_handle/mother_handle are bloodline, not sex).
        """
        primary = person.get("primary_name") or {}
        surname_list = primary.get("surname_list") or []
        return {
            "gramps_id": person["gramps_id"],
            "first_name": primary.get("first_name") or "",
            "surname": surname_list[0]["surname"] if surname_list else "",
            "gender": person.get("gender"),
        }

    def _relative_node(self, person, rel_key, relatives):
        """Build a person summary node with a list of relatives under `rel_key`.

        Shared shape for the descendants (`children`) and ancestors (`parents`)
        trees: the person summary plus the relatives list.
        """
        return {**self._person_summary(person), rel_key: relatives}

    def _descendant_node(self, person, children):
        return self._relative_node(person, "children", children)

    def _build_descendant_node(self, person, remaining_depth):
        children = []
        if remaining_depth > 0:
            for fam_handle in person.get("family_list", []):
                family = self._get_family_by_handle(fam_handle)
                for child_ref in family.get("child_ref_list", []):
                    child = self._get_person_by_handle(child_ref["ref"])
                    children.append(self._build_descendant_node(child, remaining_depth - 1))
        return self._descendant_node(person, children)

    def get_descendants(self, gramps_id, grade=1):
        if grade < 1:
            raise ValueError("grade must be >= 1")
        root = self.get_person(gramps_id)
        return self._build_descendant_node(root, grade)

    def _ancestor_node(self, person, parents):
        return self._relative_node(person, "parents", parents)

    def _build_ancestor_node(self, person, remaining_depth):
        # father_handle/mother_handle are bloodline slots, not gender: each parent
        # node reports its own `gender`; slots are never relabelled by gender here.
        parents = []
        if remaining_depth > 0:
            for fam_handle in person.get("parent_family_list", []):
                family = self._get_family_by_handle(fam_handle)
                for parent_handle in (family.get("father_handle"), family.get("mother_handle")):
                    if parent_handle:
                        parent = self._get_person_by_handle(parent_handle)
                        parents.append(self._build_ancestor_node(parent, remaining_depth - 1))
        return self._ancestor_node(person, parents)

    def get_ancestors(self, gramps_id, grade=1):
        if grade < 1:
            raise ValueError("grade must be >= 1")
        root = self.get_person(gramps_id)
        return self._build_ancestor_node(root, grade)

    def _summary_for_handle(self, handle):
        """Fetch a person by handle and return their flat summary, or None."""
        if not handle:
            return None
        return self._person_summary(self._get_person_by_handle(handle))

    def get_relations(self, gramps_id):
        """Return a person's family context: parent families and own families.

        Shape: the person summary plus
          - `parent_families`: families in which the person is a child, each with
            `father`/`mother` (the family's slots, or None) and `family_gramps_id`.
          - `families`: families in which the person is a spouse/parent, each with
            `partner` (the other slot, or None) and `children`.
        father/mother are bloodline slots, not gender: every person is a summary
        carrying its own `gender`, so callers must not read sex from a slot.
        """
        root = self.get_person(gramps_id)
        root_handle = root["handle"]

        parent_families = []
        for fam_handle in root.get("parent_family_list", []):
            family = self._get_family_by_handle(fam_handle)
            parent_families.append(
                {
                    "family_gramps_id": family.get("gramps_id"),
                    "father": self._summary_for_handle(family.get("father_handle")),
                    "mother": self._summary_for_handle(family.get("mother_handle")),
                }
            )

        families = []
        for fam_handle in root.get("family_list", []):
            family = self._get_family_by_handle(fam_handle)
            partner_handle = next(
                (
                    h
                    for h in (family.get("father_handle"), family.get("mother_handle"))
                    if h and h != root_handle
                ),
                None,
            )
            partner = self._summary_for_handle(partner_handle)
            children = [
                self._summary_for_handle(ref["ref"]) for ref in family.get("child_ref_list", [])
            ]
            families.append(
                {
                    "family_gramps_id": family.get("gramps_id"),
                    "partner": partner,
                    "children": children,
                }
            )

        return {
            **self._person_summary(root),
            "parent_families": parent_families,
            "families": families,
        }
