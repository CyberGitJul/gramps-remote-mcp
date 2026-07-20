# Welle 4 — Blog-CRUD + Name-field tools (G12/G13) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 8 MCP tools to the Gramps Web server — 5 blog-post CRUD tools and 3 person-name-editing tools — following the existing thin-tool / guarded-write patterns.

**Architecture:** Blog logic lives in a new `gramps_blog.py` (`class BlogMixin`) that `GrampsClient` inherits, sharing existing helpers (`_request`, `_create_object`, `_get_or_create_tag`, `_delete_orphaned_notes`, `_create_note`). Name-field tools are plain `GrampsClient` methods next to `set_surname`/`add_birth_name`. Every MCP tool is a thin wrapper `return client.<method>(...)`; the destructive `gramps_delete_blog_post` sits inside the existing `if enable_destructive:` block. Body format (plain text vs HTML) is a per-deployment choice via `GRAMPS_BLOG_BODY_FORMAT`, stored on the client.

**Tech Stack:** Python 3, `requests`, FastMCP, `pytest` (unit tests mock `gramps_client.requests`).

## Global Constraints

- Body format is chosen via env var **`GRAMPS_BLOG_BODY_FORMAT`**: value exactly `"html"` → HTML mode; anything else (unset, `"text"`, `"0"`, garbage) → **plain text** mode. Fail-safe: only the exact string `"html"` enables HTML. Default is plain text.
- The HTML-mode note payload MUST use the full type object **`{"_class": "NoteType", "value": 24, "string": ""}`** (24 = `NoteType.HTML_CODE`). Never send the plaintext type string (case-sensitive `"Html code"` — silent fallback to CUSTOM on any mismatch). Verified against gramps core `notetype.py` + gramps-web-api POST pipeline.
- `gramps_delete_blog_post` is destructive: registered ONLY when the server is started with `GRAMPS_ENABLE_DESTRUCTIVE=1` (or `enable_destructive=True`); requires `confirm is True` (strict literal).
- Public identifier everywhere is the **`gramps_id`** (source `S...`, person `I...`); handles stay internal.
- `PUT` is a full replace, not a merge → always Read-Modify-Write (fetch the full object, mutate, PUT it back). Never send `If-Match`.
- All name-field tools are non-destructive: they go through `_guarded_write` (fetch → snapshot → mutate → PUT → count-guard) and return `{gramps_id, before, after}`.
- Baseline is **146 tests green**; every task keeps the suite green. Run: `.venv/bin/python -m pytest -q`.
- Blog error classes live in `gramps_blog.py` (NOT `gramps_client.py`) to avoid a circular import, since `gramps_client` imports `BlogMixin` from `gramps_blog`.

## File Structure

- **Create `gramps_blog.py`** — `BlogMixin` + blog error classes (`BlogPostNotFoundError`, `BlogPostCreateCountMismatchError`, `BlogPostDeleteCountMismatchError`); blog CRUD methods + `_create_body_note`, `count_sources`, `_get_blog_source`.
- **Modify `gramps_client.py`** — `__init__` gains `blog_body_format`; `class GrampsClient(BlogMixin)`; add `_first_name_mutation`, `set_first_name`, `add_alternate_name` (generalized), `add_birth_name` (now an alias), `swap_primary_name`.
- **Modify `server.py`** — register 4 blog tools + 3 name tools; add `gramps_delete_blog_post` to the destructive block; `main()` passes `blog_body_format` from env.
- **Create `tests/test_gramps_blog.py`** — blog client unit tests.
- **Modify `tests/test_gramps_client.py`** — name-field client tests.
- **Modify `tests/test_server.py`** — new-tool registration/delegation tests.
- **Modify** `README.md`, `.env.example`, `docs/blog-crud.md`, `HANDOFF-new-tools.md` — docs.

---

### Task 1: Client body-format config + `main()` env wiring

**Files:**
- Modify: `gramps_client.py:143-147` (`GrampsClient.__init__`)
- Modify: `server.py:228-235` (`main`)
- Test: `tests/test_gramps_client.py`

**Interfaces:**
- Produces: `GrampsClient(base_url, username, password, blog_body_format=None)`; attribute `self.blog_body_format` is `"html"` iff the arg is exactly `"html"`, else `"text"`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_gramps_client.py`)

```python
import pytest


@pytest.mark.parametrize(
    "raw,expected",
    [("html", "html"), ("text", "text"), ("0", "text"), (None, "text"), ("HTML", "text"), ("garbage", "text")],
)
def test_blog_body_format_normalized_failsafe(raw, expected):
    client = GrampsClient("https://example.test", "bot", "secret", blog_body_format=raw)
    assert client.blog_body_format == expected


def test_blog_body_format_defaults_to_text():
    client = GrampsClient("https://example.test", "bot", "secret")
    assert client.blog_body_format == "text"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gramps_client.py -k blog_body_format -v`
Expected: FAIL (`__init__() got an unexpected keyword argument 'blog_body_format'`)

- [ ] **Step 3: Implement** — edit `GrampsClient.__init__`:

```python
    def __init__(self, base_url, username, password, blog_body_format=None):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._access_token = None
        # Fail-safe (like GRAMPS_ENABLE_DESTRUCTIVE): only the exact string "html"
        # enables HTML bodies; anything else falls back to safe plain text.
        self.blog_body_format = "html" if blog_body_format == "html" else "text"
```

- [ ] **Step 4: Wire `main()`** — edit `server.py` `main()` to pass the env var:

```python
def main():
    client = GrampsClient(
        base_url=os.environ["GRAMPS_BASE_URL"],
        username=os.environ["GRAMPS_USERNAME"],
        password=os.environ["GRAMPS_PASSWORD"],
        blog_body_format=os.environ.get("GRAMPS_BLOG_BODY_FORMAT"),
    )
    mcp, _ = create_server(client)
    mcp.run(transport="stdio")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gramps_client.py -k blog_body_format -v`
Expected: PASS (7 cases)

- [ ] **Step 6: Commit**

```bash
git add gramps_client.py server.py tests/test_gramps_client.py
git commit -m "feat: add blog_body_format client config (fail-safe, env-wired)"
```

---

### Task 2: `gramps_blog.py` scaffold — errors, `BlogMixin`, `_create_body_note`, `count_sources`

**Files:**
- Create: `gramps_blog.py`
- Modify: `gramps_client.py:4` (import) and `gramps_client.py:142` (`class GrampsClient(BlogMixin)`)
- Test: `tests/test_gramps_blog.py`

**Interfaces:**
- Produces: `BlogMixin._create_body_note(body) -> handle` (HTML_CODE note in html mode, General note in text mode); `BlogMixin.count_sources() -> int`; error classes `BlogPostNotFoundError`, `BlogPostCreateCountMismatchError`, `BlogPostDeleteCountMismatchError`.
- Consumes: `self._request`, `self._create_object`, `self._create_note`, `self.blog_body_format` (from `GrampsClient`).

- [ ] **Step 1: Write the failing test** (`tests/test_gramps_blog.py`, new file)

```python
from unittest.mock import MagicMock

