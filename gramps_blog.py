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
                note_list.append(note_handle)
                source["note_list"] = note_list
                self._request("PUT", f"/api/sources/{source['handle']}", json_body=source)
            updated.append("body")
        return {"gramps_id": gramps_id, "updated": updated}

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
