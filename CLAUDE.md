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

## Releasing a wave

Features ship in numbered waves. The ritual is stable across releases and two of its steps have
bitten this project before, so they are written down rather than rediscovered:

1. **PR, always.** `main` is protected by the `protect-main` ruleset — no direct pushes. History
   uses **merge commits**, not squash. Only the repo owner can merge.
2. **Check the Dockerfile.** It copies an **explicit list** of modules, not `COPY . .`:
   `COPY backup_store.py gramps_client.py gramps_blog.py server.py ./`. A new root-level module
   that is not added to that line produces an image that builds fine and crashes on import. This
   has shipped broken twice.
3. **Check the README tool count.** The `## Tools` intro states a number that drifts every wave.
   Verify it in the PR, not after:
   ```bash
   python -c "from unittest.mock import MagicMock; import server; \
   print('off', len(server.create_server(MagicMock())[1]), \
   'on', len(server.create_server(MagicMock(), enable_destructive=True)[1]))"
   ```
4. **Tag and release.** One **minor** bump per wave. There is deliberately no version string
   anywhere in the repo — a release is an annotated tag plus a GitHub release, nothing to edit.
5. **Build the image and smoke-test it** with the same off/on tool-count command as step 3, run
   inside the container, plus an `import` of any new module.
6. **Restart the MCP connection.** The client spawns the stdio container per session from
   `:latest`, so a freshly built image does not take effect until the connection is restarted.
   A stale container has previously looked like "the new tools are missing".

## Working notes

`PROGRESS.md` at the repo root is the canonical resume anchor — current state, roadmap, next
action, decision log. It is **gitignored** (local only), as is `docs/Progress_archive.md`. Read
it at the start of a session; keep it current in the same move as the work.
