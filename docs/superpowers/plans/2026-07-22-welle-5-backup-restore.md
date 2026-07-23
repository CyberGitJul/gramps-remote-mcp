# Welle 5 — Backup/Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two MCP tools — `gramps_export_tree` (full-tree backup) and `gramps_import_file` (additive restore/merge) — that move files through a mounted backup directory.

**Architecture:** Three isolated units. `backup_store.py` (new, pure filesystem: path resolution + traversal guard + read/write). `gramps_client.py` gains a REST layer (`export_tree` returns raw bytes; `import_file` POSTs octet-stream then confirms completion by polling `object_counts`, never `/api/tasks/`). `server.py` wires them into two thin, always-registered tools reading `GRAMPS_BACKUP_DIR` from env.

**Tech Stack:** Python 3.12, `requests` (already a dep), `mcp.server.fastmcp`, pytest. New code uses stdlib only (`os`, `datetime`, `time`).

## Global Constraints

- **Python 3.12**, no new runtime dependencies (stdlib only: `os`, `datetime`, `time`).
- **Do NOT modify `GrampsClient._request`** — add dedicated raw helpers instead.
- **Completion is derived from `object_counts` (`GET /api/metadata/`), never `/api/tasks/`.**
- Tests run with `.venv/bin/python -m pytest -q`; the full suite (currently 193) must stay green.
- `ruff` must stay clean (`.venv/bin/python -m ruff check .`); a pre-commit hook runs ruff on Python files.
- `create_server(client)` returns `(mcp, tools)`; tools are called as `tools["gramps_<name>"](...)`.
- Commit style: `type: subject` (`feat:`, `test:`, `docs:`). Frequent, per-task commits. Work on branch `feat/welle-5-backup-restore`.
- Export is read-only and always registered; import is always registered (REST API enforces OWNER).

---

### Task 1: `backup_store.py` — filesystem module

**Files:**
- Create: `backup_store.py`
- Test: `tests/test_backup_store.py`

**Interfaces:**
- Consumes: nothing (pure stdlib).
- Produces:
  - `resolve_export_path(backup_dir: str, filename: str | None = None, extension: str = "gramps") -> str`
  - `resolve_import_path(backup_dir: str, filename: str) -> str`
  - `write_bytes(path: str, data: bytes) -> None`
  - `read_bytes(path: str) -> bytes`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backup_store.py`:

```python
import os
import re

import pytest

import backup_store


def test_resolve_export_path_default_is_timestamped(tmp_path):
    path = backup_store.resolve_export_path(str(tmp_path))
    assert os.path.dirname(path) == os.path.realpath(str(tmp_path))
    assert re.match(r"gramps-export-\d{8}-\d{6}\.gramps$", os.path.basename(path))


def test_resolve_export_path_custom_filename(tmp_path):
    path = backup_store.resolve_export_path(str(tmp_path), "my-backup.gramps")
    assert path == os.path.realpath(os.path.join(str(tmp_path), "my-backup.gramps"))


def test_resolve_export_path_rejects_traversal(tmp_path):
    with pytest.raises(ValueError):
        backup_store.resolve_export_path(str(tmp_path), "../evil.gramps")


def test_resolve_import_path_rejects_absolute_escape(tmp_path):
    with pytest.raises(ValueError):
        backup_store.resolve_import_path(str(tmp_path), "/etc/passwd")


def test_resolve_import_path_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_store.resolve_import_path(str(tmp_path), "nope.gramps")


def test_resolve_import_path_accepts_valid(tmp_path):
    p = tmp_path / "good.gramps"
    p.write_bytes(b"x")
    path = backup_store.resolve_import_path(str(tmp_path), "good.gramps")
    assert path == os.path.realpath(str(p))


def test_read_write_roundtrip(tmp_path):
    path = os.path.join(str(tmp_path), "x.gramps")
    backup_store.write_bytes(path, b"GRAMPSDATA")
    assert backup_store.read_bytes(path) == b"GRAMPSDATA"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_backup_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backup_store'`.

