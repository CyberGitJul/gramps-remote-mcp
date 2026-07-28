# CLAUDE.md

Guidance for Claude Code (and other agents) working in this repository.

## What this is

An MCP server (stdio transport) that exposes a live [Gramps Web](https://www.grampsweb.org)
genealogy instance to an MCP client over its REST API. See `README.md` for the user-facing
description and the full tool list.

## Commands

```bash
pip install -r requirements-dev.txt   # runtime + dev deps

pytest -q                             # full suite (fast, no network — the API is mocked)
pytest tests/test_gramps_client.py -q # one file
pytest -q -k import_file              # one topic

ruff check .                          # lint
ruff format .                         # format
ruff format --check .                 # verify only, as CI does

pre-commit install                    # once per clone: auto-fix on commit, verify on push

python server.py                      # run the server (needs the env vars below)
```

CI (`.github/workflows/lint.yml`) runs `ruff check`, `ruff format --check` and `pytest`.

## Environment

| Variable | Purpose |
| --- | --- |
| `GRAMPS_BASE_URL` | Gramps Web instance to talk to |
| `GRAMPS_USERNAME` / `GRAMPS_PASSWORD` | credentials; the client re-logs in on 401 and retries once |
| `GRAMPS_ENABLE_DESTRUCTIVE` | `1` registers the `gramps_delete_*` tools. Absent = they do not exist |
| `GRAMPS_BACKUP_DIR` | directory `gramps_export_tree` / `gramps_import_file` may touch |
| `GRAMPS_BLOG_BODY_FORMAT` | blog post body format |

Never commit real credentials — `.env*` is ignored except `.env.example`.

## Layout

Four flat modules at the root, no package:

- `server.py` — MCP tool registration only. `create_server(client, ...)` is a factory with a
  closure `register()` decorator; `main()` wires it to stdio.
- `gramps_client.py` — all Gramps Web REST calls, guards, retry/relogin, polling.
- `gramps_blog.py` — blog-post CRUD.
- `backup_store.py` — filesystem access for export/import, with the path-traversal guard.

Tests mirror the modules one-to-one under `tests/`.

## Conventions that matter

**Guarded writes.** Every mutating operation takes a before-snapshot, performs the change, takes
an after-snapshot, and asserts the record count moved exactly as expected — otherwise it raises
rather than reporting success. Tools return `before` / `after` / the delta so the caller can
verify independently. Keep this shape for new write tools; it is the core invariant of this
server.

**Destructive tools are structurally gated,** not merely disabled: without the env flag they are
never registered, so they cannot be called at all. The flag is normalised explicitly
(`is True or == "1"`) so a truthy string cannot fail open. Preserve both properties.

**Docstrings are the contract.** Each MCP tool's docstring is what the calling LLM sees and
reasons about — it is API surface, not a comment. State destructiveness, required confirmations
and what the return value contains.

**Completion is detected from object counts,** never from `/api/tasks/` (unreliable —
TTL-reaped). Long-running imports/exports poll `object_counts()` until stable. Time is injected
(`_sleep`, `_now` parameters) so tests stay deterministic and fast; never call `time.sleep`
directly in a polling loop.

**Tests are written first** and the suite is expected to stay green. The test-to-source ratio is
deliberately high (~2.6:1); a new branch of behaviour without a test is incomplete.

## Working notes

`PROGRESS.md` at the repo root is the canonical resume anchor — current state, roadmap, next
action, decision log. It is **gitignored** (local only), as is `docs/Progress_archive.md`. Read
it at the start of a session; keep it current in the same move as the work.
