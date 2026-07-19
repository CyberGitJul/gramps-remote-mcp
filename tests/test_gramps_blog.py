from unittest.mock import MagicMock
from urllib.parse import quote as _quote

import pytest
import requests

from gramps_blog import BlogPostNotFoundError
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


def test_get_blog_post_http_404_raises_blog_post_not_found():
    # the live API 404s (NOT an empty 200 list) for an unknown gramps_id on the
    # sources lookup; _get_blog_source must map that to BlogPostNotFoundError,
    # like get_person does for PersonNotFoundError.
    resp = MagicMock()
    resp.status_code = 404
    err = requests.HTTPError(response=resp)
    client = make_client()
    client._request = MagicMock(side_effect=err)
    with pytest.raises(BlogPostNotFoundError):
        client.get_blog_post("S9999")


def test_get_blog_post_non_404_http_error_propagates():
    # a non-404 HTTP error (e.g. 500) must NOT be masked as BlogPostNotFoundError.
    resp = MagicMock()
    resp.status_code = 500
    err = requests.HTTPError(response=resp)
    client = make_client()
    client._request = MagicMock(side_effect=err)
    with pytest.raises(requests.HTTPError):
        client.get_blog_post("S0002")
