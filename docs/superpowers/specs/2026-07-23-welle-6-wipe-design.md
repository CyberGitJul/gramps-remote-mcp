# Welle 6 — Wipe (`delete_all_objects`) — Design

> Status: **approved for planning** (design presented 2026-07-23, approved by the owner
> 2026-07-28; adversarially reviewed the same day — 39 findings raised, 4 survived refutation,
> all four about the retry consolidation, all four folded in). Next step: writing-plans.
> Scope: one destructive MCP tool that empties a Gramps tree completely, plus the shared
> polling helper it extracts from `import_file`.

## 1. Motivation

Gramps imports are **additive** — they never merge. Welle 5 delivered the two file-transport
halves of a clean reset (`export_tree` / `import_file`), but the middle step is still missing:
there is no way to empty the tree first. Today "reset to exactly one source file" means
deleting objects one at a time through `gramps_delete_person` / `gramps_delete_family`, which
is O(tree) round-trips, cannot reach most object types at all (events, places, sources,
citations, repositories, media, tags), and leaves referential debris.

Welle 6 closes the loop: **export → wipe → import** becomes three tool calls.

This is the single most destructive operation the server will ever expose. The design is
therefore built around one question: *what stops an LLM from calling it by accident?*

## 2. Decisions (resolved)

| # | Decision | Resolution |
|---|----------|------------|
| Scope | How much does it delete | **Everything.** All object types including tags → an empty tree. No namespace or partial wipe. A half-wipe is a worse outcome than either extreme: it leaves a tree nobody can reason about. |
| Gate | New env flag or existing one | **Existing `GRAMPS_ENABLE_DESTRUCTIVE`.** No separate flag. Tool count: **off stays 27, on goes 30 → 31.** |
| Confirmation | What the caller must supply | **`confirm=True` AND `expected_count`, both mandatory.** `expected_count` must equal the live object total or nothing is deleted. This forces a read-before-write: the caller has to have counted the tree it is about to destroy. |
| Completion | How to know the wipe finished | **Counts-based, polled until total is 0 and stable.** Never `/api/tasks/` (TTL-reaped — same reasoning as Welle 5). |
| Code reuse | Duplicate the polling loop? | **No — extract `_wait_for_counts(is_done, …)`** from `import_file` and use it for both. |
| Retry consolidation | Merge the three 401→relogin blocks? | **Yes, but as its own PR, tests first.** The three are *not* interchangeable (§4.1a) and the path has **zero test coverage today**, so it does not ride along inside the wipe PR. |

### 2.1 The gate is no longer a safety net (changed 2026-07-28)

Until now the design leaned on `GRAMPS_ENABLE_DESTRUCTIVE` being off in production. That is
**no longer true**: the owner confirmed on 2026-07-28 that PROD deliberately runs with
`GRAMPS_ENABLE_DESTRUCTIVE=1`, so the `delete_*` tools are live there by choice.

Consequence for this wave: **the wipe is armed in production the moment it is registered.**
All safety must come from `confirm=True` + `expected_count`, not from the flag. The flag now
only decides whether the tool is *visible*, not whether it is *safe to be visible*.

## 3. API facts (verified against gramps-web-api, branch `master`)

- **Endpoint**: `POST /api/objects/delete/`. Without a `?namespaces=` query parameter it deletes
  **all** namespaces. **One call** — the server does deletion ordering, referential integrity,
  the `default_person` reset and the **search reindex** internally.
  `_FAST_DELETE_ORDER` is Tag → Note → Media → Citation → Repository → Source → Event → Place →
  Family → Person; the client must not attempt to replicate or second-guess this.
- **Sync vs async**: same split as Welle 5 — synchronous deployment returns `200` with a dict,
  Celery-backed returns `202` with `{"task": {...}}`. The client tolerates both and derives
  completion from counts either way.
- **Permission**: `PERM_DEL_OBJ_BATCH` → **OWNER (role 4)**. Stricter than single-object delete
  (EDITOR); same level as import. The automation user is already OWNER for Welle 5.
- **⚠️ Fresh-JWT requirement**: the resource is a `FreshProtectedResource` — it accepts only
  tokens minted by `/api/token/` (login), **not** refresh-derived ones. Our client is
  login-only, so it already qualifies, but that is incidental rather than guaranteed. The design
  therefore **forces `_login()` immediately before the delete POST**, so a long-lived session
  cannot present a stale token to the one endpoint that requires a fresh one.
- **Sources**: `resources/objects.py` → `DeleteObjectsResource`; `resources/delete.py` →
  `delete_all_objects` / `_FAST_DELETE_ORDER`; `auth/const.py` → `PERM_DEL_OBJ_BATCH`;
  `resources/__init__.py` → `FreshProtectedResource`. Endpoint introduced in upstream PR #499.

