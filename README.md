# gramps-remote-mcp

A remote [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server for
[Gramps](https://gramps-project.org), the open-source genealogy application. It lets an
MCP client (Claude, or any MCP-capable assistant) read and edit a family tree hosted on
a running [Gramps Web](https://www.grampsweb.org) instance through its REST API.

Unlike MCP servers that read a local Gramps database file, this one talks to Gramps Web
over HTTP, so it works against a live, shared instance — and it focuses on **guided,
guarded write operations**: new records are tagged as unconfirmed for later review, and
field mutations and record creates are protected by before/after snapshots and
record-count guards.

## Tools

The server exposes 25 tools over the MCP **stdio** transport, plus 3 optional destructive
tools (`gramps_delete_person`, `gramps_delete_family`, `gramps_delete_blog_post`) that are
registered only when explicitly enabled — see [Destructive tools](#destructive-tools).

**Read**

| Tool | Purpose |
| --- | --- |
| `gramps_get_person(gramps_id)` | Fetch the current live record for a person by Gramps ID (e.g. `I0024`). |
| `gramps_search_person(query, limit=None)` | Search people (case-insensitive substring) across first name, surname, the combined `First Surname`, the nickname, and alternate/maiden names; optional result `limit`. |
| `gramps_list_people(keys=None, page=None, pagesize=None)` | List people, optionally selecting fields (`keys`) and paginating (`page` is 1-based, `pagesize` caps rows). Omit both to return everyone. |
| `gramps_get_object_counts()` | Return the tree's object counts (people, families, events, notes, media, …) — handy as a before/after guard. |
| `gramps_get_descendants(gramps_id, grade=1)` | Return the person and their descendants as a nested JSON tree, `grade` generations deep. |
| `gramps_get_ancestors(gramps_id, grade=1)` | Return the person and their ancestors as a nested JSON tree, `grade` generations up. |
| `gramps_get_relations(gramps_id)` | Return a person's family context: parent families (father/mother slots) and own families (partner + children). Each person carries its own `gender`; father/mother are bloodline slots, not sex. |

**Write**

| Tool | Purpose |
| --- | --- |
| `gramps_set_gender(gramps_id, gender)` | Set a person's gender (`0`=Female, `1`=Male, `2`=Unknown, `3`=Other). |
| `gramps_set_surname(gramps_id, surname, name_type=None)` | Set the primary surname, optionally the name type (e.g. `Married Name`). |
| `gramps_set_first_name(gramps_id, first_name)` | Set a person's primary given (first) name. Non-destructive; returns before/after. |
| `gramps_set_gender_bulk(items)` | Set gender for many people in one call (`items` = `[{"gramps_id", "gender"}, …]`) under a single count-guard; best-effort, per-item results and errors. |
| `gramps_set_surname_bulk(items)` | Set the primary surname for many people in one call (`items` = `[{"gramps_id", "surname", "name_type"?}, …]`) under a single count-guard; best-effort. |
| `gramps_add_birth_name(gramps_id, surname, first_name=None)` | Add a `Birth Name` alternate-name entry. |
| `gramps_add_alternate_name(gramps_id, surname, first_name=None, name_type="Birth Name")` | Add an alternate name of a given type (e.g. `Birth Name`, `Married Name`). Surname content is taken from the argument; other subfields default. |
| `gramps_swap_primary_name(gramps_id, alt_index=0)` | Swap a person's primary name with one of their alternate names (`alt_index` selects which, default the first). Promotes e.g. a Birth Name to primary and demotes the Married Name to an alternate; errors if there is no alternate at that index. |
| `gramps_add_person(first_name, surname, gender, birth_year=None, birth_quality=None, birth_year_to=None, note=None)` | Create a new person, tagged as **unconfirmed** for later review. Returns the new Gramps ID. |
| `gramps_add_family(spouse_a_id, spouse_b_id=None)` | Create a family linking one or two spouses. Returns the new family's Gramps ID. |
| `gramps_add_child_to_family(family_id, child_id)` | Link an existing person as a child of an existing family. |
| `gramps_set_family_parent(family_id, gramps_id, role)` | Set the father or mother of an **existing** family (`role` = `father`/`mother`, an explicit bloodline slot — never reordered by sex, unlike `gramps_add_family`). Adds a missing parent or replaces the wrong one (refusing to set one person as both parents); returns the displaced parent as `previous`. |
| `gramps_remove_child_from_family(family_id, child_id)` | Remove a person from a family's children (inverse of `gramps_add_child_to_family`) — e.g. detach a spouse wrongly recorded as a child. Siblings are left intact. |
| `gramps_confirm_person(gramps_id)` | Remove the **unconfirmed** tag, marking a person as confirmed. |

### Blog posts

A blog post is a `Source` object tagged `Blog`, with its body text stored in the source's
first note — not a dedicated blog record (see [`docs/blog-crud.md`](docs/blog-crud.md) for
the full data model). The body's storage format is controlled by `GRAMPS_BLOG_BODY_FORMAT`:
plain text by default, or HTML (rendered and sanitized server-side) when set to `html`.
A post's body-note type is fixed when the post is created, so set `GRAMPS_BLOG_BODY_FORMAT` once
per deployment: flipping it on a tree that already has posts leaves those posts rendering in their
original format (a later HTML update to a text-mode post shows up escaped — visible but harmless).
Deleting a blog post (`gramps_delete_blog_post`) is destructive and only available when the
destructive-tools gate is on — see [Destructive tools](#destructive-tools).

| Tool | Purpose |
| --- | --- |
| `gramps_create_blog_post(title, body, author=None)` | Create a blog post (a Source tagged `Blog` + a body note). Body stored per `GRAMPS_BLOG_BODY_FORMAT` (plain text default, or HTML). Returns the new source gramps_id (e.g. `S0002`). |
| `gramps_list_blog_posts(page=None, pagesize=None)` | List blog posts (Sources tagged `Blog`), newest first. Returns `[{gramps_id, title, author, change}]`. `page` is 1-based, `pagesize` caps rows; omit both for all. |
| `gramps_get_blog_post(gramps_id)` | Fetch one blog post by its Source Gramps ID. Returns title, author, change (unix ts), the body as rendered HTML (`body_html`) and raw string (`body_text`), and `note_gramps_id`. |
| `gramps_update_blog_post(gramps_id, title=None, body=None, author=None)` | Update a blog post's title, body, and/or author (only what you pass). `body` is stored per `GRAMPS_BLOG_BODY_FORMAT` and replaces the existing note's text. Returns `{gramps_id, updated}`. |

### Destructive tools

These destructive tools exist but are **off by default and not even registered**, so MCP
clients never see them unless you opt in:

| Tool | Purpose |
| --- | --- |
| `gramps_delete_person(gramps_id, confirm)` | Permanently delete a person (for duplicates / erroneous entries). Requires `confirm=True` and guards that the people count drops by exactly one. Deleting a linked person unlinks them from their families (the slot is cleared) rather than cascading. Notes attached only to this person are cleaned up too (shared notes are kept); deleted note handles are returned as `deleted_notes`. |
| `gramps_delete_family(family_id, confirm)` | Permanently delete a family — for cleaning up an orphaned/childless family left behind after re-homing its children. Requires `confirm=True`, refuses if the family still has children (remove them first), and guards that the family count drops by exactly one. |
| `gramps_delete_blog_post(gramps_id, confirm)` | Permanently delete a blog post. Requires `confirm=True`. Removes the Source and cleans up its body note if now orphaned (shared notes are kept); guards that the source count drops by exactly one. |

To enable it, set `GRAMPS_ENABLE_DESTRUCTIVE=1` in the server's environment; the account
also needs delete rights on the tree. Leave it unset for a read/edit-only deployment.

## Prerequisites

- A running **Gramps Web** instance reachable over HTTP(S) with its REST API enabled.
- A Gramps Web **user account with the EDITOR role** (role `3`). Editor rights are
  required for the write tools (create person/family, set fields, add children, confirm).
- Python 3.12+ (or Docker) to run the server.

The server authenticates with username/password against `POST /api/token/`, then sends the
returned JWT as a bearer token on every request and transparently re-authenticates on a
`401`.

> **Tip:** Create a dedicated, least-privilege automation user rather than reusing a
> personal login. The helper script [`ops/setup-automation-user.sh`](ops/setup-automation-user.sh)
> creates a persistent EDITOR user and prints a generated password once.

## Configuration

The server reads its connection settings from environment variables (see
[`.env.example`](.env.example)):

| Variable | Description |
| --- | --- |
| `GRAMPS_BASE_URL` | Base URL of the Gramps Web instance, e.g. `https://gramps.example.com`. |
| `GRAMPS_USERNAME` | Username of the EDITOR-role account. |
| `GRAMPS_PASSWORD` | Password for that account. |

Copy `.env.example` to `.env` and fill in real values. `.env` is git-ignored — never
commit credentials.

## Run with Docker

```bash
docker build -t gramps-remote-mcp .
docker run --rm -i --env-file .env gramps-remote-mcp
```

The image runs `python server.py` as its entrypoint and speaks MCP over stdio, so `-i`
(interactive stdin) is required.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
GRAMPS_BASE_URL=https://gramps.example.com \
GRAMPS_USERNAME=mcp-automation \
GRAMPS_PASSWORD=... \
.venv/bin/python server.py
```

## Configure an MCP client

Add the server to your MCP client configuration. Example (Claude Desktop / `.mcp.json`
style), using the Docker image:

```json
{
  "mcpServers": {
    "gramps": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "--env-file", "/absolute/path/to/.env", "gramps-remote-mcp"]
    }
  }
}
```

Or run the Python script directly:

```json
{
  "mcpServers": {
    "gramps": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/server.py"],
      "env": {
        "GRAMPS_BASE_URL": "https://gramps.example.com",
        "GRAMPS_USERNAME": "mcp-automation",
        "GRAMPS_PASSWORD": "..."
      }
    }
  }
}
```

## Design focus

- **Unconfirmed lifecycle.** `gramps_add_person` tags every new record as unconfirmed
  (a dedicated review tag); `gramps_confirm_person` removes it. This gives you a
  review queue for records created by an assistant before they are accepted as final.
- **Guarded writes.** Field mutations capture a `before`/`after` snapshot and verify that
  the total person (or family) count is unchanged — or increased by exactly one on a
  create — raising an error otherwise, so an unexpected side effect fails loudly instead
  of silently corrupting the tree. Structural family edits (`add_child_to_family`,
  `remove_child_from_family`, `set_family_parent`) instead PUT the whole family and rely on
  the Gramps Web API's referential integrity rather than a local count guard; the
  destructive `delete_person` keeps its own exact −1 person-count guard.
- **Idempotency guards.** `gramps_add_child_to_family` refuses to add a child that is
  already linked; the unconfirmed tag is looked up and reused (with Unicode NFC
  normalization) rather than duplicated.
- **Gender-based parent slots.** When creating a family with `gramps_add_family`, the
  spouse with gender Female is assigned as mother and the other as father; if gender
  doesn't disambiguate, call order decides deterministically. To place a parent into a
  specific slot regardless of sex, use `gramps_set_family_parent` with an explicit `role`.
- **Structured relative trees.** `gramps_get_descendants` and `gramps_get_ancestors`
  return nested JSON trees bounded to `grade` generations, and `gramps_get_relations`
  gives a person's full family context in one call — rather than flat lists.
- **Bloodline ≠ gender.** In GEDCOM-imported data a family's `father`/`mother` slots
  follow the bloodline, not sex. The relation/ancestor tools therefore never infer sex
  from a slot: every person carries its own `gender`, and partners are resolved as "the
  other slot" regardless of gender. `gramps_set_family_parent` follows the same rule — you
  pass an explicit `role` (`father`/`mother`), and it never reorders by sex.
- **Batch writes with one guard.** `gramps_set_gender_bulk` / `gramps_set_surname_bulk`
  apply many updates under a single record-count guard. They are best-effort (not atomic):
  a failing item is reported in `errors` and does not abort the rest, and the count guard
  is reported (`count_guard_ok`) rather than raised so partial results are never lost.

## Development

Install the dev dependencies and run the test suite:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The tests mock the Gramps Web HTTP layer, so no live instance is required. Test fixtures
use generic placeholder names.

## Notes on the Gramps Web API

* [`docs/blog-crud.md`](docs/blog-crud.md) — how blog posts are modelled in Gramps Web
  (a `Source` tagged `Blog`, not a note) and how to CRUD them over the REST API, including
  verified pitfalls around `PUT` semantics, `If-Match` and styled text.

## Related projects

Several other MCP servers target Gramps; if this one doesn't fit your stack, one of these
might:

- [cabout-me/gramps-mcp](https://github.com/cabout-me/gramps-mcp) — Python, Gramps Web REST
  API, HTTP + stdio, with a broader ~16-tool set that also covers events, places, sources,
  citations and media (AGPL-3.0).
- [Alexey-N-Chernyshov/gramps-web-mcp-rs](https://github.com/Alexey-N-Chernyshov/gramps-web-mcp-rs)
  — a Rust server for the Gramps Web API with an optional read-only mode.
- [Scormave/gramps-web-mcp](https://github.com/Scormave/gramps-web-mcp) — a .NET 8 server
  for Gramps Web.
- [dsblank/gramps-ez-mcp](https://github.com/dsblank/gramps-ez-mcp) — an easy-to-use Gramps
  MCP server.
- [adamhathcock/gramps-db-tool](https://github.com/adamhathcock/gramps-db-tool) — works
  directly on a local Gramps database file rather than the Web API.

This project's niche is remote operation against a live **Gramps Web** instance with a
small, opinionated, review-oriented guarded-write workflow: the unconfirmed-record
lifecycle plus before/after and record-count guards.

## License

Released under the [MIT License](LICENSE).