- [ ] **Step 3: Write minimal implementation**

Create `backup_store.py`:

```python
import os
from datetime import datetime


def _safe_join(backup_dir, filename):
    """Resolve `filename` inside `backup_dir`, refusing any path escape."""
    if not filename or "\x00" in filename:
        raise ValueError(f"Invalid filename: {filename!r}")
    base = os.path.realpath(backup_dir)
    candidate = os.path.realpath(os.path.join(base, filename))
    if candidate != base and not candidate.startswith(base + os.sep):
        raise ValueError(f"Path escapes backup directory: {filename!r}")
    return candidate


def resolve_export_path(backup_dir, filename=None, extension="gramps"):
    """Absolute path for an export inside backup_dir. Default name is timestamped."""
    if filename is None:
        filename = f"gramps-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.{extension}"
    return _safe_join(backup_dir, filename)


def resolve_import_path(backup_dir, filename):
    """Absolute path for an existing import file inside backup_dir."""
    path = _safe_join(backup_dir, filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Import file not found in backup directory: {filename!r}")
    return path


def write_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_backup_store.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add backup_store.py tests/test_backup_store.py
git commit -m "feat: add backup_store filesystem module with traversal guard"
```

---

### Task 2: Client raw helpers + `export_tree`

**Files:**
- Modify: `gramps_client.py` (add constants after the error classes ~line 50; add `import time` at top ~line 2; add methods after `_request` ~line 190)
- Test: `tests/test_gramps_client.py` (append)