## 4. Architecture

### 4.1 `gramps_client.py` — extract the polling helper first

`import_file` currently owns its polling loop. Welle 6 needs the same loop with a different
finish condition, so it is extracted **before** the new feature is written:

```python
def _wait_for_counts(self, is_done, *, poll_interval, stability_window,
                     max_timeout, _sleep, _now):
    """Poll object_counts until `is_done(cur, elapsed)` holds for `stability_window`
    consecutive identical polls. Returns the final counts; raises on deadline."""
```

`is_done` receives the current counts and the elapsed seconds, which is exactly what the two
callers need:

| Caller | `is_done` | Why |
|--------|-----------|-----|
| `import_file` | `sum(cur.values()) > before_total or elapsed >= min_settle` | growth, or a settled no-op import (see PR #8) |
| `delete_all_objects` | `sum(cur.values()) == 0` | **no settle floor needed** — zero is unambiguous. An unstarted wipe reads as "not yet zero", never as "done" |

The timeout exception is supplied by the caller so each keeps its own error type.

### 4.1a Retry-login consolidation — separate PR, characterization tests first

An earlier draft called the three 401→relogin blocks "near-identical copies" and claimed the
consolidation was behaviour-preserving and covered by the existing suite. **Both claims are
false**, and the correction is load-bearing enough to have its own section.

`grep -rn "401" tests/` returns **nothing**. The only response factory is
`make_response(json_data, status_code=200)` and no caller ever passes `401`, so all three retry
branches (`gramps_client.py` ~:195, ~:212, ~:236) are dead to the test suite. There is no
regression net.

And they are not interchangeable:

| | timeout | body kwarg | extra header | headers on retry |
|---|---|---|---|---|
| `_request` | `10` | `json=` | — | rebuilt from scratch |
| `_raw_get_bytes` | `EXPORT_TIMEOUT` | — | — | rebuilt from scratch |
| `_raw_post_bytes` | `IMPORT_HTTP_TIMEOUT` | `data=` | `Content-Type: application/octet-stream` | **mutated in place** |

The last column is the trap. `_raw_post_bytes` mutates `headers["Authorization"]` precisely so
its `Content-Type` survives the retry. A shared helper that follows the majority pattern and
rebuilds the dict **silently drops `Content-Type` on the retried import POST** — a bug that
appears only when a JWT expires mid-import, and that no current test can catch.

Contract for the shared helper, so this cannot be got wrong by accident:

- takes method, path, timeout, extra headers and body kwargs **per call site** — no defaults
  borrowed from another caller;
- **builds the header dict once** and, on retry, replaces only the `Authorization` value. Never
  rebuilds.

**Sequencing:** this is PR 1 of the wave (§7), merged and green on its own, with the
characterization tests in §6.0 written **before** any retry code is touched. It does not travel
in the same PR as the wipe.

### 4.2 `gramps_client.py` — the operation

```python
def delete_all_objects(self, *, expected_count, poll_interval=…, stability_window=…,
                       max_timeout=…, _sleep=time.sleep, _now=time.monotonic):
    """Delete every object in the tree. Returns {before, after, deleted}."""
```

Order of operations — the precondition is checked **before** anything is destroyed:

1. `before = self.object_counts()`; `before_total = sum(before.values())`.
2. If `expected_count != before_total` → raise `DeleteAllCountMismatchError` with both numbers.
   **No HTTP call has been made yet.** The tree is untouched.
3. `self._login()` — fresh JWT, per §3.
4. `POST /api/objects/delete/`, tolerating `200` and `202`.
5. `_wait_for_counts(lambda cur, _: sum(cur.values()) == 0, …)` → `DeleteAllTimeoutError` on
   deadline, carrying `before` and the last counts seen so a partial wipe is diagnosable.
6. Return `{"before": before, "after": after, "deleted": {k: before[k] - after.get(k, 0)
   for k in before}}`.

New exceptions: `DeleteAllCountMismatchError`, `DeleteAllTimeoutError`,
`DeleteAllStateUnknownError` (raised from the final review round when confirming
completion itself fails after the delete request — the delete may have been accepted,
but the resulting state could not be confirmed).

**Empty tree is not a special case.** `expected_count=0` against an empty tree passes the
precondition, posts, and the completion predicate is satisfied on the first poll. No
short-circuit branch — one path, one set of tests.

### 4.3 `server.py` — the MCP tool

Registered inside the existing `if enable_destructive:` block:

```python
@register
def gramps_delete_all_objects(confirm: bool = False, expected_count: int = None) -> dict:
    """Delete EVERY object in the tree — people, families, events, places, sources,
    citations, repositories, media, notes and tags. DESTRUCTIVE AND IRREVERSIBLE.

    Requires confirm=True AND expected_count set to the tree's current total object
    count (call gramps_get_object_counts first and sum it). If expected_count does
    not match the live total, nothing is deleted. Take a backup with
    gramps_export_tree first — there is no undo.
    """
```

Both guards are checked in the tool before reaching the client, so a missing `confirm` or a
missing `expected_count` fails without a single HTTP call.

The docstring is API surface: it is what the calling model reads when deciding whether this
tool fits a request. It has to make the irreversibility and the backup step impossible to miss.

## 5. Deployment / Ops

- Automation user must be **OWNER** (`GRAMPS_ROLE=4`) — already required by Welle 5's import.
- No new environment variable. `GRAMPS_ENABLE_DESTRUCTIVE=1` registers it, and per §2.1 that
  flag is on in production.
- Tool count moves **30 → 31** with the gate on; **27** with it off. The Docker smoke test
  asserts both numbers, as in Welle 5.

## 6. Tests (TDD)

Written first, in this order:

**§6.0 — 401-relogin characterization (PR 1, before touching any retry code)**

These do not exist today and are the reason the consolidation is its own PR. One per call site,
each: first call returns `401` → assert `_login()` ran → assert the **second** request carried
the refreshed bearer token, that call site's own timeout, its own body kwarg, and — for
`_raw_post_bytes` — that `Content-Type: application/octet-stream` **survived the retry**.

0a. `_request` — 401 → relogin → 200, `json=` body, `timeout=10`.
0b. `_raw_get_bytes` — 401 → relogin → 200, no body kwarg, `timeout=EXPORT_TIMEOUT`.
0c. `_raw_post_bytes` — 401 → relogin → 200, `data=` body, `timeout=IMPORT_HTTP_TIMEOUT`,
    **Content-Type present on both attempts**. This is the assertion that fails on a naive merge.

**Helper extraction (behaviour-preserving)**
1. Existing `import_file` tests stay green through the `_wait_for_counts` extraction — that is
   the regression net for the refactor.
2. `_wait_for_counts` returns as soon as the predicate holds for `stability_window` polls.
3. `_wait_for_counts` raises the caller-supplied exception at the deadline.

**Precondition (nothing is destroyed)**
4. `expected_count` mismatching the live total → `DeleteAllCountMismatchError`, **and no POST
   was issued** (assert on the request mock, not just the exception).
5. `expected_count=None` → error, no POST.
6. `confirm` not `True` at the tool layer → error, no client call.

**Happy path**
7. Sync `200`: counts go to zero → `{before, after, deleted}` with `deleted == before`.
8. Async `202`: counts fall over several polls, then zero → same result shape.
9. `_login()` is called **after** the precondition check and **before** the POST (assert call
   order — this is the fresh-JWT requirement, and it is invisible otherwise).
10. Empty tree, `expected_count=0` → succeeds, `deleted` all zeros.

**Failure**
11. Counts never reach zero → `DeleteAllTimeoutError` carrying `before` and last-seen counts.
12. Completion is never derived from `/api/tasks/` (assert no such URL is ever requested —
    the same guard Welle 5 uses).

**Registration**
13. Gate off → `gramps_delete_all_objects` is not registered (27 tools).
14. Gate on → registered (31 tools).

## 7. Delivery

**Three PRs, not one.** `_request` is the sole HTTP path under all 27 existing tools, and
`object_counts()` is simultaneously the source of the `expected_count` precondition *and* the
wipe's completion signal — so a regression in the shared retry helper would corrupt both sides
of the destructive operation at once. That refactor does not belong in the same reviewable diff
as the tree-wipe.

| PR | Contents | Gate |
|----|----------|------|
| 1 | §6.0 characterization tests, then the shared retry helper | green on its own before PR 2 starts |
| 2 | `_wait_for_counts` extraction; `import_file` switched over, its tests untouched | existing suite is the net |
| 3 | `delete_all_objects` + `gramps_delete_all_objects` | §6 tests 2–14 |

Then: owner merge → tag `v0.5.0` → Docker image `:latest` + `:v0.5.0` + container smoke test
(27 / 31). Per `CLAUDE.md`, check the `Dockerfile` COPY list and the README tool count *inside*
PR 3, not after it.

README gets a Wipe subsection under **Destructive tools** documenting the two-argument
confirmation and the export-first advice.

## 8. Out of scope (explicit)

- **Namespace / partial wipe** (`?namespaces=…`). Deliberately not exposed: a partial wipe is
  harder to reason about than a full one and multiplies the ways to get it wrong.
- **`/api/tasks/` polling** — unreliable by TTL, same as Welle 5.
- **Undo / soft delete.** Gramps Web has no such concept; `gramps_export_tree` is the backup
  story and the docstring points at it.
- **A dry-run mode.** `gramps_get_object_counts` already answers "what would be deleted", and
  `expected_count` forces the caller through it.