import pytest

from gramps_client import GrampsClient


# Blog tests mock client._request DIRECTLY (returning raw parsed JSON), not the
# requests layer: blog logic is about composing Source/Note/Tag calls, and the
# HTTP/login/_request plumbing is already covered by test_gramps_client.py. Each
# side_effect entry is exactly what the real _request returns (a list, dict, or
# None) for that call, in call order.
def make_client(blog_body_format="text"):
    client = GrampsClient("https://example.test", "bot", "secret", blog_body_format=blog_body_format)
    client._access_token = "tok"  # skip login; _request is mocked anyway
    return client


def test_create_body_note_text_mode_is_general_styledtext():
    client = make_client("text")
    client._request = MagicMock(return_value=[{"_class": "Note", "type": "add", "handle": "n1",
                                               "new": {"_class": "Note", "handle": "n1"}}])
    handle = client._create_body_note("hello")
    assert handle == "n1"
    method, path = client._request.call_args.args[0], client._request.call_args.args[1]
    body = client._request.call_args.kwargs["json_body"]
    assert (method, path) == ("POST", "/api/notes/")
    assert body["text"]["string"] == "hello"
    assert "type" not in body  # text mode: no explicit NoteType (server defaults to General)


def test_create_body_note_html_mode_sets_html_code_type():
    client = make_client("html")
    client._request = MagicMock(return_value=[{"_class": "Note", "type": "add", "handle": "n2",
                                               "new": {"_class": "Note", "handle": "n2"}}])
    handle = client._create_body_note("<p>hi</p>")
    assert handle == "n2"
    body = client._request.call_args.kwargs["json_body"]
    assert body["text"]["string"] == "<p>hi</p>"
    assert body["type"] == {"_class": "NoteType", "value": 24, "string": ""}


def test_count_sources_counts_the_list():
    client = make_client()
    client._request = MagicMock(return_value=[{"gramps_id": "S0001"}, {"gramps_id": "S0002"}])
    assert client.count_sources() == 2
    assert client._request.call_args.args == ("GET", "/api/sources/?keys=gramps_id")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gramps_blog.py -v`
Expected: FAIL (`ImportError` / `AttributeError: 'GrampsClient' object has no attribute '_create_body_note'`)

- [ ] **Step 3: Implement** — create `gramps_blog.py`:

```python
from urllib.parse import quote

import requests


class BlogPostNotFoundError(Exception):
    pass


class BlogPostCreateCountMismatchError(Exception):
    pass


class BlogPostDeleteCountMismatchError(Exception):
    pass


class BlogMixin:
    """Blog-post CRUD over the generic Source/Note/Tag REST endpoints.

    A blog post is a Source tagged 'Blog' whose body is the first Note in its
    note_list. Mixed into GrampsClient; uses its _request/_create_object/
    _create_note/_get_or_create_tag/_delete_orphaned_notes helpers and the
    self.blog_body_format setting.
    """

    def count_sources(self):
        return len(self._request("GET", "/api/sources/?keys=gramps_id"))

    def _create_body_note(self, body):
        """Create the body Note for a post; type depends on blog_body_format.

        html mode -> a NoteType.HTML_CODE (24) note whose raw string the server
        renders as sanitized HTML. text mode -> a plain General StyledText note
        (the existing _create_note). Returns the new note handle.
        """
        if self.blog_body_format == "html":
            note_dict = {
                "_class": "Note",
                "text": {"_class": "StyledText", "tags": [], "string": body},
                "type": {"_class": "NoteType", "value": 24, "string": ""},  # HTML_CODE
                "format": 0,
            }
            new_note = self._create_object("notes", note_dict)
            return new_note["handle"]
        return self._create_note(body)
```

- [ ] **Step 4: Wire the mixin** — edit `gramps_client.py`:

At the top with the other imports (after `import requests`):

```python
from gramps_blog import BlogMixin
```

Change the class declaration (line ~142):

```python
class GrampsClient(BlogMixin):
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gramps_blog.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Run full suite (no regressions)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (146 + new)

- [ ] **Step 7: Commit**

```bash
git add gramps_blog.py gramps_client.py tests/test_gramps_blog.py
git commit -m "feat: add BlogMixin scaffold (_create_body_note, count_sources, errors)"
```

---

### Task 3: `create_blog_post` + `gramps_create_blog_post`

**Files:**
- Modify: `gramps_blog.py` (add `create_blog_post`)
- Modify: `server.py` (register `gramps_create_blog_post`)
- Test: `tests/test_gramps_blog.py`, `tests/test_server.py`

**Interfaces:**
- Produces: `create_blog_post(title, body, author=None) -> gramps_id` (POSTs a Note then a Source tagged 'Blog'; count-guard sources+1).

- [ ] **Step 1: Write the failing test** (append to `tests/test_gramps_blog.py`)

```python
def test_create_blog_post_posts_note_then_tagged_source():
    client = make_client("text")
    client._request = MagicMock(side_effect=[
        [{"gramps_id": "S0001"}],                       # count_sources before
        [{"name": "Blog", "handle": "tagBlog"}],        # _find_tag_handle (tags list)
        [{"_class": "Note", "type": "add", "handle": "nH",
          "new": {"_class": "Note", "handle": "nH"}}],  # POST note
        [{"_class": "Source", "type": "add", "handle": "sH",
          "new": {"_class": "Source", "handle": "sH", "gramps_id": "S0002"}}],  # POST source
        [{"gramps_id": "S0001"}, {"gramps_id": "S0002"}],  # count_sources after
    ])

    gid = client.create_blog_post("My title", "Body text", author="Max")

    assert gid == "S0002"
    source_body = client._request.call_args_list[3].kwargs["json_body"]
    assert client._request.call_args_list[3].args[:2] == ("POST", "/api/sources/")
    assert source_body["title"] == "My title"
    assert source_body["author"] == "Max"
    assert source_body["note_list"] == ["nH"]
    assert source_body["tag_list"] == ["tagBlog"]


def test_create_blog_post_count_guard():
    client = make_client("text")
    client._request = MagicMock(side_effect=[
        [{"gramps_id": "S0001"}],                       # before = 1
        [{"name": "Blog", "handle": "tagBlog"}],
        [{"_class": "Note", "type": "add", "handle": "nH", "new": {"_class": "Note", "handle": "nH"}}],
        [{"_class": "Source", "type": "add", "handle": "sH", "new": {"_class": "Source", "handle": "sH", "gramps_id": "S0002"}}],
        [{"gramps_id": "S0001"}],                       # after = 1 (no increase!)
    ])
    from gramps_blog import BlogPostCreateCountMismatchError
    with pytest.raises(BlogPostCreateCountMismatchError):
        client.create_blog_post("t", "b")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gramps_blog.py -k create_blog_post -v`
