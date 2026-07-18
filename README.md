# gramps-remote-mcp

A remote [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server for
[Gramps](https://gramps-project.org), the open-source genealogy application. It lets an
MCP client (Claude, or any MCP-capable assistant) read and edit a family tree hosted on
a running [Gramps Web](https://www.grampsweb.org) instance through its REST API.

Unlike MCP servers that read a local Gramps database file, this one talks to Gramps Web
over HTTP, so it works against a live, shared instance — and it focuses on **guided,
guarded write operations**: new records are tagged as unconfirmed for later review, and
every mutation is protected by before/after snapshots and record-count guards.

## Tools

The server exposes 10 tools over the MCP **stdio** transport:

| Tool | Purpose |
| --- | --- |
| `gramps_get_person(gramps_id)` | Fetch the current live record for a person by Gramps ID (e.g. `I0024`). |
| `gramps_search_person(query)` | Search people by first or last name (case-insensitive substring match). |
| `gramps_get_descendants(gramps_id, grade=1)` | Return the person and their descendants as a nested JSON tree, `grade` generations deep. |
| `gramps_set_gender(gramps_id, gender)` | Set a person's gender (`0`=Female, `1`=Male, `2`=Unknown, `3`=Other). |
| `gramps_set_surname(gramps_id, surname, name_type=None)` | Set the primary surname, optionally the name type (e.g. `Married Name`). |
| `gramps_add_birth_name(gramps_id, surname, first_name=None)` | Add a `Birth Name` alternate-name entry. |
| `gramps_add_person(first_name, surname, gender, birth_year=None, birth_quality=None, birth_year_to=None, note=None)` | Create a new person, tagged **Unbestätigt** (unconfirmed). Returns the new Gramps ID. |
| `gramps_add_family(spouse_a_id, spouse_b_id=None)` | Create a family linking one or two spouses. Returns the new family's Gramps ID. |
| `gramps_add_child_to_family(family_id, child_id)` | Link an existing person as a child of an existing family. |
| `gramps_confirm_person(gramps_id)` | Remove the **Unbestätigt** tag, marking a person as confirmed. |

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

- **Unconfirmed lifecycle.** `gramps_add_person` tags every new record with an
  `Unbestätigt` ("unconfirmed") tag; `gramps_confirm_person` removes it. This gives you a
  review queue for records created by an assistant before they are accepted as final.
- **Guarded writes.** Field mutations capture a `before`/`after` snapshot and verify that
  the total person (or family) count is unchanged — or increased by exactly one on a
  create — raising an error otherwise, so an unexpected side effect fails loudly instead
  of silently corrupting the tree.
- **Idempotency guards.** `gramps_add_child_to_family` refuses to add a child that is
  already linked; the unconfirmed tag is looked up and reused (with Unicode NFC
  normalization) rather than duplicated.
- **Gender-based parent slots.** When creating a family, the spouse with gender Female is
  assigned as mother and the other as father; if gender doesn't disambiguate, call order
  decides deterministically.
- **Structured descendant tree.** `gramps_get_descendants` returns a nested JSON tree
  bounded to `grade` generations, rather than a flat list.

## Development

Install the dev dependencies and run the test suite:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The tests mock the Gramps Web HTTP layer, so no live instance is required. Test fixtures
use generic placeholder names.

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
