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