**Interfaces:**
- Consumes: `GrampsClient._login`, `self._access_token`, `self.base_url`.
- Produces:
  - `GrampsClient._raw_get_bytes(path: str) -> bytes`
  - `GrampsClient._raw_post_bytes(path: str, data: bytes) -> tuple[int, dict | None]`
  - `GrampsClient.export_tree(extension: str = "gramps") -> bytes`
  - module constants `EXPORT_TIMEOUT`, `IMPORT_HTTP_TIMEOUT` (both `300`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gramps_client.py`:

```python
from gramps_client import EXPORT_TIMEOUT, IMPORT_HTTP_TIMEOUT


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_export_tree_returns_raw_bytes(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok"})
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b"\x1f\x8bGRAMPS"
    resp.raise_for_status.return_value = None
    mock_request.return_value = resp
    client = GrampsClient("https://example.test", "bot", "secret")

    data = client.export_tree()

    assert data == b"\x1f\x8bGRAMPS"
    mock_request.assert_called_once_with(
        "GET",
        "https://example.test/api/exporters/gramps/file",
        headers={"Authorization": "Bearer tok"},
        timeout=EXPORT_TIMEOUT,
    )


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_raw_post_bytes_sends_octet_stream(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok"})
    mock_request.return_value = make_response({"task": {"id": "t1"}}, 202)
    client = GrampsClient("https://example.test", "bot", "secret")

    status, body = client._raw_post_bytes("/api/importers/gramps/file", b"DATA")

    assert status == 202
    assert body == {"task": {"id": "t1"}}
    mock_request.assert_called_once_with(
        "POST",
        "https://example.test/api/importers/gramps/file",
        data=b"DATA",
        headers={"Authorization": "Bearer tok", "Content-Type": "application/octet-stream"},
        timeout=IMPORT_HTTP_TIMEOUT,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gramps_client.py -k "export_tree or raw_post_bytes" -v`
Expected: FAIL — `ImportError: cannot import name 'EXPORT_TIMEOUT'` / `AttributeError: ... 'export_tree'`.

- [ ] **Step 3: Write minimal implementation**

In `gramps_client.py`, add to the imports at the top (after `import copy` / `import unicodedata`):

```python
import time
```

Add constants right after the last error class (after `class FamilyDeleteCountMismatchError` ~line 50):

```python
EXPORT_TIMEOUT = 300
IMPORT_HTTP_TIMEOUT = 300
```

Add these methods to `GrampsClient` immediately after `_request` (after ~line 190):

```python
    def _raw_get_bytes(self, path):
        """GET raw bytes (binary download), mirroring _request's 401-relogin retry."""
        if self._access_token is None:
            self._login()
        headers = {"Authorization": f"Bearer {self._access_token}"}
        resp = requests.request(
            "GET", f"{self.base_url}{path}", headers=headers, timeout=EXPORT_TIMEOUT
        )
        if resp.status_code == 401:
            self._login()
            headers = {"Authorization": f"Bearer {self._access_token}"}
            resp = requests.request(
                "GET", f"{self.base_url}{path}", headers=headers, timeout=EXPORT_TIMEOUT
            )
        resp.raise_for_status()
        return resp.content

    def _raw_post_bytes(self, path, data):
        """POST a raw octet-stream body; returns (status_code, json-or-None). 201 and 202 both ok."""
        if self._access_token is None:
            self._login()
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/octet-stream",
        }
        resp = requests.request(
            "POST", f"{self.base_url}{path}", data=data, headers=headers,
            timeout=IMPORT_HTTP_TIMEOUT,
        )
        if resp.status_code == 401:
            self._login()
            headers["Authorization"] = f"Bearer {self._access_token}"
            resp = requests.request(
                "POST", f"{self.base_url}{path}", data=data, headers=headers,
                timeout=IMPORT_HTTP_TIMEOUT,
            )
        resp.raise_for_status()
        return resp.status_code, (resp.json() if resp.content else None)

    def export_tree(self, extension="gramps"):
        """Download the whole tree as raw (gzip) bytes. GET /api/exporters/{ext}/file (synchronous)."""
        return self._raw_get_bytes(f"/api/exporters/{extension}/file")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gramps_client.py -k "export_tree or raw_post_bytes" -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add gramps_client.py tests/test_gramps_client.py
git commit -m "feat: add binary export_tree + raw HTTP helpers to GrampsClient"
```

---

### Task 3: Client `import_file` + `ImportTimeoutError` + counts-based completion

**Files:**
- Modify: `gramps_client.py` (add `ImportTimeoutError` after the error classes; add polling constants; add `import_file` after `export_tree`)
- Test: `tests/test_gramps_client.py` (append)

**Interfaces:**
- Consumes: `self.object_counts()` (returns a `dict` of counts), `self._raw_post_bytes` (Task 2).
- Produces:
  - `class ImportTimeoutError(Exception)`
  - `GrampsClient.import_file(data: bytes, extension: str = "gramps", *, poll_interval=2.0, stability_window=2, max_timeout=300, _sleep=time.sleep, _now=time.monotonic) -> dict` returning `{"before", "after", "added"}`
  - module constants `IMPORT_POLL_INTERVAL = 2.0`, `IMPORT_STABILITY_WINDOW = 2`, `IMPORT_MAX_TIMEOUT = 300`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gramps_client.py`:

```python
from gramps_client import ImportTimeoutError


def _metadata(counts):
    return make_response({"object_counts": counts})


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_import_file_polls_counts_until_stable(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok"})
    before = {"people": 10, "families": 4}
    after = {"people": 12, "families": 5}
    mock_request.side_effect = [
        _metadata(before),                           # before counts
        make_response({"task": {"id": "t1"}}, 202),  # import POST (async 202)
        _metadata(after),                            # poll 1 (first sighting)
        _metadata(after),                            # poll 2 (confirm)
        _metadata(after),                            # poll 3 (stable -> done)
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.import_file(
        b"DATA", stability_window=2, poll_interval=0, _sleep=lambda s: None
    )

    assert result["before"] == before
    assert result["after"] == after
    assert result["added"] == {"people": 2, "families": 1}
    # POST went to the importers endpoint with the raw bytes...
    post_call = mock_request.call_args_list[1]
    assert post_call.args[0] == "POST"
    assert post_call.args[1] == "https://example.test/api/importers/gramps/file"
    assert post_call.kwargs["data"] == b"DATA"
    # ...and completion was NEVER derived from /api/tasks/
    assert all("/api/tasks/" not in call.args[1] for call in mock_request.call_args_list)


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_import_file_handles_sync_201(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok"})
    before = {"people": 10}
    after = {"people": 11}
    mock_request.side_effect = [
        _metadata(before),                # before
        make_response(None, 201),         # sync import, empty body
        _metadata(after),                 # poll 1
        _metadata(after),                 # poll 2
        _metadata(after),                 # poll 3 -> stable
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.import_file(
        b"DATA", stability_window=2, poll_interval=0, _sleep=lambda s: None
    )

    assert result["added"] == {"people": 1}


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_import_file_timeout_raises(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok"})
    before = {"people": 10}
    mock_request.side_effect = [
        _metadata(before),          # before
        make_response(None, 201),   # import
        _metadata(before),          # poll: no growth
        _metadata(before),
        _metadata(before),
    ]
    clock = iter([0.0, 0.0, 1.0, 2.0, 999.0])
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(ImportTimeoutError):
        client.import_file(
            b"DATA", max_timeout=10, poll_interval=0,
            _sleep=lambda s: None, _now=lambda: next(clock),
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gramps_client.py -k import_file -v`
Expected: FAIL — `ImportError: cannot import name 'ImportTimeoutError'`.

- [ ] **Step 3: Write minimal implementation**

In `gramps_client.py`, add the error class after `class FamilyDeleteCountMismatchError` (before or alongside the timeout constants):

```python
class ImportTimeoutError(Exception):
    pass
```

Add the polling constants next to `EXPORT_TIMEOUT` / `IMPORT_HTTP_TIMEOUT`:

```python
IMPORT_POLL_INTERVAL = 2.0
IMPORT_STABILITY_WINDOW = 2
IMPORT_MAX_TIMEOUT = 300
```

Add this method to `GrampsClient` right after `export_tree`:

```python
    def import_file(
        self,
        data,
        extension="gramps",
        *,
        poll_interval=IMPORT_POLL_INTERVAL,
        stability_window=IMPORT_STABILITY_WINDOW,
        max_timeout=IMPORT_MAX_TIMEOUT,
        _sleep=time.sleep,
        _now=time.monotonic,
    ):
        """Import a file into the tree (additive). POST octet-stream, confirm via object_counts.

        Completion is detected from object_counts (GET /api/metadata/), never from
        /api/tasks/ (unreliable: TTL-reaped). Done once the total count has grown
        beyond `before` AND the counts are identical for `stability_window`
        consecutive confirmation polls. Works for sync (201) and async (202) alike.
        """
        before = self.object_counts()
        before_total = sum(before.values())
        self._raw_post_bytes(f"/api/importers/{extension}/file", data)

        deadline = _now() + max_timeout
        prev = None
        stable = 0
        last = before
        while _now() < deadline:
            _sleep(poll_interval)
            cur = self.object_counts()
            last = cur
            if cur == prev:
                stable += 1
            else:
                stable = 0
                prev = cur
            if sum(cur.values()) > before_total and stable >= stability_window:
                return {
                    "before": before,
                    "after": cur,
                    "added": {k: cur.get(k, 0) - before.get(k, 0) for k in cur},
                }
        raise ImportTimeoutError(
            f"Import did not stabilize within {max_timeout}s; before={before}, last={last}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gramps_client.py -k import_file -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add gramps_client.py tests/test_gramps_client.py
git commit -m "feat: add import_file with counts-based completion polling"
```

---

### Task 4: Server tools `gramps_export_tree` / `gramps_import_file`

**Files:**
- Modify: `server.py` (add `import backup_store` at top; add `backup_dir=None` param to `create_server`; register two tools in the always-on section, before the `if enable_destructive:` block ~line 269)
- Test: `tests/test_server.py` (add `import pytest`; append tests)

**Interfaces:**
- Consumes: `backup_store.resolve_export_path/resolve_import_path/read_bytes/write_bytes` (Task 1); `client.export_tree` (Task 2), `client.import_file` (Task 3), `client.object_counts`.
- Produces:
  - `create_server(client, enable_destructive=None, backup_dir=None) -> (mcp, tools)`
  - tool `gramps_export_tree(filename: str | None = None, extension: str = "gramps") -> dict` → `{path, bytes, counts}`
  - tool `gramps_import_file(filename: str, extension: str = "gramps") -> dict` → `{before, after, added}`

- [ ] **Step 1: Write the failing tests**

In `tests/test_server.py`, add `import pytest` to the imports at the top, then append:

```python
def test_gramps_export_tree_writes_file_and_returns_summary(tmp_path):
    client = MagicMock()
    client.export_tree.return_value = b"GRAMPSDATA"
    client.object_counts.return_value = {"people": 10}
    _, tools = create_server(client, backup_dir=str(tmp_path))

    result = tools["gramps_export_tree"]("backup.gramps")

    client.export_tree.assert_called_once_with("gramps")
    assert result["path"] == os.path.realpath(os.path.join(str(tmp_path), "backup.gramps"))
    assert result["bytes"] == len(b"GRAMPSDATA")
    assert result["counts"] == {"people": 10}
    with open(result["path"], "rb") as f:
        assert f.read() == b"GRAMPSDATA"


def test_gramps_import_file_delegates(tmp_path):
    src = tmp_path / "restore.gramps"
    src.write_bytes(b"RESTOREDATA")
    client = MagicMock()
    client.import_file.return_value = {"before": {}, "after": {}, "added": {}}
    _, tools = create_server(client, backup_dir=str(tmp_path))

    result = tools["gramps_import_file"]("restore.gramps")

    client.import_file.assert_called_once_with(b"RESTOREDATA", "gramps")
    assert result == {"before": {}, "after": {}, "added": {}}


def test_gramps_export_tree_without_backup_dir_errors(monkeypatch):
    monkeypatch.delenv("GRAMPS_BACKUP_DIR", raising=False)
    client = MagicMock()
    _, tools = create_server(client)

    with pytest.raises(RuntimeError):
        tools["gramps_export_tree"]()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_server.py -k "export_tree or import_file" -v`
Expected: FAIL — `KeyError: 'gramps_export_tree'` (tool not registered) / `TypeError` on `backup_dir`.

- [ ] **Step 3: Write minimal implementation**

In `server.py`, add the import near the top (after `from gramps_client import GrampsClient`):

```python
import backup_store
```

Change the `create_server` signature and add the backup-dir resolution. Replace the signature line and add resolution right after the `enable_destructive` normalization block (before `mcp = FastMCP(...)`):

```python
def create_server(client, enable_destructive=None, backup_dir=None):
```

```python
    if backup_dir is None:
        backup_dir = os.environ.get("GRAMPS_BACKUP_DIR")
```

Register both tools in the always-on section, immediately before the `if enable_destructive:` block (~line 269):

```python
    @register
    def gramps_export_tree(filename: str | None = None, extension: str = "gramps") -> dict:
        """Export the whole tree as a backup file in the server's backup directory.

        Writes a .gramps (gzip XML) backup and returns {path, bytes, counts}. `path`
        is the container path, which maps to the mounted host directory. Requires
        GRAMPS_BACKUP_DIR to be configured.
        """
        if not backup_dir:
            raise RuntimeError("GRAMPS_BACKUP_DIR is not configured; cannot export.")
        path = backup_store.resolve_export_path(backup_dir, filename, extension)
        data = client.export_tree(extension)
        backup_store.write_bytes(path, data)
        return {"path": path, "bytes": len(data), "counts": client.object_counts()}

    @register
    def gramps_import_file(filename: str, extension: str = "gramps") -> dict:
        """Import a file from the server's backup directory into the tree (additive).

        Reads `filename` from GRAMPS_BACKUP_DIR and imports it, confirming completion
        via object_counts. Returns {before, after, added}. The account must have OWNER
        role. Requires GRAMPS_BACKUP_DIR to be configured.
        """
        if not backup_dir:
            raise RuntimeError("GRAMPS_BACKUP_DIR is not configured; cannot import.")
        path = backup_store.resolve_import_path(backup_dir, filename)
        data = backup_store.read_bytes(path)
        return client.import_file(data, extension)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_server.py -k "export_tree or import_file" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full suite + ruff**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check .`
Expected: all green (193 + 12 new = 205), ruff clean.

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: register gramps_export_tree and gramps_import_file MCP tools"
```

---

### Task 5: Ops role parametrization + docs

**Files:**
- Modify: `ops/setup-automation-user.sh`
- Modify: `.env.example`
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing (shell + docs).
- Produces: a `GRAMPS_ROLE`-parametrized setup script; documented `GRAMPS_BACKUP_DIR` + volume mount + OWNER-role requirement.

This task has no unit test (shell script + docs). Verify by re-reading the diff and, if a container is available, a manual run.

- [ ] **Step 1: Parametrize the role in `ops/setup-automation-user.sh`**

Change the header comment on line 2 from `EDITOR / role=3` to note it is configurable:

```bash
# One-time creation of a persistent, role-restricted automation user for the
# Gramps Remote MCP server (default EDITOR / role=3; set GRAMPS_ROLE=4 for OWNER,
# required by the import/backup tools). Run this ONCE, directly on the host that
```

After the `USERNAME="${1:-mcp-automation}"` line (line 15), add:

```bash
# Role for the automation user: 3=EDITOR (default), 4=OWNER (needed for
# gramps_import_file / batch delete). See README "Backup / Restore".
GRAMPS_ROLE="${GRAMPS_ROLE:-3}"
```

In the embedded Python helper, change the role read + the CLI call. Replace:

```python
result = subprocess.run(
    ["python3", "-m", "gramps_webapi", "user", "add", username, password,
     "--role", "3", "--tree", tree_id, "--fullname", "MCP Automation"],
    capture_output=True, text=True,
)
```

with:

```python
role = sys.argv[2]
result = subprocess.run(
    ["python3", "-m", "gramps_webapi", "user", "add", username, password,
     "--role", role, "--tree", tree_id, "--fullname", "MCP Automation"],
    capture_output=True, text=True,
)
```

Change the final `docker exec` line to pass the role, and the echo above it. Replace:

```bash
echo "Creating persistent automation user (role EDITOR=3) -- credentials are shown only once:"
docker exec "$GRAMPS_CONTAINER" sh -c \
  "export SECRET_KEY=\"\$(cat /app/secret/secret)\"; python3 '$helper_remote' '$username_remote'"
```

with:

```bash
echo "Creating persistent automation user (role=$GRAMPS_ROLE) -- credentials are shown only once:"
docker exec "$GRAMPS_CONTAINER" sh -c \
  "export SECRET_KEY=\"\$(cat /app/secret/secret)\"; python3 '$helper_remote' '$username_remote' '$GRAMPS_ROLE'"
```

- [ ] **Step 2: Verify the script parses**

Run: `bash -n ops/setup-automation-user.sh`
Expected: no output (syntax OK).

- [ ] **Step 3: Document `GRAMPS_BACKUP_DIR` in `.env.example`**

Append to `.env.example`:

```bash

# Backup directory (inside the container) for the backup/restore tools.
# gramps_export_tree writes backups here; gramps_import_file reads files from here.
# Mount a host directory to this path and set GRAMPS_BACKUP_DIR to it, e.g.
#   docker run ... -v /path/on/host/export:/data ... and GRAMPS_BACKUP_DIR=/data
# Import additionally requires the account to have OWNER role (see README).
# GRAMPS_BACKUP_DIR=/data
```

- [ ] **Step 4: Document the tools in `README.md`**

Add a "Backup / Restore" subsection to the tools/configuration area of `README.md`:

```markdown
### Backup / Restore

Two tools move whole-tree files through a mounted backup directory:

- `gramps_export_tree(filename=None, extension="gramps")` — writes a `.gramps`
  (gzip XML) backup into the backup directory; returns `{path, bytes, counts}`.
  Read-only, always available.
- `gramps_import_file(filename, extension="gramps")` — imports a file from the
  backup directory into the tree (**additive** — Gramps import never merges, it
  stacks). Returns `{before, after, added}`. **Requires the account to have OWNER
  role** (`GRAMPS_ROLE=4` when running `ops/setup-automation-user.sh`).

Set `GRAMPS_BACKUP_DIR` to a directory inside the container and mount a host
directory there so files survive and are reachable from the host:

    docker run --rm -i \
      --env-file .env \
      -v /home/you/gramps/export:/data \
      -e GRAMPS_BACKUP_DIR=/data \
      gramps-remote-mcp

Export writes to that directory; for import, drop the file into the host
directory first, then call `gramps_import_file("your-file.gramps")`. Completion
of an import is confirmed by polling object counts (never the task endpoint), so
it works on both synchronous and Celery-backed Gramps Web deployments.
```

- [ ] **Step 5: Commit**

```bash
git add ops/setup-automation-user.sh .env.example README.md
git commit -m "docs: document backup/restore tools, GRAMPS_BACKUP_DIR, OWNER role"
```

---

### Task 6: Verification & branch wrap-up

**Files:** none (verification + handoff).

- [ ] **Step 1: Full suite green + ruff clean**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check .`
Expected: all pass (205 tests), `All checks passed!`.

- [ ] **Step 2: Confirm tool count with the destructive gate off and on**

Run: `.venv/bin/python -c "from unittest.mock import MagicMock; from server import create_server; print('off', len(create_server(MagicMock(), enable_destructive=False)[1])); print('on', len(create_server(MagicMock(), enable_destructive=True)[1]))"`
Expected: `off 27`, `on 30` (25 prior non-destructive + export + import = 27; +3 destructive = 30).

- [ ] **Step 3: Update `PROGRESS.md`** (gitignored, not committed)

Mark Welle 5 done, update the Stand/Roadmap/Protokoll sections with the current timestamp (`date '+%Y-%m-%d %H:%M:%S %Z'`), and point Next Action at Welle 6.

- [ ] **Step 4: Finish the branch**

Use the `superpowers:finishing-a-development-branch` skill to open a PR against `main` (branch protection blocks direct pushes). Release tag (`v0.3.1` / `v0.4.0`) is a release-time decision.

---

## Self-Review

**Spec coverage:**
- §2 Transport (mounted dir + paths) → Tasks 1 (paths), 4 (server reads `GRAMPS_BACKUP_DIR`), 5 (docs/mount). ✅
- §2 Async completion (counts-based) → Task 3 (`import_file` polling, never `/tasks/`). ✅
- §2 Import always registered → Task 4 (registered outside the `enable_destructive` block). ✅
- §2 Export full-backup only → Task 2 (`export_tree` has no filter args). ✅
- §4.1 `_request` untouched, raw helpers → Task 2. ✅
- §4.2 `backup_store.py` + traversal guard → Task 1. ✅
- §4.3 server thin glue + missing-dir error → Task 4. ✅
- §5 ops OWNER role + `.env.example` + README → Task 5. ✅
- §6 tests (client/backup_store/server) → Tasks 1–4. ✅

**Placeholder scan:** no TBD/TODO; every code step shows complete code. ✅

**Type consistency:** `resolve_export_path`/`resolve_import_path`/`read_bytes`/`write_bytes` names match across Tasks 1 & 4; `export_tree(extension)` / `import_file(data, extension)` signatures match Tasks 2/3 usage in Task 4; `create_server(..., backup_dir=None)` matches test calls; `ImportTimeoutError` defined in Task 3 and imported in its tests. ✅
