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
