# Design — Welle 4: Blog-CRUD (Gramps Web MCP)

**Status:** approved (brainstorming) · **Date:** 2026-07-19 · **Branch:** `feat/blog-crud`

Adds MCP tools to manage **Gramps Web blog posts** over the REST API. Grounded in the
live-verified API notes in [`docs/blog-crud.md`](../../blog-crud.md).

## Background — the data model

A "blog post" in Gramps Web is **not** a dedicated object. It is:

> a **`Source`** carrying a tag named **`Blog`**, whose body text is the **first `Note`**
> in `source.note_list`.

| Blog element (frontend) | Origin in the data model |
| --- | --- |
| Title | `source.title` |
| Author | `source.author` |
| Date shown | `source.change` (last-change unix ts — no separate publish date) |
| Body | `source.note_list[0]` → the Note's rendered text |
| Visible as blog | tag `name == "Blog"` in `source.tag_list` |

There is no blog endpoint; everything goes through the generic `Source`/`Note`/`Tag` CRUD.
The blog view queries `GET /api/sources/?rules={"rules":[{"name":"HasTag","values":["Blog"]}]}&sort=-change`.

### Rendering: no Markdown — plain text OR HTML (env-selectable)

Verified against current source (`gramps-web`, `gramps-web-api`, `gramps` core) on 2026-07-19:

- Gramps Web **does not parse Markdown** in note bodies. Formatting comes **only** from
  `StyledText` tag ranges (bold/italic/link/…), rendered to HTML by
  `DocBackend.add_markup_from_styled`. A literal `**bold**` renders verbatim (escaped).
- **Plain text** path: a General note whose `text.string` is the body. `format=0` (flowed) turns
  line breaks into paragraphs; bare URLs are auto-linked by the frontend; any markup shows
  literally. Simplest, no rendering surprises. Uses the existing `_create_note`.
- **HTML** path: a Note of type **`NoteType.HTML_CODE`** is rendered by passing its raw string
  through as HTML (`escape=False` in `gramps_webapi/api/html.py`), sanitized server-side with
  `bleach` against a fixed allow-list. The blog-post frontend renders `note.formatted.html`, so an
  `HTML_CODE` note yields real formatting (bold/links/lists/headings) **without** computing
  StyledText ranges.
- `note.format` (0=flowed, 1=preformatted) controls only whitespace, unrelated to the choice above.

**Decision:** the body format is **chosen per deployment via an env flag**, not hard-wired.

> **`GRAMPS_BLOG_BODY_FORMAT`** — value exactly `html` → HTML mode (`HTML_CODE` note);
> anything else (unset, `text`, `0`, garbage) → **plain text** mode (General note).
> **Default is plain text**, and the normalization is **fail-safe** (same strict rule as
> `GRAMPS_ENABLE_DESTRUCTIVE`: only the exact opt-in string enables the richer/riskier path, so a
> stray value can never silently turn on HTML). Owners opt into HTML deliberately.

The flag affects only **note creation** (`create_blog_post`, and the update edge where a post has
no note yet). `update_blog_post` with a `body` **preserves the existing note's type** — it only
replaces the string — so switching the flag never flips the type of already-published posts.

## Scope

**In scope — full CRUD, 5 tools:**
`gramps_list_blog_posts`, `gramps_get_blog_post`, `gramps_create_blog_post`,
`gramps_update_blog_post`, `gramps_delete_blog_post` (the last is destructive, env-gated).
Body format (plain text vs HTML) selectable per deployment via `GRAMPS_BLOG_BODY_FORMAT`.

**Out of scope (YAGNI):** media / title image upload (`/api/media/`), galleries, Markdown or
StyledText-range authoring, `If-Match` optimistic locking (unusable in 3.17.0 — always 412).

## Architecture & module layout

- **New file `gramps_blog.py`** with `class BlogMixin:` holding the 5 blog methods plus a small
  HTML-note helper. Methods use the existing `GrampsClient` helpers via `self.`:
  `_request`, `_create_object`, `_get_or_create_tag`, `_delete_orphaned_notes`, `count_*`.
- **`gramps_client.py`**: `class GrampsClient(BlogMixin):` — only the base-class list changes;
  no change to the client's public API or to callers.