Expected: FAIL (`AttributeError: ... 'create_blog_post'`)

- [ ] **Step 3: Implement** — add to `BlogMixin` (in `gramps_blog.py`):

```python
    def create_blog_post(self, title, body, author=None):
        """Create a blog post: a Source tagged 'Blog' with a body Note.

        The body's storage format follows blog_body_format (html/text). Guards
        that the source count rises by exactly one. Returns the new source's
        gramps_id.
        """
        count_before = self.count_sources()
        tag_handle = self._get_or_create_tag("Blog")
        note_handle = self._create_body_note(body)
        source_dict = {
            "_class": "Source",
            "title": title,
            "author": author or "",
            "note_list": [note_handle],
            "tag_list": [tag_handle],
        }
        new_source = self._create_object("sources", source_dict)
        count_after = self.count_sources()
        if count_after != count_before + 1:
            raise BlogPostCreateCountMismatchError(
                f"Source count did not rise by one: {count_before} -> {count_after}"
            )
        return new_source["gramps_id"]
```

- [ ] **Step 4: Register the tool** — in `server.py`, add after `gramps_get_relations` (before the `if enable_destructive:` block):

```python
    @register
    def gramps_create_blog_post(title: str, body: str, author: str | None = None) -> str:
        """Create a blog post (a Source tagged 'Blog' + a body note). Returns its Gramps ID.

        `body` is stored per the server's GRAMPS_BLOG_BODY_FORMAT: plain text
        (default) or HTML. Returns the new source gramps_id (e.g. 'S0002').
        """
        return client.create_blog_post(title, body, author)
```

- [ ] **Step 5: Write the server delegation test** (append to `tests/test_server.py`)

```python
def test_gramps_create_blog_post_calls_client():
    client = MagicMock()
    client.create_blog_post.return_value = "S0002"
    _, tools = create_server(client)

    result = tools["gramps_create_blog_post"]("My title", "Body", "Max")

    client.create_blog_post.assert_called_once_with("My title", "Body", "Max")
    assert result == "S0002"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gramps_blog.py tests/test_server.py -k "create_blog_post" -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add gramps_blog.py server.py tests/test_gramps_blog.py tests/test_server.py
git commit -m "feat: add gramps_create_blog_post (Source+Blog tag+body note)"
```

---

### Task 4: `list_blog_posts` + `gramps_list_blog_posts`

**Files:**
- Modify: `gramps_blog.py` (add `list_blog_posts`)
- Modify: `server.py` (register `gramps_list_blog_posts`)
- Test: `tests/test_gramps_blog.py`, `tests/test_server.py`

**Interfaces:**
- Produces: `list_blog_posts(page=None, pagesize=None) -> list` (HasTag 'Blog', `sort=-change`, slim keys).

- [ ] **Step 1: Write the failing test** (append to `tests/test_gramps_blog.py`)

```python
from urllib.parse import quote as _quote


def test_list_blog_posts_query_shape():
    client = make_client()
    client._request = MagicMock(return_value=[{"gramps_id": "S0002", "title": "t", "author": "a", "change": 5}])

    result = client.list_blog_posts()

    method, url = client._request.call_args.args[0], client._request.call_args.args[1]
    assert method == "GET"
    assert url.startswith("/api/sources/?")
    rules = '{"rules":[{"name":"HasTag","values":["Blog"]}]}'
    assert "rules=" + _quote(rules, safe="") in url
    assert "sort=-change" in url
    assert "keys=gramps_id,title,author,change" in url
    assert result == [{"gramps_id": "S0002", "title": "t", "author": "a", "change": 5}]


def test_list_blog_posts_defaults_page_when_only_pagesize():
    client = make_client()
    client._request = MagicMock(return_value=[])
    client.list_blog_posts(pagesize=10)
    url = client._request.call_args.args[1]
    assert "page=1" in url
    assert "pagesize=10" in url
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gramps_blog.py -k list_blog_posts -v`
Expected: FAIL (`AttributeError: ... 'list_blog_posts'`)

- [ ] **Step 3: Implement** — add to `BlogMixin`:

```python
    def list_blog_posts(self, page=None, pagesize=None):
        """List blog posts (Sources tagged 'Blog'), newest first.

        Returns a slim list [{gramps_id, title, author, change}]. `page` is
        1-based; a bare `pagesize` defaults page to 1 (gramps-web-api ignores
        pagesize when page < 1 and returns everything).
        """
        if page is None and pagesize is not None:
            page = 1
        rules = '{"rules":[{"name":"HasTag","values":["Blog"]}]}'
        params = [
            "rules=" + quote(rules, safe=""),
            "sort=-change",
            "keys=gramps_id,title,author,change",
        ]
        if page is not None:
            params.append(f"page={page}")
        if pagesize is not None:
            params.append(f"pagesize={pagesize}")
        return self._request("GET", "/api/sources/?" + "&".join(params))
```

- [ ] **Step 4: Register the tool** — in `server.py`, add near the other blog tool:

```python
    @register
    def gramps_list_blog_posts(page: int | None = None, pagesize: int | None = None) -> list:
        """List blog posts (Sources tagged 'Blog'), newest first.

        Returns [{gramps_id, title, author, change}]. page is 1-based; pagesize
        caps rows. Omit both to return all posts.
        """
        return client.list_blog_posts(page, pagesize)
```

- [ ] **Step 5: Server delegation test** (append to `tests/test_server.py`)

