# Welle 5 — Backup/Restore (`export_tree` / `import_file`) — Design

> Status: **approved for planning** (2026-07-22). Next step: writing-plans → implementation.
> Scope: two MCP tools for full-tree backup (export) and restore/merge (import) against the
> Gramps Web REST API, plus the deployment/ops changes they require.

## 1. Motivation

The MCP server can create/edit individual objects but has no way to **back up** or **restore**
a whole tree. The concrete trigger: repeated Gramps imports are *additive* (they never merge),
so a clean reset means "wipe + import exactly one source". Welle 5 delivers the two file-transport
halves of that workflow:

- **`gramps_export_tree`** — download the tree as a `.gramps` (gzip XML) backup. Read-only, safe.
- **`gramps_import_file`** — upload a file into the tree (additive). Owner-only at the API level.

The destructive wipe (`delete_all_objects`) is **out of scope** — it stays Welle 6. `get_object_counts`
already exists and is reused here as the completion signal.

## 2. Decisions (resolved)

| # | Decision | Resolution |
|---|----------|------------|
| Transport (Entscheidung 3) | How files move through the MCP server | **Mounted backup directory + file paths.** `docker run` gets `-v <host>/data/export:/data` and `GRAMPS_BACKUP_DIR=/data`. No Base64 over MCP (context blowup on export; LLM can't produce MB of Base64 for import). |
| Async completion (Entscheidung 2) | How to know import finished | **Counts-based stabilization** via `GET /api/metadata/` → `object_counts`. Never poll `/api/tasks/`. Works uniformly for both sync (`201`) and async (`202`) deployments. |
| Import gate | Registration policy | **Always registered** (normal write tool). The REST API enforces OWNER (`PERM_IMPORT_FILE`) anyway; no new env flag. |
| Export options | Filter surface | **Full backup only** (`compress=true`, all data). No `living`/`private`/person-filter args — YAGNI, add later if needed. |

## 3. API facts (verified against gramps-web-api, main branch)

- **Export**: `GET /api/exporters/{ext}/file` is **always synchronous** — calls `run_export` inline,
  returns a **raw binary stream** (`send_file`, `application/octet-stream`). No task involved.
  Extensions come from Gramps' registered export plugins (`gramps`, `ged`, `csv`, `vcf`, `gw`, …);
  `gpkg` is force-disabled server-side. Any valid JWT may export (private records only included for
  Member+ roles — irrelevant for the owner automation user).
- **Import**: `POST /api/importers/{ext}/file` takes a **raw binary body** (read from `request.stream`,
  **not** multipart). Empty body → `400`. Gated by `require_permissions([PERM_IMPORT_FILE])` →
  **OWNER (role 4)** and up.
- **Sync vs async**: `POST` export/import go through `run_task()`. If `CELERY_CONFIG` is empty
  (no broker) → runs **synchronously**, returns `201`. If Celery/Redis is wired up (the INT
  deployment) → **`202`** with `{"task":{"id","href"}}`. A robust client must tolerate **both**.
- **`/api/tasks/{id}` is unreliable for completion**: task metadata rows are purged after a TTL
  (default ~24h) and the Celery result backend expires results on the same TTL, after which state
  reverts to a `PENDING`-looking response (and older versions/proxies may 404). → Do **not** rely on
  it. `object_counts` is the authoritative, deployment-independent completion signal.

## 4. Architecture — three isolated units

### 4.1 `gramps_client.py` — REST layer (unchanged `_request`)

`_request` is **not touched** (per project rule). Two dedicated raw helpers mirror its auth + 401-retry
logic for binary bodies / non-JSON responses:

```python
def _raw_get_bytes(self, path) -> bytes:
    # GET with Bearer auth + one 401-relogin retry; returns resp.content (raw bytes)

def _raw_post_bytes(self, path, data) -> tuple[int, dict | None]:
    # POST octet-stream body with Bearer auth + one 401-relogin retry;
    # returns (status_code, parsed-json-or-None). Tolerates 201 and 202.
```

Public methods:

```python
def export_tree(self, extension="gramps") -> bytes:
    # GET /api/exporters/{extension}/file -> raw (gzip) bytes. Synchronous.

def import_file(self, data, extension="gramps", *,
                poll_interval=IMPORT_POLL_INTERVAL,
                stability_window=IMPORT_STABILITY_WINDOW,
                max_timeout=IMPORT_MAX_TIMEOUT,
                _sleep=time.sleep, _now=time.monotonic) -> dict:
    # before = self.object_counts()
    # self._raw_post_bytes(f"/api/importers/{extension}/file", data)   # 201 or 202; body ignored
    # poll object_counts until (total > before-total) AND stable for stability_window polls
    # on timeout without growth -> raise ImportTimeoutError(before, last)
    # return {"before", "after", "added": {k: after[k]-before.get(k,0) ...}}
```

**Completion contract (precise + testable):**
- `before = object_counts()`.
- POST the bytes; ignore the response body and any task id (works for `201` and `202`).
- Loop: `_sleep(poll_interval)`, read `cur = object_counts()`. Track consecutive-identical reads.
  `stability_window` = number of **confirmation** polls after a value is first seen (window=2 ⇒ a
  value observed on 3 consecutive reads is "stable").
- **Done** when `total(cur) > total(before)` **and** the value has been stable for `stability_window`
  polls → return `{before, after, added}`. `total(x)` = `sum(x.values())` over the `object_counts`
  dict; "stable" = the whole dict compared equal (`cur == prev`) across consecutive reads.
- **Timeout**: if `_now()` passes `deadline` without ever satisfying "grew and stable" →
  raise `ImportTimeoutError` carrying `before` and the last-seen counts.
- `_sleep`/`_now` are injectable so tests run deterministically with no real waiting.

Defaults (module constants): `IMPORT_POLL_INTERVAL = 2.0`, `IMPORT_STABILITY_WINDOW = 2`,
`IMPORT_MAX_TIMEOUT = 300`. New exception `ImportTimeoutError` in the client's error hierarchy.

### 4.2 `backup_store.py` — filesystem layer (new, no HTTP / no MCP)

Pure, independently unit-testable. Responsibilities: resolve/validate paths inside `GRAMPS_BACKUP_DIR`,
enforce a path-traversal guard, read/write bytes.

```python
def resolve_export_path(backup_dir, filename=None, extension="gramps") -> str:
    # filename=None -> "gramps-export-<YYYYmmdd-HHMMSS>.<extension>"
    # returns an absolute path guaranteed to sit inside backup_dir

def resolve_import_path(backup_dir, filename) -> str:
    # returns absolute path inside backup_dir; raises if it escapes or does not exist

def write_bytes(path, data) -> None
def read_bytes(path) -> bytes
```

**Traversal guard**: resolve the candidate against `realpath(backup_dir)`; reject if the resolved
path is not within it (blocks `..`, absolute paths, symlink escapes). Reject empty/`/`-containing
filenames for export defaults.

### 4.3 `server.py` — MCP tool surface (thin glue)

Reads `GRAMPS_BACKUP_DIR` from env once in `create_server`. Two new tools, both **always registered**:

```python
@register
def gramps_export_tree(filename: str | None = None, extension: str = "gramps") -> dict:
    """Export the whole tree as a backup file into the server's backup directory.
    Returns {path, bytes, counts}. path is the container path (= mounted host path)."""
    path = backup_store.resolve_export_path(BACKUP_DIR, filename, extension)
    data = client.export_tree(extension)
    backup_store.write_bytes(path, data)
    return {"path": path, "bytes": len(data), "counts": client.object_counts()}

@register
def gramps_import_file(filename: str, extension: str = "gramps") -> dict:
    """Import a file from the server's backup directory into the tree (additive).
    Returns {before, after, added}. filename must live inside the backup directory."""
    path = backup_store.resolve_import_path(BACKUP_DIR, filename)
    data = backup_store.read_bytes(path)
    return client.import_file(data, extension)
```

If `GRAMPS_BACKUP_DIR` is unset, the tools raise a clear configuration error on use (not at import).

## 5. Deployment / Ops

- **docker / `.mcp.json`** (test workspace, not in this repo — document only): add
  `-v /home/julian-prentl/projects/gramps/data/export:/data` and `GRAMPS_BACKUP_DIR=/data` to the
  `docker run` args / env-file. The host dir is the already-established backup location.
- **`.env.example` + README**: document `GRAMPS_BACKUP_DIR` and the required volume mount, with the
  export→file / import←file workflow.
- **Automation user must be OWNER (4)** for import. `ops/setup-automation-user.sh` currently creates
  role 3 (EDITOR). **Parametrize the role** (default configurable) and add a README note that import
  needs role 4.

## 6. Tests (TDD)

- **`test_gramps_client.py`**:
  - `export_tree` GETs `/api/exporters/gramps/file`, returns the raw bytes.
  - `import_file` POSTs octet-stream to `/api/importers/gramps/file`; with a mocked `object_counts`
    sequence (growing → stable) returns `{before, after, added}`; asserts it **never** calls `/tasks/`.
  - `import_file` handles both a `201` and a `202` POST response.
  - `import_file` timeout (counts never grow) raises `ImportTimeoutError`; injected `_sleep`/`_now`
    keep the test instant.
- **`test_backup_store.py`** (new):
  - traversal guard rejects `../x`, absolute paths, symlink escapes; accepts a plain filename.
  - `resolve_export_path(None)` yields a timestamped name inside the dir.
  - `write_bytes`/`read_bytes` round-trip.
- **`test_server.py`**:
  - `gramps_export_tree` delegates (mock client + backup_store) and returns `{path, bytes, counts}`.
  - `gramps_import_file` delegates and returns before/after/added.
  - Missing `GRAMPS_BACKUP_DIR` → clear error on tool use.

Full suite green: `.venv/bin/python -m pytest -q` (currently 193 → grows with the new tests). `ruff` clean.

## 7. Delivery

One Welle-5 PR against `main` (export is trivial; import carries the polling logic). Tag/release
(`v0.3.1` or `v0.4.0`) is a release-time decision. Update `PROGRESS.md` (Welle 5 → done) and
`docs/blog-crud.md`-style docs as needed.

## 8. Out of scope (explicit)

- Wipe / `delete_all_objects` (Welle 6).
- Export filter options (`living`, `private`, person/event/note filters).
- Base64 transport, `POST`-export async variant, explicit reindex trigger (import auto-reindexes).