- **`server.py` / `create_server`**: 4 non-destructive tools registered normally; the destructive
  `gramps_delete_blog_post` goes inside the existing `if enable_destructive:` block. Every tool
  stays a thin `return client.<method>(...)` wrapper, matching the existing style.
- **Public identifier is the source `gramps_id`** (e.g. `S0001`) everywhere — consistent with all
  other tools. Handles stay internal.
- **Body-format config lives on the client**, since it governs *client behavior* (how a note is
  created), separate from the server-side `enable_destructive` (which governs tool registration).
  `GrampsClient.__init__` gains a `blog_body_format=None` parameter, normalized fail-safe to
  `"html"` iff the value is exactly `"html"`, else `"text"`, and stored as
  `self.blog_body_format`. `main()` reads `os.environ.get("GRAMPS_BLOG_BODY_FORMAT")` and passes it
  in; tests pass the mode explicitly. `create_server` does not need to know about it.

Rationale for the mixin: `gramps_client.py` is already ~793 lines; blog is a distinct concern
(Source/Note/Tag vs Person/Family). A mixin isolates it into its own file while sharing the
existing helpers naturally, with no indirection.

## The 5 tools

### `gramps_list_blog_posts(page=None, pagesize=None) -> list`
- `GET /api/sources/?rules=<HasTag Blog>&sort=-change&keys=gramps_id,title,author,change`
- Returns a slim list `[{gramps_id, title, author, change}]`, newest first.
- **`page=1` default when only `pagesize` is given** — the same pagination gotcha already fixed
  for `list_people` (default `page=0` = "all"); untested for `/sources/` (doc §10), so we apply
  the guard defensively.

### `gramps_get_blog_post(gramps_id) -> dict`
- Resolve source by `gramps_id` (`extend=all`), render the **first** body note
  (`note_list[0]`) as HTML.
- Returns `{gramps_id, title, author, change, body_html, body_text, note_gramps_id}` where
  `change` is the raw unix ts, `body_html` comes from `GET /api/notes/{handle}?formats=html`, and
  `body_text` is the raw string.
- Edge: source has an empty `note_list` (foreign post with no body) → `body_html`/`body_text` are
  `None` and `note_gramps_id` is `None`; the call still succeeds.
- Unknown id → `BlogPostNotFoundError`.

### `gramps_create_blog_post(title, body, author=None) -> str`
- `body` is interpreted per `self.blog_body_format` (HTML if `html`, else plain text). Steps:
  1. `_get_or_create_tag("Blog")` → tag handle.
  2. `_create_body_note(body)` → note handle (HTML_CODE note in HTML mode, General note in text
     mode — see helper below).
  3. `_create_object("sources", {...title, author, note_list:[note], tag_list:[tag]})`.
- Count-guard: sources count must rise by exactly one (`BlogPostCreateCountMismatchError`).
- Returns the new source `gramps_id`.

### `gramps_update_blog_post(gramps_id, title=None, body=None, author=None) -> dict`
- **Partial update**: only provided fields change; others untouched.
- `title` / `author` → **Read-Modify-Write on the Source** (GET full object, set field, PUT).
- `body` → **Read-Modify-Write on the first body Note** (`note_list[0]`): replace `text.string`
  only, **preserving the note's existing type** (so switching `GRAMPS_BLOG_BODY_FORMAT` never flips
  the type of an already-published post).
- Edge: source has no note but `body` given (foreign-created post) → create a note via
  `_create_body_note(body)` (type per the current flag) and append its handle to `note_list`
  (RMW on the source).
- Returns `{gramps_id, updated: [<changed field names>]}`.

### `gramps_delete_blog_post(gramps_id, confirm=False) -> dict`  *(destructive, env-gated)*
- Registered **only** when `GRAMPS_ENABLE_DESTRUCTIVE=1` (or `enable_destructive=True`), like
  `gramps_delete_person` / `gramps_delete_family`.