```python
def test_gramps_list_blog_posts_calls_client():
    client = MagicMock()
    client.list_blog_posts.return_value = [{"gramps_id": "S0002"}]
    _, tools = create_server(client)

    result = tools["gramps_list_blog_posts"](1, 20)

    client.list_blog_posts.assert_called_once_with(1, 20)
    assert result == [{"gramps_id": "S0002"}]


def test_gramps_list_blog_posts_defaults():
    client = MagicMock()
    client.list_blog_posts.return_value = []
    _, tools = create_server(client)

    tools["gramps_list_blog_posts"]()

    client.list_blog_posts.assert_called_once_with(None, None)
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_gramps_blog.py tests/test_server.py -k list_blog_posts -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add gramps_blog.py server.py tests/test_gramps_blog.py tests/test_server.py
git commit -m "feat: add gramps_list_blog_posts (HasTag Blog, sort -change)"
```

---

### Task 5: `get_blog_post` + `_get_blog_source` + `gramps_get_blog_post`

**Files:**
- Modify: `gramps_blog.py` (add `_get_blog_source`, `get_blog_post`)
- Modify: `server.py` (register `gramps_get_blog_post`)
- Test: `tests/test_gramps_blog.py`, `tests/test_server.py`

**Interfaces:**
- Produces: `_get_blog_source(gramps_id) -> source dict` (raises `BlogPostNotFoundError`); `get_blog_post(gramps_id) -> {gramps_id, title, author, change, body_html, body_text, note_gramps_id}`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_gramps_blog.py`)

```python
from gramps_blog import BlogPostNotFoundError


def test_get_blog_post_renders_body_html():
    client = make_client()
    client._request = MagicMock(side_effect=[
        [{"gramps_id": "S0002", "handle": "sH", "title": "T", "author": "A",
          "change": 99, "note_list": ["nH"]}],                 # source by gramps_id
        {"gramps_id": "N0001", "handle": "nH",
         "text": {"string": "<p>hi</p>"},
         "formatted": {"html": "<div><p>hi</p></div>"}},        # note formats=html
    ])

    post = client.get_blog_post("S0002")

    assert post == {
        "gramps_id": "S0002", "title": "T", "author": "A", "change": 99,
        "body_html": "<div><p>hi</p></div>", "body_text": "<p>hi</p>",
        "note_gramps_id": "N0001",
    }
    assert client._request.call_args_list[1].args[:2] == ("GET", "/api/notes/nH?formats=html")


def test_get_blog_post_empty_note_list_ok():
    client = make_client()
    client._request = MagicMock(side_effect=[
        [{"gramps_id": "S0002", "handle": "sH", "title": "T", "author": "A",
          "change": 99, "note_list": []}],
    ])
    post = client.get_blog_post("S0002")
    assert post["body_html"] is None and post["body_text"] is None and post["note_gramps_id"] is None


def test_get_blog_post_not_found_raises():
    client = make_client()
    client._request = MagicMock(return_value=[])
    with pytest.raises(BlogPostNotFoundError):
        client.get_blog_post("S9999")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gramps_blog.py -k get_blog_post -v`
Expected: FAIL (`AttributeError: ... 'get_blog_post'`)

- [ ] **Step 3: Implement** — add to `BlogMixin`:

```python
    def _get_blog_source(self, gramps_id):
        """Fetch a Source by gramps_id (full object), or raise BlogPostNotFoundError.

        The live API 404s on an unknown gramps_id; map that (and an empty 200
        list) to BlogPostNotFoundError, like get_person does.
        """
        try:
            sources = self._request("GET", f"/api/sources/?gramps_id={gramps_id}")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                raise BlogPostNotFoundError(gramps_id) from exc
            raise
        if not sources:
            raise BlogPostNotFoundError(gramps_id)
        return sources[0]

    def get_blog_post(self, gramps_id):
        """Fetch a blog post: source fields + the first note rendered as HTML.

        Returns {gramps_id, title, author, change, body_html, body_text,
        note_gramps_id}. change is the raw unix ts. If the source has no note,
        the body_* / note_gramps_id fields are None.
        """
        source = self._get_blog_source(gramps_id)
        note_list = source.get("note_list") or []
        body_html = body_text = note_gramps_id = None
        if note_list:
            note = self._request("GET", f"/api/notes/{note_list[0]}?formats=html")
            body_text = (note.get("text") or {}).get("string")
            body_html = (note.get("formatted") or {}).get("html")
            note_gramps_id = note.get("gramps_id")
        return {
            "gramps_id": source["gramps_id"],
            "title": source.get("title"),
            "author": source.get("author"),
            "change": source.get("change"),
            "body_html": body_html,
            "body_text": body_text,
            "note_gramps_id": note_gramps_id,
        }
```

- [ ] **Step 4: Register the tool** — in `server.py`:

```python
    @register
    def gramps_get_blog_post(gramps_id: str) -> dict:
        """Fetch one blog post by its Source Gramps ID (e.g. 'S0002').

        Returns title, author, change (unix ts), the body as rendered HTML
        (body_html) and raw string (body_text), and note_gramps_id.
        """
        return client.get_blog_post(gramps_id)
```

- [ ] **Step 5: Server delegation test** (append to `tests/test_server.py`)

```python
def test_gramps_get_blog_post_calls_client():
    client = MagicMock()
    client.get_blog_post.return_value = {"gramps_id": "S0002", "title": "T"}
    _, tools = create_server(client)

    result = tools["gramps_get_blog_post"]("S0002")

    client.get_blog_post.assert_called_once_with("S0002")
    assert result["gramps_id"] == "S0002"
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_gramps_blog.py tests/test_server.py -k get_blog_post -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add gramps_blog.py server.py tests/test_gramps_blog.py tests/test_server.py
git commit -m "feat: add gramps_get_blog_post (source + HTML-rendered body note)"
```

---

### Task 6: `update_blog_post` + `gramps_update_blog_post`

**Files:**
- Modify: `gramps_blog.py` (add `update_blog_post`)
- Modify: `server.py` (register `gramps_update_blog_post`)
- Test: `tests/test_gramps_blog.py`, `tests/test_server.py`

**Interfaces:**
- Produces: `update_blog_post(gramps_id, title=None, body=None, author=None) -> {gramps_id, updated:[...]}` (partial; RMW on Source for title/author, RMW on the first Note for body, preserving the note's type).

- [ ] **Step 1: Write the failing test** (append to `tests/test_gramps_blog.py`)

```python
def test_update_blog_post_title_author_rmw_on_source():
    client = make_client()
    source = {"gramps_id": "S0002", "handle": "sH", "title": "old", "author": "old",
              "note_list": ["nH"], "tag_list": ["tagBlog"]}
    client._request = MagicMock(side_effect=[
        [source],   # _get_blog_source
        None,       # PUT source
    ])

    result = client.update_blog_post("S0002", title="new", author="newauth")

    put = client._request.call_args_list[1]
    assert put.args[:2] == ("PUT", "/api/sources/sH")
    body = put.kwargs["json_body"]
    assert body["title"] == "new" and body["author"] == "newauth"
    assert body["tag_list"] == ["tagBlog"]  # RMW preserved (not blanked)
    assert set(result["updated"]) == {"title", "author"}


def test_update_blog_post_body_rmw_on_note_preserves_type():
    client = make_client()
    source = {"gramps_id": "S0002", "handle": "sH", "note_list": ["nH"]}
    note = {"handle": "nH", "text": {"_class": "StyledText", "tags": [], "string": "old"},
            "type": {"_class": "NoteType", "value": 24, "string": ""}}
    client._request = MagicMock(side_effect=[
        [source],   # _get_blog_source
        note,       # GET note
        None,       # PUT note
    ])

    result = client.update_blog_post("S0002", body="<p>new</p>")

    put = client._request.call_args_list[2]
    assert put.args[:2] == ("PUT", "/api/notes/nH")
    body = put.kwargs["json_body"]
    assert body["text"]["string"] == "<p>new</p>"
    assert body["type"] == {"_class": "NoteType", "value": 24, "string": ""}  # type preserved
    assert result["updated"] == ["body"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gramps_blog.py -k update_blog_post -v`
Expected: FAIL (`AttributeError: ... 'update_blog_post'`)

- [ ] **Step 3: Implement** — add to `BlogMixin`:

```python
    def update_blog_post(self, gramps_id, title=None, body=None, author=None):
        """Partial update of a blog post; only provided fields change.

        title/author are Read-Modify-Written on the Source; body is RMW'd on the
        first note's text.string (preserving the note's type). If the source has
        no note yet, a body note is created (per blog_body_format) and attached.
        Returns {gramps_id, updated: [changed field names]}.
        """
        source = self._get_blog_source(gramps_id)
        updated = []
        if title is not None:
            source["title"] = title
            updated.append("title")
        if author is not None:
            source["author"] = author
            updated.append("author")
        if title is not None or author is not None:
            self._request("PUT", f"/api/sources/{source['handle']}", json_body=source)

        if body is not None:
            note_list = source.get("note_list") or []
            if note_list:
                note = self._request("GET", f"/api/notes/{note_list[0]}")
                note["text"]["string"] = body
                self._request("PUT", f"/api/notes/{note['handle']}", json_body=note)
            else:
                note_handle = self._create_body_note(body)
                source.setdefault("note_list", []).append(note_handle)
                self._request("PUT", f"/api/sources/{source['handle']}", json_body=source)
            updated.append("body")
        return {"gramps_id": gramps_id, "updated": updated}
```

- [ ] **Step 4: Register the tool** — in `server.py`:

```python
    @register
    def gramps_update_blog_post(
        gramps_id: str,
        title: str | None = None,
        body: str | None = None,
        author: str | None = None,
    ) -> dict:
        """Update a blog post's title, body, and/or author (only what you pass).

        body is stored per the server's GRAMPS_BLOG_BODY_FORMAT and replaces the
        existing note's text (keeping its type). Returns {gramps_id, updated}.
        """
        return client.update_blog_post(gramps_id, title, body, author)
```

- [ ] **Step 5: Server delegation test** (append to `tests/test_server.py`)

```python
def test_gramps_update_blog_post_calls_client():
    client = MagicMock()
    client.update_blog_post.return_value = {"gramps_id": "S0002", "updated": ["title"]}
    _, tools = create_server(client)

    result = tools["gramps_update_blog_post"]("S0002", "new title")

    client.update_blog_post.assert_called_once_with("S0002", "new title", None, None)
    assert result["updated"] == ["title"]
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_gramps_blog.py tests/test_server.py -k update_blog_post -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add gramps_blog.py server.py tests/test_gramps_blog.py tests/test_server.py
git commit -m "feat: add gramps_update_blog_post (partial RMW, type-preserving)"
```

---

### Task 7: `delete_blog_post` + `gramps_delete_blog_post` (destructive, env-gated)

**Files:**
- Modify: `gramps_blog.py` (add `delete_blog_post`)
- Modify: `server.py` (add tool inside the `if enable_destructive:` block)
- Test: `tests/test_gramps_blog.py`, `tests/test_server.py`

**Interfaces:**
- Produces: `delete_blog_post(gramps_id, confirm=False) -> {gramps_id, deleted, count_before, count_after, deleted_notes}` (confirm strict; sources-1 guard; orphaned-note cleanup).

- [ ] **Step 1: Write the failing test** (append to `tests/test_gramps_blog.py`)

```python
from gramps_blog import BlogPostDeleteCountMismatchError


def test_delete_blog_post_requires_confirm_true():
    client = make_client()
    client._request = MagicMock()
    with pytest.raises(ValueError):
        client.delete_blog_post("S0002", confirm=False)
    client._request.assert_not_called()  # no request when unconfirmed


def test_delete_blog_post_deletes_source_and_orphaned_note():
    client = make_client()
    source = {"gramps_id": "S0002", "handle": "sH", "note_list": ["nH"]}
    client._request = MagicMock(side_effect=[
        [source],                                      # _get_blog_source
        [{"gramps_id": "S0002"}, {"gramps_id": "S0003"}],  # count before = 2
        None,                                          # DELETE source
        [{"gramps_id": "S0003"}],                      # count after = 1
        {"handle": "nH", "backlinks": {}},             # note backlinks (orphaned)
        None,                                          # DELETE note
    ])

    result = client.delete_blog_post("S0002", confirm=True)

    assert result["deleted"] is True
    assert result["count_before"] == 2 and result["count_after"] == 1
    assert result["deleted_notes"] == ["nH"]
    assert client._request.call_args_list[2].args[:2] == ("DELETE", "/api/sources/sH")


def test_delete_blog_post_count_guard():
    client = make_client()
    source = {"gramps_id": "S0002", "handle": "sH", "note_list": []}
    client._request = MagicMock(side_effect=[
        [source],
        [{"gramps_id": "S0002"}, {"gramps_id": "S0003"}],  # before = 2
        None,
        [{"gramps_id": "S0002"}, {"gramps_id": "S0003"}],  # after = 2 (unchanged!)
    ])
    with pytest.raises(BlogPostDeleteCountMismatchError):
        client.delete_blog_post("S0002", confirm=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gramps_blog.py -k delete_blog_post -v`
Expected: FAIL (`AttributeError: ... 'delete_blog_post'`)

- [ ] **Step 3: Implement** — add to `BlogMixin`:

```python
    def delete_blog_post(self, gramps_id, confirm=False):
        """Delete a blog post. DESTRUCTIVE — requires confirm=True.

        Deletes the Source, guards that the source count drops by exactly one,
        then cleans up the body note(s) if now orphaned (shared notes kept).
        `confirm` must be the literal True. Returns before/after counts and the
        deleted note handles.
        """
        if confirm is not True:
            raise ValueError("delete_blog_post requires confirm=True (destructive)")
        source = self._get_blog_source(gramps_id)
        note_handles = source.get("note_list") or []
        count_before = self.count_sources()
        self._request("DELETE", f"/api/sources/{source['handle']}")
        count_after = self.count_sources()
        if count_after != count_before - 1:
            raise BlogPostDeleteCountMismatchError(
                f"Source count did not drop by one: {count_before} -> {count_after}"
            )
        deleted_notes = self._delete_orphaned_notes(note_handles)
        return {
            "gramps_id": gramps_id,
            "deleted": True,
            "count_before": count_before,
            "count_after": count_after,
            "deleted_notes": deleted_notes,
        }
```

- [ ] **Step 4: Register the tool** — in `server.py`, inside the existing `if enable_destructive:` block (after `gramps_delete_family`):

```python
        @register
        def gramps_delete_blog_post(gramps_id: str, confirm: bool = False) -> dict:
            """Delete a blog post. DESTRUCTIVE — requires confirm=True.

            Removes the Source and cleans up its body note if now orphaned
            (shared notes are kept); guards that the source count drops by one.
            Only present when the server was started with
            GRAMPS_ENABLE_DESTRUCTIVE=1.
            """
            return client.delete_blog_post(gramps_id, confirm)
```

- [ ] **Step 5: Server registration/gating tests** (append to `tests/test_server.py`)

```python
def test_gramps_delete_blog_post_registered_and_delegates_when_enabled():
    client = MagicMock()
    client.delete_blog_post.return_value = {
        "gramps_id": "S0002", "deleted": True, "count_before": 2, "count_after": 1,
        "deleted_notes": ["nH"],
    }
    _, tools = create_server(client, enable_destructive=True)

    assert "gramps_delete_blog_post" in tools
    result = tools["gramps_delete_blog_post"]("S0002", True)

    client.delete_blog_post.assert_called_once_with("S0002", True)
    assert result["deleted"] is True


def test_gramps_delete_blog_post_hidden_when_disabled():
    client = MagicMock()
    _, tools = create_server(client, enable_destructive=False)

    assert "gramps_delete_blog_post" not in tools
    assert "gramps_create_blog_post" in tools  # non-destructive blog tools still present
```

- [ ] **Step 6: Run tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_gramps_blog.py tests/test_server.py -k "delete_blog_post" -v && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add gramps_blog.py server.py tests/test_gramps_blog.py tests/test_server.py
git commit -m "feat: add gramps_delete_blog_post (env-gated, orphaned-note cleanup)"
```

---

### Task 8: G12 — `set_first_name` + `gramps_set_first_name`

**Files:**
- Modify: `gramps_client.py` (add `_first_name_mutation` near `_surname_mutation:133`; add `set_first_name` near `set_surname:307`)
- Modify: `server.py` (register `gramps_set_first_name`)
- Test: `tests/test_gramps_client.py`, `tests/test_server.py`

**Interfaces:**
- Produces: `set_first_name(gramps_id, first_name) -> {gramps_id, before, after}` (guarded write; sets `primary_name.first_name`).

- [ ] **Step 1: Write the failing test** (append to `tests/test_gramps_client.py`)

```python
@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_first_name_updates_primary(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    person = {
        "gramps_id": "I0036", "handle": "xyz789", "gender": 0,
        "primary_name": {"first_name": "Ala", "surname_list": [{"surname": "Werneck"}]},
        "alternate_names": [],
    }
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0036"}]),  # count before
        make_response([person]),                  # get_person
        make_response(None),                      # put
        make_response([{"gramps_id": "I0036"}]),  # count after
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.set_first_name("I0036", "Alla")

    put_body = mock_request.call_args_list[2].kwargs["json"]
    assert put_body["primary_name"]["first_name"] == "Alla"
    assert put_body["primary_name"]["surname_list"][0]["surname"] == "Werneck"  # preserved
    assert result["before"]["primary_name"]["first_name"] == "Ala"
    assert result["after"]["primary_name"]["first_name"] == "Alla"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gramps_client.py -k set_first_name -v`
Expected: FAIL (`AttributeError: ... 'set_first_name'`)

- [ ] **Step 3: Implement** — in `gramps_client.py`, add a module-level mutation after `_surname_mutation` (line ~139):

```python
def _first_name_mutation(first_name):
    """Build a person-mutation that sets the primary given (first) name."""
    def mutate(person):
        person["primary_name"]["first_name"] = first_name
    return mutate
```

And add the method after `set_surname` (line ~308):

```python
    def set_first_name(self, gramps_id, first_name):
        return self._guarded_write(gramps_id, _first_name_mutation(first_name))
```

- [ ] **Step 4: Register the tool** — in `server.py`, add after `gramps_set_surname`:

```python
    @register
    def gramps_set_first_name(gramps_id: str, first_name: str) -> dict:
        """Set a person's primary given (first) name. Non-destructive; returns before/after."""
        return client.set_first_name(gramps_id, first_name)
```

- [ ] **Step 5: Server delegation test** (append to `tests/test_server.py`)

```python
def test_gramps_set_first_name_calls_client():
    client = MagicMock()
    client.set_first_name.return_value = {"gramps_id": "I0036", "before": {}, "after": {}}
    _, tools = create_server(client)

    tools["gramps_set_first_name"]("I0036", "Alla")

    client.set_first_name.assert_called_once_with("I0036", "Alla")
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_gramps_client.py tests/test_server.py -k set_first_name -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add gramps_client.py server.py tests/test_gramps_client.py tests/test_server.py
git commit -m "feat: add gramps_set_first_name (G12)"
```

---

### Task 9: G13a — generalize `add_birth_name` into `add_alternate_name` + `gramps_add_alternate_name`

**Files:**
- Modify: `gramps_client.py:318-351` (`add_birth_name` → `add_alternate_name` + alias)
- Modify: `server.py` (register `gramps_add_alternate_name`)
- Test: `tests/test_gramps_client.py`, `tests/test_server.py`

**Interfaces:**
- Produces: `add_alternate_name(gramps_id, surname, first_name=None, name_type="Birth Name") -> {gramps_id, before, after}`; `add_birth_name(gramps_id, surname, first_name=None)` becomes a thin alias.

- [ ] **Step 1: Write the failing test** (append to `tests/test_gramps_client.py`)

```python
@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_add_alternate_name_uses_given_type(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    person = {
        "gramps_id": "I0036", "handle": "xyz789", "gender": 0,
        "primary_name": {"first_name": "Alla", "surname_list": [{"surname": "Prentl", "primary": True}]},
        "alternate_names": [],
    }
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0036"}]),
        make_response([person]),
        make_response(None),
        make_response([{"gramps_id": "I0036"}]),
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    client.add_alternate_name("I0036", "Werneck", name_type="Married Name")

    alt = mock_request.call_args_list[2].kwargs["json"]["alternate_names"][0]
    assert alt["surname_list"][0]["surname"] == "Werneck"
    assert alt["type"] == "Married Name"
    assert alt["first_name"] == "Alla"  # carried from primary
```

The existing `test_add_birth_name_appends_entry` must still pass (regression: the alias still produces a `Birth Name` alt).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gramps_client.py -k "add_alternate_name or add_birth_name" -v`
Expected: FAIL on `add_alternate_name` (not defined); `add_birth_name` still passes.

- [ ] **Step 3: Implement** — in `gramps_client.py`, replace the `add_birth_name` method (lines 318-351) with a generalized method + alias. The body is identical to the current `add_birth_name` except the hardcoded `"type": "Birth Name"` becomes `name_type`:

```python
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
                "first_name": first_name if first_name is not None else primary_name.get("first_name", ""),
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
```

- [ ] **Step 4: Register the tool** — in `server.py`, add after `gramps_add_birth_name`:

```python
    @register
    def gramps_add_alternate_name(
        gramps_id: str,
        surname: str,
        first_name: str | None = None,
        name_type: str = "Birth Name",
    ) -> dict:
        """Add an alternate name of a given type (e.g. 'Birth Name', 'Married Name').

        Surname content is taken from the argument; other subfields default. Use
        it to record a maiden or married name alongside the primary name.
        """
        return client.add_alternate_name(gramps_id, surname, first_name, name_type)
```

- [ ] **Step 5: Server delegation test** (append to `tests/test_server.py`)

```python
def test_gramps_add_alternate_name_calls_client():
    client = MagicMock()
    client.add_alternate_name.return_value = {"gramps_id": "I0036", "before": {}, "after": {}}
    _, tools = create_server(client)

    tools["gramps_add_alternate_name"]("I0036", "Werneck", "Alla", "Married Name")

    client.add_alternate_name.assert_called_once_with("I0036", "Werneck", "Alla", "Married Name")
```

- [ ] **Step 6: Run tests (incl. add_birth_name regression) + full suite**

Run: `.venv/bin/python -m pytest tests/test_gramps_client.py tests/test_server.py -k "alternate_name or birth_name" -v && .venv/bin/python -m pytest -q`
Expected: PASS (including the untouched `test_add_birth_name_appends_entry` and `test_gramps_add_birth_name_calls_client`)

- [ ] **Step 7: Commit**

```bash
git add gramps_client.py server.py tests/test_gramps_client.py tests/test_server.py
git commit -m "feat: generalize add_birth_name into add_alternate_name (G13a)"
```

---

### Task 10: G13b — `swap_primary_name` + `gramps_swap_primary_name`

**Files:**
- Modify: `gramps_client.py` (add `swap_primary_name` near the other name methods)
- Modify: `server.py` (register `gramps_swap_primary_name`)
- Test: `tests/test_gramps_client.py`, `tests/test_server.py`

**Interfaces:**
- Produces: `swap_primary_name(gramps_id, alt_index=0) -> {gramps_id, before, after}` (swaps `primary_name` with `alternate_names[alt_index]`; `ValueError` if index out of range, no write).

- [ ] **Step 1: Write the failing test** (append to `tests/test_gramps_client.py`)

```python
@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_swap_primary_name_swaps_with_alt(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    person = {
        "gramps_id": "I0036", "handle": "xyz789", "gender": 0,
        "primary_name": {"first_name": "Alla", "type": "Married Name",
                         "surname_list": [{"surname": "Werneck", "primary": True}]},
        "alternate_names": [
            {"first_name": "Alla", "type": "Birth Name",
             "surname_list": [{"surname": "Prentl", "primary": True}]}
        ],
    }
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0036"}]),
        make_response([person]),
        make_response(None),
        make_response([{"gramps_id": "I0036"}]),
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.swap_primary_name("I0036")

    put_body = mock_request.call_args_list[2].kwargs["json"]
    assert put_body["primary_name"]["type"] == "Birth Name"
    assert put_body["primary_name"]["surname_list"][0]["surname"] == "Prentl"
    assert put_body["alternate_names"][0]["type"] == "Married Name"
    assert put_body["alternate_names"][0]["surname_list"][0]["surname"] == "Werneck"
    assert result["after"]["primary_name"]["type"] == "Birth Name"


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_swap_primary_name_out_of_range_raises_without_write(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    person = {"gramps_id": "I0036", "handle": "xyz789", "gender": 0,
              "primary_name": {"first_name": "A", "surname_list": [{"surname": "B"}]},
              "alternate_names": []}
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0036"}]),  # count before
        make_response([person]),                  # get_person
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(ValueError):
        client.swap_primary_name("I0036")
    # only count + get were issued; no PUT
    assert all(c.args[0] != "PUT" for c in mock_request.call_args_list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gramps_client.py -k swap_primary_name -v`
Expected: FAIL (`AttributeError: ... 'swap_primary_name'`)

- [ ] **Step 3: Implement** — in `gramps_client.py`, add after `swap`-adjacent name methods (e.g. after `add_birth_name`):

```python
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
```

- [ ] **Step 4: Register the tool** — in `server.py`, add after `gramps_add_alternate_name`:

```python
    @register
    def gramps_swap_primary_name(gramps_id: str, alt_index: int = 0) -> dict:
        """Swap a person's primary name with one of their alternate names.

        alt_index selects which alternate (default the first). Use it to promote
        e.g. a Birth Name to primary and demote the Married Name to an alternate.
        Errors if there is no alternate name at that index.
        """
        return client.swap_primary_name(gramps_id, alt_index)
```

- [ ] **Step 5: Server delegation test** (append to `tests/test_server.py`)

```python
def test_gramps_swap_primary_name_calls_client():
    client = MagicMock()
    client.swap_primary_name.return_value = {"gramps_id": "I0036", "before": {}, "after": {}}
    _, tools = create_server(client)

    tools["gramps_swap_primary_name"]("I0036", 0)

    client.swap_primary_name.assert_called_once_with("I0036", 0)


def test_gramps_swap_primary_name_defaults_index():
    client = MagicMock()
    client.swap_primary_name.return_value = {"gramps_id": "I0036", "before": {}, "after": {}}
    _, tools = create_server(client)

    tools["gramps_swap_primary_name"]("I0036")

    client.swap_primary_name.assert_called_once_with("I0036", 0)
```

- [ ] **Step 6: Run tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_gramps_client.py tests/test_server.py -k swap_primary_name -v && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add gramps_client.py server.py tests/test_gramps_client.py tests/test_server.py
git commit -m "feat: add gramps_swap_primary_name (G13b)"
```

---

### Task 11: Docs — README, `.env.example`, `docs/blog-crud.md`, `HANDOFF-new-tools.md`

**Files:**
- Modify: `README.md` (tool list + a "Blog posts" note + the 3 name tools)
- Modify: `.env.example` (document `GRAMPS_BLOG_BODY_FORMAT`)
- Modify: `docs/blog-crud.md` (record the confirmed HTML_CODE payload)
- Modify: `HANDOFF-new-tools.md` (mark G12/G13 done, like §8d did for G10/G11)

- [ ] **Step 1: `.env.example`** — add next to the `GRAMPS_ENABLE_DESTRUCTIVE` entry:

```bash
# Blog post body format: "html" stores post bodies as HTML (rendered + sanitized
# by the server); anything else (default) stores plain text. Only the exact
# value "html" enables HTML.
GRAMPS_BLOG_BODY_FORMAT=text
```

- [ ] **Step 2: `README.md`** — add the 8 new tools to the tool list and a short "Blog posts" subsection (blog post = Source tagged `Blog` + first note as body; body format via `GRAMPS_BLOG_BODY_FORMAT`; `gramps_delete_blog_post` only with the destructive gate on). List `gramps_set_first_name`, `gramps_add_alternate_name`, `gramps_swap_primary_name` under the person-editing tools. Verify the tool list matches `server.py` exactly.

- [ ] **Step 3: `docs/blog-crud.md`** — in §7 / §3.3, record that the reliable create payload for an HTML body note is the full type object `{"_class": "NoteType", "value": 24, "string": ""}` (24 = HTML_CODE), NOT the plaintext string (`"Html code"` is case-sensitive and silently falls back to CUSTOM otherwise). Note the `bleach` allow-list finding from the live smoke (Task 12).

- [ ] **Step 4: `HANDOFF-new-tools.md`** — add a `### 8f. STATUS ... — G12/G13 implemented (part of Welle 4)` note mirroring §8d, marking G12 (`set_first_name`) and G13 (`add_alternate_name` + `swap_primary_name`) done.

- [ ] **Step 5: Commit**

```bash
git add README.md .env.example docs/blog-crud.md HANDOFF-new-tools.md
git commit -m "docs: document Welle 4 blog + name-field tools and GRAMPS_BLOG_BODY_FORMAT"
```

Note: `HANDOFF-new-tools.md` and `PROGRESS.md` are gitignored — the HANDOFF edit is local-only; `git add` will no-op on it, which is expected. Stage what git tracks.

---

### Task 12: Live smoke against INT (verification)

**Not a code task — a verification run before opening the PR.** Uses the INT instance (`.env.int`, OWNER role) exactly like the Welle 3 smoke.

- [ ] **Step 1: Start the server against INT with HTML mode + destructive on**, in a throwaway Python session or via the MCP client, constructing `GrampsClient(..., blog_body_format="html")`.

- [ ] **Step 2: Exercise the flow and record results:**
  - `create_blog_post("Smoke title", "<p><strong>Bold</strong> and <a href='https://gramps-project.org'>link</a>.</p>", author="Smoke")` → note the new `S...` id.
  - `list_blog_posts()` → the new post appears first (sort=-change).
  - `get_blog_post(<id>)` → assert `body_html` contains rendered `<strong>`/`<a>` (confirms HTML_CODE renders) and record which tags survived `bleach` (the allow-list).
  - `update_blog_post(<id>, title="Smoke title 2", body="<p>Updated <em>body</em>.</p>")` → re-get, assert changes and that the note type stayed HTML_CODE.
  - `delete_blog_post(<id>, confirm=True)` → assert `deleted_notes` includes the body note and INT source count returns to baseline.
  - Also smoke text mode once: a client with `blog_body_format="text"` → `create` → `get` shows the plain string.
- [ ] **Step 3: Name-field smoke:** on a throwaway person — `set_first_name`, `add_alternate_name(..., name_type="Married Name")`, `swap_primary_name` — assert the primary/alt round-trip cleanly (Part B live-verify item).
- [ ] **Step 4: Restore INT** to its baseline counts (delete any smoke objects created).
- [ ] **Step 5:** Fold the confirmed `bleach` allow-list into `docs/blog-crud.md` (Task 11 Step 3) if not already, and note any format nuance for HTML_CODE. Commit any doc refinement.

---

## Self-Review

Run after the plan is complete (checklist, not a subagent):

1. **Spec coverage** — Part A: list ✓ (T4), get ✓ (T5), create ✓ (T3), update ✓ (T6), delete/gated ✓ (T7), body-format flag ✓ (T1/T2), `HTML_CODE` payload ✓ (T2 Global Constraints). Part B: G12 ✓ (T8), G13a ✓ (T9), G13b ✓ (T10). Docs ✓ (T11), live smoke ✓ (T12).
2. **Placeholder scan** — no TBD/TODO; each code step shows real code; the only deferred items (bleach allow-list, HTML format nuance) are explicit verification steps in T12, not implementation gaps.
3. **Type consistency** — `_create_body_note`/`count_sources`/`_get_blog_source` defined in T2/T5 and reused consistently; error classes imported from `gramps_blog`; server tools all thin wrappers; `add_birth_name` alias preserves the existing signature.

## Execution Handoff

Offer the two execution options (subagent-driven vs inline) after the user reviews this plan.