- `confirm is not True` → `ValueError` (strict literal `True`, so a stray truthy value can't delete).
- `DELETE /api/sources/{handle}`; guard sources count drops by exactly one
  (`BlogPostDeleteCountMismatchError`).
- Then `_delete_orphaned_notes(note_list)` — removes body note(s) that are now unreferenced
  (backlinks-checked; shared notes kept). Best-effort — a cleanup failure never fails the delete.
- Returns `{gramps_id, deleted, count_before, count_after, deleted_notes}`.

## Body handling — `_create_body_note` (mode-aware)

- New helper on `BlogMixin`, branches on `self.blog_body_format`:
  - **text mode** → a General note, `text.string = body`, empty StyledText `tags`, `format = 0`.
    This is what the existing `_create_note(text)` already builds, so text mode reuses it directly.
  - **html mode** → a Note of type **`HTML_CODE`**, `text.string = body`, empty `tags`,
    `format = 0`.
  The existing `_create_note` stays as-is (it is the text-mode path).
- **HTML_CODE type payload — resolved during implementation (live smoke):** send either the
  plaintext type string the API accepts for HTML_CODE, or the full object
  `{"_class":"NoteType","value":<HTML_CODE value>,"string":""}`. **Never** send a partial type
  object like `{"value":N}` without `"string"` — that triggers a server **HTTP 500**
  (doc §7). The exact `HTML_CODE` enum value and accepted string form are confirmed against INT
  before finalizing.
- **`bleach` allow-list (html mode):** the server strips tags outside its allow-list on render.
  Which tags survive is documented from the live smoke and noted in `docs/blog-crud.md` / tool
  docstrings.

## Error handling & edge cases

- **`PUT` = full replace**, not merge → always Read-Modify-Write on both Source and Note; never
  write a partial object (a partial PUT would blank `author`/`note_list`/`tag_list` and drop the
  post out of the blog view). Verified in doc §5.
- **No `If-Match`** — last-write-wins; the 3.17.0 ETag/If-Match mismatch always 412s (doc §5.1).
- **Tag duplicates:** `_get_or_create_tag("Blog")` reuse (no unique constraint on tag names).
- **Unknown `gramps_id`:** `BlogPostNotFoundError` (analogous to `PersonNotFoundError`), 404-mapped.
- **Trailing slash** matters: collection `/api/sources/` (with), single object
  `/api/sources/{handle}` (without) — already the convention in `_request` call sites.
- **Response envelope:** POST/PUT/DELETE return a transaction array `[{type, handle, old, new}]`;
  the handle is `resp[0]["handle"]` — the existing `_create_object` already unwraps this.

## Testing

- **TDD** (RED → GREEN), unit tests with a mocked `_request`, mirroring `tests/test_gramps_client.py`.
  Per tool: happy path plus guards —
  - create: count-guard, tag get-or-create reuse, correct note type per mode (General in text mode,
    HTML_CODE in html mode);
  - body-format flag: fail-safe normalization (`"html"` → html; `"text"`/`"0"`/unset/garbage →
    text), and that the mode drives the created note's type;
  - list: rules/sort query shape, `page=1`-when-only-`pagesize` guard;
  - get: HTML render path, not-found, empty-`note_list` edge;
  - update: partial (only provided fields), RMW completeness (no field blanked), existing note type
    preserved on body edit, no-note edge;
  - delete: `confirm is True` strictness, count-guard, orphaned-note cleanup (shared note kept).
- **`tests/test_server.py`:** the 4 non-destructive tools are always registered;
  `gramps_delete_blog_post` present only when the server is started with the gate ON
  (mirrors `gramps_delete_person`).
- **Live smoke against INT** (`.env.int`, OWNER role): create → list → get (assert HTML renders) →
  update → delete incl. note cleanup; restore INT object counts afterward. This run finalizes the
  `HTML_CODE` type payload and records the `bleach` allow-list behavior.
- Baseline today **146 green** → target: all green plus the new tests.

## Open items deferred to implementation (not blockers)

1. Exact `HTML_CODE` type payload accepted by the API (string vs full object) — confirmed on INT.
2. The server-side `bleach` allow-list (which HTML tags/attrs survive) — recorded from the smoke.

## Delivery

Own PR (`feat/blog-crud`), documented like PR #1/#2. README: add the 5 tools + a short "Blog posts"
note (body format via `GRAMPS_BLOG_BODY_FORMAT`, default plain text; destructive delete behind the
gate). `.env.example`: document `GRAMPS_BLOG_BODY_FORMAT` (default `text`, opt into `html`) next to
the existing `GRAMPS_ENABLE_DESTRUCTIVE` entry. Update `docs/blog-crud.md` with the finalized
HTML_CODE payload + bleach findings.
