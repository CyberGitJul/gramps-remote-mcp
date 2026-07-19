from unittest.mock import MagicMock, patch

import pytest

from gramps_client import GrampsClient, PersonNotFoundError


def make_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = b"1" if json_data is not None else b""
    resp.json.return_value = json_data

    def raise_for_status():
        if status_code >= 400:
            raise Exception(f"HTTP {status_code}")

    resp.raise_for_status.side_effect = raise_for_status
    return resp


@patch("gramps_client.requests.post")
def test_login_stores_access_token(mock_post):
    mock_post.return_value = make_response({"access_token": "tok123"})
    client = GrampsClient("https://example.test", "bot", "secret")

    client._login()

    assert client._access_token == "tok123"
    mock_post.assert_called_once_with(
        "https://example.test/api/token/",
        json={"username": "bot", "password": "secret"},
        timeout=10,
    )


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_person_found(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [{"gramps_id": "I0024", "handle": "abc123", "gender": 2}]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    person = client.get_person("I0024")

    assert person == {"gramps_id": "I0024", "handle": "abc123", "gender": 2}
    mock_request.assert_called_once_with(
        "GET",
        "https://example.test/api/people/?gramps_id=I0024",
        json=None,
        headers={"Authorization": "Bearer tok123"},
        timeout=10,
    )


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_person_not_found_raises(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response([])
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(PersonNotFoundError):
        client.get_person("I9999")


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_person_404_raises_person_not_found(mock_post, mock_request):
    # the live API returns 404 (NOT an empty 200 list) for an unknown gramps_id;
    # get_person must surface that as a clean PersonNotFoundError, not a raw HTTPError.
    import requests

    mock_post.return_value = make_response({"access_token": "tok123"})
    resp = MagicMock()
    resp.status_code = 404
    resp.content = b""
    resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    mock_request.return_value = resp
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(PersonNotFoundError):
        client.get_person("I9999")


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_person_non_404_http_error_propagates(mock_post, mock_request):
    # a non-404 HTTP error (e.g. 500) must NOT be masked as PersonNotFoundError.
    import requests

    mock_post.return_value = make_response({"access_token": "tok123"})
    resp = MagicMock()
    resp.status_code = 500
    resp.content = b""
    resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    mock_request.return_value = resp
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(requests.HTTPError):
        client.get_person("I0024")


from gramps_client import PersonCountMismatchError


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_count_people(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [{"gramps_id": "I0001"}, {"gramps_id": "I0002"}]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    assert client.count_people() == 2
    mock_request.assert_called_once_with(
        "GET",
        "https://example.test/api/people/?keys=gramps_id",
        json=None,
        headers={"Authorization": "Bearer tok123"},
        timeout=10,
    )


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_gender_preserves_other_fields(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    person = {
        "gramps_id": "I0024",
        "handle": "abc123",
        "gender": 2,
        "primary_name": {"first_name": "John", "nick": "Jack"},
        "alternate_names": [],
    }
    responses = [
        make_response([{"gramps_id": "I0024"}]),  # count before
        make_response([person]),  # get_person
        make_response(None),  # put
        make_response([{"gramps_id": "I0024"}]),  # count after
    ]
    mock_request.side_effect = responses
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.set_gender("I0024", 1)

    put_call = mock_request.call_args_list[2]
    assert put_call.args[0] == "PUT"
    assert put_call.args[1] == "https://example.test/api/people/abc123"
    put_body = put_call.kwargs["json"]
    assert put_body["gender"] == 1
    assert put_body["primary_name"]["nick"] == "Jack"  # preserved
    assert result["before"]["gender"] == 2
    assert result["after"]["gender"] == 1


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_surname_preserves_subfields(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    person = {
        "gramps_id": "I0036",
        "handle": "xyz789",
        "gender": 2,
        "primary_name": {
            "first_name": "Emma",
            "nick": "",
            "surname_list": [{"surname": "", "primary": True}],
            "type": "Birth Name",
        },
        "alternate_names": [],
    }
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0036"}]),
        make_response([person]),
        make_response(None),
        make_response([{"gramps_id": "I0036"}]),
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.set_surname("I0036", "Jones", name_type="Married Name")

    put_body = mock_request.call_args_list[2].kwargs["json"]
    assert put_body["primary_name"]["surname_list"][0]["surname"] == "Jones"
    assert put_body["primary_name"]["type"] == "Married Name"
    assert put_body["primary_name"]["first_name"] == "Emma"  # preserved
    # Verify before/after snapshots are independent (not aliased)
    assert result["before"]["primary_name"]["surname_list"][0]["surname"] == ""
    assert result["after"]["primary_name"]["surname_list"][0]["surname"] == "Jones"
    assert result["before"]["primary_name"]["type"] == "Birth Name"
    assert result["after"]["primary_name"]["type"] == "Married Name"


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_add_birth_name_appends_entry(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    person = {
        "gramps_id": "I0061",
        "handle": "def456",
        "gender": 2,
        "primary_name": {
            "first_name": "Mary",
            "nick": "",
            "surname_list": [
                {
                    "surname": "Miller",
                    "primary": True,
                    "prefix": "von",  # surname field that should be preserved
                    "connector": "und",  # surname field that should be preserved
                }
            ],
            "type": "Married Name",
            "citation_list": ["c001"],  # metadata that should NOT be inherited
            "date": {  # metadata that should NOT be inherited
                "calendar": 1,
                "dateval": [1900, 1, 1, False],
                "modifier": 0,
                "newyear": 0,
                "quality": 0,
                "sortval": 1,
            },
        },
        "alternate_names": [],
    }
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0061"}]),
        make_response([person]),
        make_response(None),
        make_response([{"gramps_id": "I0061"}]),
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.add_birth_name("I0061", "Smith")

    put_body = mock_request.call_args_list[2].kwargs["json"]
    assert len(put_body["alternate_names"]) == 1
    birth_name = put_body["alternate_names"][0]
    assert birth_name["surname_list"][0]["surname"] == "Smith"
    assert birth_name["type"] == "Birth Name"
    assert birth_name["first_name"] == "Mary"  # carried over from primary_name
    # Verify surname fields are preserved (prefix, connector, etc.)
    assert birth_name["surname_list"][0]["prefix"] == "von"  # preserved from primary
    assert birth_name["surname_list"][0]["connector"] == "und"  # preserved from primary
    # Verify metadata is NOT inherited (not a deepcopy)
    assert birth_name["citation_list"] == []  # fresh, not inherited from primary_name
    assert birth_name["date"] == {  # zeroed default, not inherited
        "calendar": 0,
        "dateval": [0, 0, 0, False],
        "modifier": 0,
        "newyear": 0,
        "quality": 0,
        "sortval": 0,
    }
    # Verify before/after snapshots are independent (not aliased)
    assert len(result["before"]["alternate_names"]) == 0  # before: empty
    assert len(result["after"]["alternate_names"]) == 1  # after: has new entry
    assert result["after"]["alternate_names"][0]["surname_list"][0]["surname"] == "Smith"


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_count_mismatch_raises(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    person = {
        "gramps_id": "I0024",
        "handle": "abc123",
        "gender": 2,
        "primary_name": {"surname_list": [{"surname": ""}]},
        "alternate_names": [],
    }
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0024"}, {"gramps_id": "I0025"}]),  # count before: 2
        make_response([person]),
        make_response(None),
        make_response([{"gramps_id": "I0024"}]),  # count after: 1 -- mismatch!
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(PersonCountMismatchError):
        client.set_gender("I0024", 1)


from gramps_client import FamilyNotFoundError


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_create_object_returns_new_item_from_transaction(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [
            {
                "type": "add",
                "handle": "newhandle1",
                "_class": "Tag",
                "old": None,
                "new": {"handle": "newhandle1", "_class": "Tag", "name": "Unbestätigt"},
            }
        ],
        status_code=201,
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client._create_object("tags", {"_class": "Tag", "name": "Unbestätigt"})

    assert result == {"handle": "newhandle1", "_class": "Tag", "name": "Unbestätigt"}
    mock_request.assert_called_once_with(
        "POST",
        "https://example.test/api/tags/",
        json={"_class": "Tag", "name": "Unbestätigt"},
        headers={"Authorization": "Bearer tok123"},
        timeout=10,
    )


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_create_object_raises_if_no_add_item_found(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [{"type": "update", "handle": "x", "_class": "Tag", "old": {}, "new": {}}],
        status_code=201,
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(ValueError):
        client._create_object("tags", {"_class": "Tag", "name": "Unbestätigt"})


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_count_families(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [{"gramps_id": "F0001"}, {"gramps_id": "F0002"}]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    assert client.count_families() == 2
    mock_request.assert_called_once_with(
        "GET",
        "https://example.test/api/families/?keys=gramps_id",
        json=None,
        headers={"Authorization": "Bearer tok123"},
        timeout=10,
    )


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_family_found(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [{"gramps_id": "F0007", "handle": "famhandle7"}]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    family = client.get_family("F0007")

    assert family == {"gramps_id": "F0007", "handle": "famhandle7"}
    mock_request.assert_called_once_with(
        "GET",
        "https://example.test/api/families/?gramps_id=F0007",
        json=None,
        headers={"Authorization": "Bearer tok123"},
        timeout=10,
    )


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_family_not_found_raises(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response([])
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(FamilyNotFoundError):
        client.get_family("F9999")


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_find_tag_handle_found(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [
            {"handle": "taghandle1", "name": "Unbestätigt"},
            {"handle": "taghandle2", "name": "ToDo"},
        ]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    assert client._find_tag_handle("Unbestätigt") == "taghandle1"
    mock_request.assert_called_once_with(
        "GET",
        "https://example.test/api/tags/?keys=handle,name",
        json=None,
        headers={"Authorization": "Bearer tok123"},
        timeout=10,
    )


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_find_tag_handle_matches_across_unicode_normalization(mock_post, mock_request):
    # a tag stored decomposed (NFD) must still match the NFC literal
    import unicodedata

    nfd = unicodedata.normalize("NFD", "Unbestätigt")
    assert nfd != "Unbestätigt"  # sanity: the two byte forms genuinely differ
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response([{"handle": "taghandle1", "name": nfd}])
    client = GrampsClient("https://example.test", "bot", "secret")

    assert client._find_tag_handle("Unbestätigt") == "taghandle1"


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_find_tag_handle_not_found_returns_none(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response([{"handle": "taghandle2", "name": "ToDo"}])
    client = GrampsClient("https://example.test", "bot", "secret")

    assert client._find_tag_handle("Unbestätigt") is None


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_or_create_tag_reuses_existing(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [{"handle": "taghandle1", "name": "Unbestätigt"}]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    assert client._get_or_create_tag("Unbestätigt") == "taghandle1"
    assert mock_request.call_count == 1  # only the lookup, no create


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_or_create_tag_creates_when_missing(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.side_effect = [
        make_response([]),  # find: not found
        make_response(  # create
            [
                {
                    "type": "add",
                    "handle": "newtaghandle",
                    "_class": "Tag",
                    "old": None,
                    "new": {"handle": "newtaghandle", "name": "Unbestätigt"},
                }
            ],
            status_code=201,
        ),
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    assert client._get_or_create_tag("Unbestätigt") == "newtaghandle"
    create_call = mock_request.call_args_list[1]
    assert create_call.args[0] == "POST"
    assert create_call.args[1] == "https://example.test/api/tags/"
    assert create_call.kwargs["json"] == {
        "_class": "Tag",
        "name": "Unbestätigt",
        "color": "#000000000000",
        "priority": 0,
    }


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_search_person_matches_first_name_case_insensitive(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [
            {
                "gramps_id": "I0024",
                "gender": 1,
                "primary_name": {
                    "first_name": "John",
                    "surname_list": [{"surname": "Smith"}],
                },
            },
            {
                "gramps_id": "I0036",
                "gender": 2,
                "primary_name": {"first_name": "Emma", "surname_list": [{"surname": "Jones"}]},
            },
        ]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    results = client.search_person("john")

    assert results == [
        {"gramps_id": "I0024", "first_name": "John", "surname": "Smith", "gender": 1}
    ]
    mock_request.assert_called_once_with(
        "GET",
        "https://example.test/api/people/?keys=gramps_id,primary_name,gender,alternate_names",
        json=None,
        headers={"Authorization": "Bearer tok123"},
        timeout=10,
    )


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_search_person_matches_surname(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [
            {
                "gramps_id": "I0001",
                "gender": 2,
                "primary_name": {
                    "first_name": "William",
                    "surname_list": [{"surname": "Smith"}],
                },
            }
        ]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    results = client.search_person("smith")

    assert len(results) == 1
    assert results[0]["gramps_id"] == "I0001"


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_search_person_no_match_returns_empty(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [{"gramps_id": "I0001", "gender": 2, "primary_name": {"first_name": "Emma", "surname_list": []}}]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    assert client.search_person("nonexistent") == []


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_search_person_matches_true_substring_mid_name(mock_post, mock_request):
    # search_person's headline behavior: a substring in the MIDDLE of a name
    # (not just a prefix) matches, case-insensitively, on first name or surname.
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [
            {
                "gramps_id": "I0024",
                "gender": 1,
                "primary_name": {"first_name": "John", "surname_list": [{"surname": "Smith"}]},
            },
            {
                "gramps_id": "I0099",
                "gender": 0,
                "primary_name": {"first_name": "Susan", "surname_list": [{"surname": "Brown"}]},
            },
        ]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    # "ohn" is a mid-string substring of "John" (not a prefix), upper-cased in the query
    assert [r["gramps_id"] for r in client.search_person("OHN")] == ["I0024"]
    # "mit" is a mid-string substring of the surname "Smith"
    assert [r["gramps_id"] for r in client.search_person("mit")] == ["I0024"]


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_search_person_handles_missing_surname_list(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [{"gramps_id": "I0050", "gender": 0, "primary_name": {"first_name": "Josephine", "surname_list": []}}]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    results = client.search_person("josephine")

    assert results == [
        {"gramps_id": "I0050", "first_name": "Josephine", "surname": "", "gender": 0}
    ]


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_search_person_empty_query_returns_empty_without_request(mock_post, mock_request):
    # an empty/whitespace query must return [] and must not fetch anyone
    client = GrampsClient("https://example.test", "bot", "secret")

    assert client.search_person("") == []
    assert client.search_person("   ") == []
    mock_request.assert_not_called()


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_search_person_matches_combined_full_name(mock_post, mock_request):
    # headline G1 fix: "Georg Prentl" (first + surname) must match, where the old
    # first-OR-surname substring search returned nothing.
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [
            {
                "gramps_id": "I0024",
                "gender": 1,
                "primary_name": {
                    "first_name": "Georg",
                    "surname_list": [{"surname": "Prentl"}],
                },
            },
            {
                "gramps_id": "I0099",
                "gender": 0,
                "primary_name": {"first_name": "Anna", "surname_list": [{"surname": "Huber"}]},
            },
        ]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    results = client.search_person("Georg Prentl")

    assert [r["gramps_id"] for r in results] == ["I0024"]


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_search_person_matches_primary_nick(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [
            {
                "gramps_id": "I0024",
                "gender": 1,
                "primary_name": {
                    "first_name": "Georg",
                    "nick": "Schorsch",
                    "surname_list": [{"surname": "Prentl"}],
                },
            }
        ]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    results = client.search_person("schorsch")

    assert [r["gramps_id"] for r in results] == ["I0024"]
    # the returned record still reports the PRIMARY name, not the nick
    assert results[0]["first_name"] == "Georg"
    assert results[0]["surname"] == "Prentl"


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_search_person_matches_alternate_name_surname(mock_post, mock_request):
    # a maiden/alternate surname that differs from the primary surname must match
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [
            {
                "gramps_id": "I0036",
                "gender": 0,
                "primary_name": {
                    "first_name": "Elisabeth",
                    "nick": "Liesl",
                    "surname_list": [{"surname": "Prentl"}],
                },
                "alternate_names": [
                    {"first_name": "Elisabeth", "surname_list": [{"surname": "Müller"}]}
                ],
            }
        ]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    # matches on the alternate surname...
    assert [r["gramps_id"] for r in client.search_person("müller")] == ["I0036"]
    # ...and on the primary nick
    assert [r["gramps_id"] for r in client.search_person("liesl")] == ["I0036"]


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_search_person_limit_caps_results(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [
            {"gramps_id": "I0001", "gender": 1,
             "primary_name": {"first_name": "Hans", "surname_list": [{"surname": "Prentl"}]}},
            {"gramps_id": "I0002", "gender": 1,
             "primary_name": {"first_name": "Josef", "surname_list": [{"surname": "Prentl"}]}},
            {"gramps_id": "I0003", "gender": 0,
             "primary_name": {"first_name": "Maria", "surname_list": [{"surname": "Prentl"}]}},
        ]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    results = client.search_person("prentl", limit=2)

    assert [r["gramps_id"] for r in results] == ["I0001", "I0002"]  # first two, capped


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_search_person_limit_none_returns_all(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [
            {"gramps_id": "I0001", "gender": 1,
             "primary_name": {"first_name": "Hans", "surname_list": [{"surname": "Prentl"}]}},
            {"gramps_id": "I0002", "gender": 1,
             "primary_name": {"first_name": "Josef", "surname_list": [{"surname": "Prentl"}]}},
        ]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    assert len(client.search_person("prentl")) == 2  # no limit -> all matches


from gramps_client import _build_date, PersonCreateCountMismatchError


def test_build_date_exact_defaults_to_none_quality():
    date = _build_date(1831, None)
    assert date["modifier"] == 0
    assert date["quality"] == 0
    assert date["dateval"] == [0, 0, 1831, False]
    assert date["_class"] == "Date"


def test_build_date_about():
    date = _build_date(1831, "about")
    assert date["modifier"] == 3
    assert date["dateval"] == [0, 0, 1831, False]


def test_build_date_before():
    assert _build_date(1824, "before")["modifier"] == 1


def test_build_date_after():
    assert _build_date(1824, "after")["modifier"] == 2


def test_build_date_estimated_sets_quality_not_modifier():
    date = _build_date(1800, "estimated")
    assert date["modifier"] == 0
    assert date["quality"] == 1


def test_build_date_between_builds_compound_dateval():
    date = _build_date(1795, "between", year_to=1804)
    assert date["modifier"] == 4
    assert date["dateval"] == [0, 0, 1795, False, 0, 0, 1804, False]


def test_build_date_between_without_year_to_raises():
    with pytest.raises(ValueError):
        _build_date(1795, "between")


def test_build_date_unknown_quality_raises():
    with pytest.raises(ValueError):
        _build_date(1800, "guessed")


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_add_person_minimal_reuses_existing_tag(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0001"}, {"gramps_id": "I0002"}]),  # count before: 2
        make_response([{"handle": "taghandle1", "name": "Unbestätigt"}]),  # tag find
        make_response(  # person create
            [
                {
                    "type": "add",
                    "handle": "newpersonhandle",
                    "_class": "Person",
                    "old": None,
                    "new": {"handle": "newpersonhandle", "gramps_id": "I0163"},
                }
            ],
            status_code=201,
        ),
        make_response(  # count after: 3
            [{"gramps_id": "I0001"}, {"gramps_id": "I0002"}, {"gramps_id": "I0163"}]
        ),
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.add_person("John", "Smith", 1)

    assert result == "I0163"
    create_call = mock_request.call_args_list[2]
    assert create_call.args[0] == "POST"
    assert create_call.args[1] == "https://example.test/api/people/"
    person_body = create_call.kwargs["json"]
    assert person_body["gender"] == 1
    assert person_body["primary_name"]["first_name"] == "John"
    assert person_body["primary_name"]["surname_list"][0]["surname"] == "Smith"
    assert person_body["tag_list"] == ["taghandle1"]
    assert person_body["event_ref_list"] == []
    assert person_body["birth_ref_index"] == -1
    assert person_body["note_list"] == []


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_add_person_with_birth_note_and_new_tag(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0001"}]),  # count before: 1
        make_response(  # birth event create
            [
                {
                    "type": "add",
                    "handle": "eventhandle1",
                    "_class": "Event",
                    "old": None,
                    "new": {"handle": "eventhandle1"},
                }
            ],
            status_code=201,
        ),
        make_response(  # note create
            [
                {
                    "type": "add",
                    "handle": "notehandle1",
                    "_class": "Note",
                    "old": None,
                    "new": {"handle": "notehandle1"},
                }
            ],
            status_code=201,
        ),
        make_response([]),  # tag find: not found
        make_response(  # tag create
            [
                {
                    "type": "add",
                    "handle": "newtaghandle",
                    "_class": "Tag",
                    "old": None,
                    "new": {"handle": "newtaghandle"},
                }
            ],
            status_code=201,
        ),
        make_response(  # person create
            [
                {
                    "type": "add",
                    "handle": "newpersonhandle",
                    "_class": "Person",
                    "old": None,
                    "new": {"handle": "newpersonhandle", "gramps_id": "I0164"},
                }
            ],
            status_code=201,
        ),
        make_response([{"gramps_id": "I0001"}, {"gramps_id": "I0164"}]),  # count after: 2
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.add_person(
        "John",
        "Smith",
        1,
        birth_year=1795,
        birth_quality="estimated",
        note="citations/SMITH-001-citations.md, Entry C4",
    )

    assert result == "I0164"
    event_call = mock_request.call_args_list[1]
    assert event_call.args[1] == "https://example.test/api/events/"
    assert event_call.kwargs["json"]["type"] == {"_class": "EventType", "value": 12, "string": ""}
    assert event_call.kwargs["json"]["date"]["dateval"] == [0, 0, 1795, False]
    assert event_call.kwargs["json"]["date"]["quality"] == 1

    note_call = mock_request.call_args_list[2]
    assert note_call.args[1] == "https://example.test/api/notes/"
    assert note_call.kwargs["json"]["text"]["string"] == "citations/SMITH-001-citations.md, Entry C4"

    person_call = mock_request.call_args_list[5]
    person_body = person_call.kwargs["json"]
    assert person_body["tag_list"] == ["newtaghandle"]
    assert person_body["note_list"] == ["notehandle1"]
    assert person_body["event_ref_list"] == [
        {"_class": "EventRef", "ref": "eventhandle1", "role": {"_class": "EventRoleType", "value": 1, "string": ""}}
    ]
    assert person_body["birth_ref_index"] == 0


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_add_person_count_mismatch_raises(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0001"}]),  # count before: 1
        make_response([{"handle": "taghandle1", "name": "Unbestätigt"}]),  # tag find
        make_response(  # person create
            [
                {
                    "type": "add",
                    "handle": "newpersonhandle",
                    "_class": "Person",
                    "old": None,
                    "new": {"handle": "newpersonhandle", "gramps_id": "I0163"},
                }
            ],
            status_code=201,
        ),
        make_response([{"gramps_id": "I0001"}]),  # count after: still 1 -- mismatch!
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(PersonCreateCountMismatchError):
        client.add_person("John", "Smith", 1)


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_add_person_rejects_empty_names(mock_post, mock_request):
    # both names blank -> ValueError before any HTTP call
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(ValueError):
        client.add_person("", "", 2)
    with pytest.raises(ValueError):
        client.add_person("   ", "   ", 2)
    mock_request.assert_not_called()


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_add_person_skips_empty_note(mock_post, mock_request):
    # note="" must not create or attach an empty Note
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0001"}]),  # count before: 1
        make_response([{"handle": "taghandle1", "name": "Unbestätigt"}]),  # tag find
        make_response(  # person create
            [
                {
                    "type": "add",
                    "handle": "newpersonhandle",
                    "_class": "Person",
                    "old": None,
                    "new": {"handle": "newpersonhandle", "gramps_id": "I0163"},
                }
            ],
            status_code=201,
        ),
        make_response([{"gramps_id": "I0001"}, {"gramps_id": "I0163"}]),  # count after: 2
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.add_person("John", "Smith", 1, note="")

    assert result == "I0163"
    urls = [c.args[1] for c in mock_request.call_args_list]
    assert "https://example.test/api/notes/" not in urls  # no note created
    person_body = mock_request.call_args_list[2].kwargs["json"]
    assert person_body["note_list"] == []


from gramps_client import _assign_parent_handles, FamilyCreateCountMismatchError


def test_assign_parent_handles_male_and_female():
    father, mother = _assign_parent_handles(
        {"handle": "h_male", "gender": 1}, {"handle": "h_female", "gender": 0}
    )
    assert father == "h_male"
    assert mother == "h_female"


def test_assign_parent_handles_order_independent():
    father, mother = _assign_parent_handles(
        {"handle": "h_female", "gender": 0}, {"handle": "h_male", "gender": 1}
    )
    assert father == "h_male"
    assert mother == "h_female"


def test_assign_parent_handles_single_parent_female():
    father, mother = _assign_parent_handles({"handle": "h_female", "gender": 0}, None)
    assert father is None
    assert mother == "h_female"


def test_assign_parent_handles_single_parent_male():
    father, mother = _assign_parent_handles({"handle": "h_male", "gender": 1}, None)
    assert father == "h_male"
    assert mother is None


def test_assign_parent_handles_same_gender_keeps_call_order():
    father, mother = _assign_parent_handles(
        {"handle": "h_a", "gender": 1}, {"handle": "h_b", "gender": 1}
    )
    assert father == "h_a"
    assert mother == "h_b"


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_add_family_creates_with_two_spouses(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.side_effect = [
        make_response([{"gramps_id": "F0001"}]),  # count before: 1
        make_response([{"gramps_id": "I0024", "handle": "h_male", "gender": 1}]),  # get spouse_a
        make_response([{"gramps_id": "I0050", "handle": "h_female", "gender": 0}]),  # get spouse_b
        make_response(  # family create
            [
                {
                    "type": "add",
                    "handle": "newfamhandle",
                    "_class": "Family",
                    "old": None,
                    "new": {"handle": "newfamhandle", "gramps_id": "F0002"},
                },
                {"type": "update", "handle": "h_male", "_class": "Person", "old": {}, "new": {}},
                {"type": "update", "handle": "h_female", "_class": "Person", "old": {}, "new": {}},
            ],
            status_code=201,
        ),
        make_response([{"gramps_id": "F0001"}, {"gramps_id": "F0002"}]),  # count after: 2
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.add_family("I0024", "I0050")

    assert result == "F0002"
    create_call = mock_request.call_args_list[3]
    assert create_call.args[1] == "https://example.test/api/families/"
    assert create_call.kwargs["json"]["father_handle"] == "h_male"
    assert create_call.kwargs["json"]["mother_handle"] == "h_female"


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_add_family_single_spouse(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.side_effect = [
        make_response([]),  # count before: 0
        make_response([{"gramps_id": "I0024", "handle": "h_male", "gender": 1}]),  # get spouse_a
        make_response(  # family create
            [
                {
                    "type": "add",
                    "handle": "newfamhandle",
                    "_class": "Family",
                    "old": None,
                    "new": {"handle": "newfamhandle", "gramps_id": "F0001"},
                }
            ],
            status_code=201,
        ),
        make_response([{"gramps_id": "F0001"}]),  # count after: 1
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.add_family("I0024")

    assert result == "F0001"
    create_call = mock_request.call_args_list[2]
    assert create_call.kwargs["json"]["father_handle"] == "h_male"
    assert create_call.kwargs["json"]["mother_handle"] is None


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_add_family_count_mismatch_raises(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.side_effect = [
        make_response([]),  # count before: 0
        make_response([{"gramps_id": "I0024", "handle": "h_male", "gender": 1}]),
        make_response(
            [
                {
                    "type": "add",
                    "handle": "newfamhandle",
                    "_class": "Family",
                    "old": None,
                    "new": {"handle": "newfamhandle", "gramps_id": "F0001"},
                }
            ],
            status_code=201,
        ),
        make_response([]),  # count after: still 0 -- mismatch!
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(FamilyCreateCountMismatchError):
        client.add_family("I0024")


from gramps_client import ChildAlreadyInFamilyError


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_add_child_to_family_appends_child_ref(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    family = {
        "gramps_id": "F0002",
        "handle": "famhandle2",
        "child_ref_list": [],
    }
    child = {"gramps_id": "I0163", "handle": "childhandle1"}
    mock_request.side_effect = [
        make_response([family]),  # get_family
        make_response([child]),  # get_person (child)
        make_response(None),  # put family
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.add_child_to_family("F0002", "I0163")

    assert result == {"family_id": "F0002", "child_id": "I0163"}
    put_call = mock_request.call_args_list[2]
    assert put_call.args[0] == "PUT"
    assert put_call.args[1] == "https://example.test/api/families/famhandle2"
    put_body = put_call.kwargs["json"]
    assert len(put_body["child_ref_list"]) == 1
    child_ref = put_body["child_ref_list"][0]
    assert child_ref["_class"] == "ChildRef"
    assert child_ref["ref"] == "childhandle1"
    assert child_ref["private"] is False
    assert child_ref["citation_list"] == []
    assert child_ref["note_list"] == []
    assert child_ref["frel"] == {"_class": "ChildRefType", "value": 1, "string": ""}
    assert child_ref["mrel"] == {"_class": "ChildRefType", "value": 1, "string": ""}


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_add_child_to_family_already_linked_raises(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    family = {
        "gramps_id": "F0002",
        "handle": "famhandle2",
        "child_ref_list": [{"ref": "childhandle1"}],
    }
    child = {"gramps_id": "I0163", "handle": "childhandle1"}
    mock_request.side_effect = [
        make_response([family]),  # get_family
        make_response([child]),  # get_person (child)
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(ChildAlreadyInFamilyError):
        client.add_child_to_family("F0002", "I0163")


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_confirm_person_removes_tag(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    person = {
        "gramps_id": "I0163",
        "handle": "personhandle1",
        "gender": 1,
        "primary_name": {"first_name": "John"},
        "alternate_names": [],
        "tag_list": ["taghandle1", "taghandleother"],
    }
    mock_request.side_effect = [
        make_response([{"handle": "taghandle1", "name": "Unbestätigt"}]),  # tag find
        make_response([{"gramps_id": "I0163"}]),  # count before
        make_response([person]),  # get_person
        make_response(None),  # put
        make_response([{"gramps_id": "I0163"}]),  # count after
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.confirm_person("I0163")

    put_body = mock_request.call_args_list[3].kwargs["json"]
    assert put_body["tag_list"] == ["taghandleother"]
    assert result["before"]["tag_list"] == ["taghandle1", "taghandleother"]
    assert result["after"]["tag_list"] == ["taghandleother"]


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_confirm_person_noop_when_tag_not_on_person(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    person = {
        "gramps_id": "I0024",
        "handle": "personhandle2",
        "gender": 1,
        "primary_name": {"first_name": "John"},
        "alternate_names": [],
        "tag_list": [],
    }
    mock_request.side_effect = [
        make_response([{"handle": "taghandle1", "name": "Unbestätigt"}]),  # tag find
        make_response([{"gramps_id": "I0024"}]),  # count before
        make_response([person]),  # get_person
        make_response(None),  # put
        make_response([{"gramps_id": "I0024"}]),  # count after
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.confirm_person("I0024")

    assert result["before"]["tag_list"] == []
    assert result["after"]["tag_list"] == []


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_confirm_person_noop_when_tag_does_not_exist_at_all(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    person = {
        "gramps_id": "I0024",
        "handle": "personhandle2",
        "gender": 1,
        "primary_name": {"first_name": "John"},
        "alternate_names": [],
        "tag_list": [],
    }
    mock_request.side_effect = [
        make_response([]),  # tag find: no "Unbestätigt" tag exists anywhere
        make_response([{"gramps_id": "I0024"}]),  # count before
        make_response([person]),  # get_person
        make_response(None),  # put
        make_response([{"gramps_id": "I0024"}]),  # count after
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.confirm_person("I0024")

    assert result["after"]["tag_list"] == []


# --- gramps_get_descendants ---

def _person(gramps_id, handle, first_name, surname, gender, family_list=None):
    return {
        "gramps_id": gramps_id,
        "handle": handle,
        "gender": gender,
        "primary_name": {"first_name": first_name, "surname_list": [{"surname": surname}]},
        "family_list": family_list or [],
    }


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_descendants_grade1_two_children(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    root = _person("I0024", "h_john", "John", "Smith", 1, ["f1"])
    family = {"child_ref_list": [{"ref": "h_c1"}, {"ref": "h_c2"}]}
    child1 = _person("I0031", "h_c1", "Alice", "Smith", 0)
    child2 = _person("I0032", "h_c2", "Bob", "Smith", 1)
    mock_request.side_effect = [
        make_response([root]),   # get_person(?gramps_id=I0024)
        make_response(family),   # GET /api/families/f1
        make_response(child1),   # GET /api/people/h_c1
        make_response(child2),   # GET /api/people/h_c2
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    tree = client.get_descendants("I0024", 1)

    assert tree == {
        "gramps_id": "I0024", "first_name": "John", "surname": "Smith",
        "gender": 1, "children": [
            {"gramps_id": "I0031", "first_name": "Alice", "surname": "Smith",
             "gender": 0, "children": []},
            {"gramps_id": "I0032", "first_name": "Bob", "surname": "Smith",
             "gender": 1, "children": []},
        ],
    }
    assert mock_request.call_args_list[1].args[:2] == (
        "GET", "https://example.test/api/families/f1")
    assert mock_request.call_args_list[2].args[:2] == (
        "GET", "https://example.test/api/people/h_c1")


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_descendants_grade2_includes_grandchild_and_stops(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    root = _person("I0024", "h_john", "John", "Smith", 1, ["f1"])
    fam1 = {"child_ref_list": [{"ref": "h_c1"}]}
    child1 = _person("I0031", "h_c1", "Alice", "Smith", 0, ["f2"])
    fam2 = {"child_ref_list": [{"ref": "h_gc1"}]}
    grandchild = _person("I0040", "h_gc1", "Carol", "Clark", 0, ["f3"])
    mock_request.side_effect = [
        make_response([root]),      # get_person
        make_response(fam1),        # families/f1
        make_response(child1),      # people/h_c1
        make_response(fam2),        # families/f2
        make_response(grandchild),  # people/h_gc1
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    tree = client.get_descendants("I0024", 2)

    assert tree["children"][0]["gramps_id"] == "I0031"
    assert tree["children"][0]["children"][0]["gramps_id"] == "I0040"
    assert tree["children"][0]["children"][0]["children"] == []
    assert mock_request.call_count == 5  # f3 (grandchild's family) NOT fetched at grade=2


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_descendants_no_children(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    root = _person("I0024", "h_john", "John", "Smith", 1, [])
    mock_request.side_effect = [make_response([root])]
    client = GrampsClient("https://example.test", "bot", "secret")

    tree = client.get_descendants("I0024", 1)

    assert tree["children"] == []
    assert mock_request.call_count == 1  # only get_person


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_descendants_multiple_families_union(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    root = _person("I0024", "h_john", "John", "Smith", 1, ["f1", "f2"])
    fam1 = {"child_ref_list": [{"ref": "h_c1"}]}
    child1 = _person("I0031", "h_c1", "Alice", "Smith", 0)
    fam2 = {"child_ref_list": [{"ref": "h_c2"}]}
    child2 = _person("I0050", "h_c2", "David", "Smith", 1)
    mock_request.side_effect = [
        make_response([root]), make_response(fam1), make_response(child1),
        make_response(fam2), make_response(child2),
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    tree = client.get_descendants("I0024", 1)

    assert [c["gramps_id"] for c in tree["children"]] == ["I0031", "I0050"]


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_descendants_grade_zero_raises(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(ValueError):
        client.get_descendants("I0024", 0)
    assert mock_request.call_count == 0  # validated before any request


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_descendants_person_not_found(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.side_effect = [make_response([])]  # get_person -> empty
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(PersonNotFoundError):
        client.get_descendants("I9999", 1)


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_descendants_node_missing_name_fields(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    root = {"gramps_id": "I0099", "handle": "h", "family_list": []}  # no primary_name/gender
    mock_request.side_effect = [make_response([root])]
    client = GrampsClient("https://example.test", "bot", "secret")

    tree = client.get_descendants("I0099", 1)

    assert tree == {"gramps_id": "I0099", "first_name": "", "surname": "",
                    "gender": None, "children": []}


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_descendants_defaults_to_grade1(mock_post, mock_request):
    # regression: grade defaults to 1 -> only direct children; a child's own
    # family (the grandchildren) must NOT be fetched when grade is omitted.
    mock_post.return_value = make_response({"access_token": "tok123"})
    root = _person("I0024", "h_john", "John", "Smith", 1, ["f1"])
    fam1 = {"child_ref_list": [{"ref": "h_c1"}]}
    child1 = _person("I0031", "h_c1", "Alice", "Smith", 0, ["f2"])  # child has a family f2
    mock_request.side_effect = [
        make_response([root]),   # get_person
        make_response(fam1),     # families/f1
        make_response(child1),   # people/h_c1
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    tree = client.get_descendants("I0024")  # no grade argument -> default 1

    assert tree["children"][0]["gramps_id"] == "I0031"
    assert tree["children"][0]["children"] == []  # depth 1: not descended further
    assert mock_request.call_count == 3  # f2 (child's family) NOT fetched at default grade


# --- get_ancestors (G2) ---


def _child_person(gramps_id, handle, first_name, surname, gender, parent_family_list=None):
    return {
        "gramps_id": gramps_id,
        "handle": handle,
        "gender": gender,
        "primary_name": {"first_name": first_name, "surname_list": [{"surname": surname}]},
        "parent_family_list": parent_family_list or [],
    }


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_ancestors_grade1_two_parents(mock_post, mock_request):
    # bloodline data: the "father" slot holds a female, the "mother" slot a male.
    # The tree must report each parent's OWN gender, never the slot's implied one.
    mock_post.return_value = make_response({"access_token": "tok123"})
    child = _child_person("I0031", "h_child", "Josef", "Prentl", 1, ["f1"])
    fam1 = {"father_handle": "h_ala", "mother_handle": "h_franz"}
    ala = _child_person("I0036", "h_ala", "Ala", "Prentl", 0)      # female in father slot
    franz = _child_person("I0037", "h_franz", "Franz", "Huber", 1)  # male in mother slot
    mock_request.side_effect = [
        make_response([child]),   # get_person(?gramps_id=I0031)
        make_response(fam1),      # GET /api/families/f1
        make_response(ala),       # GET /api/people/h_ala
        make_response(franz),     # GET /api/people/h_franz
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    tree = client.get_ancestors("I0031", 1)

    assert tree == {
        "gramps_id": "I0031", "first_name": "Josef", "surname": "Prentl",
        "gender": 1, "parents": [
            {"gramps_id": "I0036", "first_name": "Ala", "surname": "Prentl",
             "gender": 0, "parents": []},
            {"gramps_id": "I0037", "first_name": "Franz", "surname": "Huber",
             "gender": 1, "parents": []},
        ],
    }
    assert mock_request.call_args_list[1].args[:2] == (
        "GET", "https://example.test/api/families/f1")
    assert mock_request.call_args_list[2].args[:2] == (
        "GET", "https://example.test/api/people/h_ala")


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_ancestors_single_parent_skips_empty_slot(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    child = _child_person("I0031", "h_child", "Josef", "Prentl", 1, ["f1"])
    fam1 = {"father_handle": "h_franz", "mother_handle": None}  # only one known parent
    franz = _child_person("I0037", "h_franz", "Franz", "Prentl", 1)
    mock_request.side_effect = [
        make_response([child]),
        make_response(fam1),
        make_response(franz),
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    tree = client.get_ancestors("I0031", 1)

    assert [p["gramps_id"] for p in tree["parents"]] == ["I0037"]
    assert mock_request.call_count == 3  # missing mother slot triggers no person fetch


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_ancestors_grade2_includes_grandparent_and_stops(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    child = _child_person("I0031", "h_child", "Josef", "Prentl", 1, ["f1"])
    fam1 = {"father_handle": "h_franz", "mother_handle": None}
    franz = _child_person("I0037", "h_franz", "Franz", "Prentl", 1, ["f2"])
    fam2 = {"father_handle": "h_opa", "mother_handle": None}
    opa = _child_person("I0040", "h_opa", "Anton", "Prentl", 1, ["f3"])
    mock_request.side_effect = [
        make_response([child]),   # get_person
        make_response(fam1),      # families/f1
        make_response(franz),     # people/h_franz
        make_response(fam2),      # families/f2
        make_response(opa),       # people/h_opa
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    tree = client.get_ancestors("I0031", 2)

    assert tree["parents"][0]["gramps_id"] == "I0037"
    assert tree["parents"][0]["parents"][0]["gramps_id"] == "I0040"
    assert tree["parents"][0]["parents"][0]["parents"] == []
    assert mock_request.call_count == 5  # f3 (grandparent's family) NOT fetched at grade=2


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_ancestors_no_parents(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    root = _child_person("I0031", "h_child", "Josef", "Prentl", 1, [])
    mock_request.side_effect = [make_response([root])]
    client = GrampsClient("https://example.test", "bot", "secret")

    tree = client.get_ancestors("I0031", 1)

    assert tree["parents"] == []
    assert mock_request.call_count == 1  # only get_person


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_ancestors_grade_zero_raises(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(ValueError):
        client.get_ancestors("I0031", 0)
    assert mock_request.call_count == 0  # validated before any request


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_ancestors_person_not_found(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.side_effect = [make_response([])]  # get_person -> empty
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(PersonNotFoundError):
        client.get_ancestors("I9999", 1)


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_ancestors_defaults_to_grade1(mock_post, mock_request):
    # regression: grade defaults to 1 -> only direct parents; a parent's own
    # parent-family must NOT be fetched when grade is omitted.
    mock_post.return_value = make_response({"access_token": "tok123"})
    child = _child_person("I0031", "h_child", "Josef", "Prentl", 1, ["f1"])
    fam1 = {"father_handle": "h_franz", "mother_handle": None}
    franz = _child_person("I0037", "h_franz", "Franz", "Prentl", 1, ["f2"])  # has parents
    mock_request.side_effect = [
        make_response([child]),
        make_response(fam1),
        make_response(franz),
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    tree = client.get_ancestors("I0031")  # no grade argument -> default 1

    assert tree["parents"][0]["gramps_id"] == "I0037"
    assert tree["parents"][0]["parents"] == []
    assert mock_request.call_count == 3  # f2 (parent's family) NOT fetched at default grade


# --- get_relations (G3) ---


def _root_person(gramps_id, handle, first_name, surname, gender,
                 parent_family_list=None, family_list=None):
    return {
        "gramps_id": gramps_id,
        "handle": handle,
        "gender": gender,
        "primary_name": {"first_name": first_name, "surname_list": [{"surname": surname}]},
        "parent_family_list": parent_family_list or [],
        "family_list": family_list or [],
    }


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_relations_full_slots_are_bloodline_not_gender(mock_post, mock_request):
    # Root "Ala" is FEMALE but sits in her family's father slot; her partner Franz
    # is MALE in the mother slot. Relations must report the partner as the OTHER
    # slot (regardless of sex) and each person's own gender, never the slot's.
    mock_post.return_value = make_response({"access_token": "tok123"})
    root = _root_person("I0036", "h_ala", "Ala", "Prentl", 0,
                        parent_family_list=["f_parent"], family_list=["f_own"])
    f_parent = {"gramps_id": "F_P", "father_handle": "h_opa", "mother_handle": "h_oma"}
    opa = _person("I0100", "h_opa", "Opa", "Prentl", 1)
    oma = _person("I0101", "h_oma", "Oma", "Prentl", 0)
    f_own = {
        "gramps_id": "F_O", "father_handle": "h_ala", "mother_handle": "h_franz",
        "child_ref_list": [{"ref": "h_c1"}, {"ref": "h_c2"}],
    }
    franz = _person("I0037", "h_franz", "Franz", "Huber", 1)
    kind1 = _person("I0060", "h_c1", "Kind1", "Prentl", 1)
    kind2 = _person("I0061", "h_c2", "Kind2", "Prentl", 0)
    mock_request.side_effect = [
        make_response([root]),      # get_person(?gramps_id=I0036)
        make_response(f_parent),    # families/f_parent
        make_response(opa),         # people/h_opa (father slot)
        make_response(oma),         # people/h_oma (mother slot)
        make_response(f_own),       # families/f_own
        make_response(franz),       # people/h_franz (partner = other slot)
        make_response(kind1),       # people/h_c1
        make_response(kind2),       # people/h_c2
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    rel = client.get_relations("I0036")

    assert rel == {
        "gramps_id": "I0036", "first_name": "Ala", "surname": "Prentl", "gender": 0,
        "parent_families": [
            {
                "family_gramps_id": "F_P",
                "father": {"gramps_id": "I0100", "first_name": "Opa",
                           "surname": "Prentl", "gender": 1},
                "mother": {"gramps_id": "I0101", "first_name": "Oma",
                           "surname": "Prentl", "gender": 0},
            }
        ],
        "families": [
            {
                "family_gramps_id": "F_O",
                "partner": {"gramps_id": "I0037", "first_name": "Franz",
                            "surname": "Huber", "gender": 1},
                "children": [
                    {"gramps_id": "I0060", "first_name": "Kind1",
                     "surname": "Prentl", "gender": 1},
                    {"gramps_id": "I0061", "first_name": "Kind2",
                     "surname": "Prentl", "gender": 0},
                ],
            }
        ],
    }


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_relations_partner_is_the_other_slot_when_root_is_mother(mock_post, mock_request):
    # mirror: root sits in the MOTHER slot -> partner must be the father slot.
    mock_post.return_value = make_response({"access_token": "tok123"})
    root = _root_person("I0037", "h_franz", "Franz", "Huber", 1, family_list=["f_own"])
    f_own = {
        "gramps_id": "F_O", "father_handle": "h_ala", "mother_handle": "h_franz",
        "child_ref_list": [],
    }
    ala = _person("I0036", "h_ala", "Ala", "Prentl", 0)
    mock_request.side_effect = [
        make_response([root]),    # get_person
        make_response(f_own),     # families/f_own
        make_response(ala),       # people/h_ala (partner = father slot)
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    rel = client.get_relations("I0037")

    assert rel["families"][0]["partner"]["gramps_id"] == "I0036"
    assert rel["families"][0]["children"] == []


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_relations_no_families_returns_empty_lists(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    root = _root_person("I0036", "h_ala", "Ala", "Prentl", 0)
    mock_request.side_effect = [make_response([root])]
    client = GrampsClient("https://example.test", "bot", "secret")

    rel = client.get_relations("I0036")

    assert rel["parent_families"] == []
    assert rel["families"] == []
    assert mock_request.call_count == 1  # only get_person; no family/person fetches


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_relations_parent_family_with_single_known_parent(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    root = _root_person("I0036", "h_ala", "Ala", "Prentl", 0, parent_family_list=["f_parent"])
    f_parent = {"gramps_id": "F_P", "father_handle": "h_opa", "mother_handle": None}
    opa = _person("I0100", "h_opa", "Opa", "Prentl", 1)
    mock_request.side_effect = [
        make_response([root]),
        make_response(f_parent),
        make_response(opa),
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    rel = client.get_relations("I0036")

    assert rel["parent_families"][0]["father"]["gramps_id"] == "I0100"
    assert rel["parent_families"][0]["mother"] is None
    assert mock_request.call_count == 3  # empty mother slot triggers no person fetch


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_relations_own_family_without_partner_or_children(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    root = _root_person("I0036", "h_ala", "Ala", "Prentl", 0, family_list=["f_own"])
    f_own = {"gramps_id": "F_O", "father_handle": "h_ala", "mother_handle": None,
             "child_ref_list": []}
    mock_request.side_effect = [
        make_response([root]),
        make_response(f_own),
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    rel = client.get_relations("I0036")

    assert rel["families"][0]["partner"] is None
    assert rel["families"][0]["children"] == []
    assert mock_request.call_count == 2  # only get_person + family; no partner/child fetches


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_get_relations_person_not_found(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.side_effect = [make_response([])]  # get_person -> empty
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(PersonNotFoundError):
        client.get_relations("I9999")


# --- list_people (G4) ---


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_list_people_no_args_fetches_all(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [{"gramps_id": "I0001"}, {"gramps_id": "I0002"}]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.list_people()

    assert result == [{"gramps_id": "I0001"}, {"gramps_id": "I0002"}]
    mock_request.assert_called_once_with(
        "GET",
        "https://example.test/api/people/",
        json=None,
        headers={"Authorization": "Bearer tok123"},
        timeout=10,
    )


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_list_people_with_keys(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response([{"gramps_id": "I0001", "gender": 1}])
    client = GrampsClient("https://example.test", "bot", "secret")

    client.list_people(keys=["gramps_id", "gender", "family_list"])

    mock_request.assert_called_once_with(
        "GET",
        "https://example.test/api/people/?keys=gramps_id,gender,family_list",
        json=None,
        headers={"Authorization": "Bearer tok123"},
        timeout=10,
    )


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_list_people_with_keys_and_pagination(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response([])
    client = GrampsClient("https://example.test", "bot", "secret")

    client.list_people(keys=["gramps_id"], page=2, pagesize=50)

    mock_request.assert_called_once_with(
        "GET",
        "https://example.test/api/people/?keys=gramps_id&page=2&pagesize=50",
        json=None,
        headers={"Authorization": "Bearer tok123"},
        timeout=10,
    )


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_list_people_pagesize_only_defaults_page_to_1(mock_post, mock_request):
    # gramps-web-api only paginates when page >= 1; a bare pagesize is ignored and
    # the WHOLE list is returned (verified against live INT: pagesize=3 -> 160 rows).
    # The wrapper defaults page to 1 so pagesize actually caps the result.
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response([])
    client = GrampsClient("https://example.test", "bot", "secret")

    client.list_people(pagesize=20)

    mock_request.assert_called_once_with(
        "GET",
        "https://example.test/api/people/?page=1&pagesize=20",
        json=None,
        headers={"Authorization": "Bearer tok123"},
        timeout=10,
    )


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_list_people_no_pagination_stays_unpaginated(mock_post, mock_request):
    # neither page nor pagesize -> no page param injected -> server returns all
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response([])
    client = GrampsClient("https://example.test", "bot", "secret")

    client.list_people(keys=["gramps_id"])

    mock_request.assert_called_once_with(
        "GET",
        "https://example.test/api/people/?keys=gramps_id",
        json=None,
        headers={"Authorization": "Bearer tok123"},
        timeout=10,
    )


# --- object_counts (G6) ---


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_object_counts_returns_object_counts_from_metadata(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        {"object_counts": {"people": 160, "families": 50, "events": 200}}
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    counts = client.object_counts()

    assert counts == {"people": 160, "families": 50, "events": 200}
    mock_request.assert_called_once_with(
        "GET",
        "https://example.test/api/metadata/",
        json=None,
        headers={"Authorization": "Bearer tok123"},
        timeout=10,
    )


# --- set_gender_bulk / set_surname_bulk (G5) ---

from gramps_client import PersonNotFoundError as _PNF  # noqa: E402  (grouped with G5)


def _writable_person(gramps_id, handle, gender=2, surname=""):
    return {
        "gramps_id": gramps_id,
        "handle": handle,
        "gender": gender,
        "primary_name": {"first_name": "X", "surname_list": [{"surname": surname}]},
        "alternate_names": [],
    }


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_gender_bulk_updates_multiple_with_single_count_guard(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    p1 = _writable_person("I0031", "h1", gender=2)
    p2 = _writable_person("I0032", "h2", gender=2)
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0031"}, {"gramps_id": "I0032"}]),  # count before: 2
        make_response([p1]),   # get_person I0031
        make_response(None),   # put I0031
        make_response([p2]),   # get_person I0032
        make_response(None),   # put I0032
        make_response([{"gramps_id": "I0031"}, {"gramps_id": "I0032"}]),  # count after: 2
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.set_gender_bulk(
        [{"gramps_id": "I0031", "gender": 0}, {"gramps_id": "I0032", "gender": 1}]
    )

    # exactly ONE count before + ONE count after wrap the whole batch (6 reqs total)
    assert mock_request.call_count == 6
    put1 = mock_request.call_args_list[2]
    put2 = mock_request.call_args_list[4]
    assert put1.args[0] == "PUT" and put1.kwargs["json"]["gender"] == 0
    assert put2.args[0] == "PUT" and put2.kwargs["json"]["gender"] == 1
    assert result["count_before"] == 2
    assert result["count_after"] == 2
    assert result["count_guard_ok"] is True
    assert [r["gramps_id"] for r in result["results"]] == ["I0031", "I0032"]
    assert result["results"][0]["before"]["gender"] == 2
    assert result["results"][0]["after"]["gender"] == 0
    assert result["errors"] == []


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_gender_bulk_partial_failure_does_not_abort_rest(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    p1 = _writable_person("I0031", "h1")
    p3 = _writable_person("I0033", "h3")
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0031"}]),  # count before
        make_response([p1]),    # get I0031 -> ok
        make_response(None),     # put I0031
        make_response([]),       # get I0032 -> empty -> PersonNotFoundError
        make_response([p3]),     # get I0033 -> ok
        make_response(None),     # put I0033
        make_response([{"gramps_id": "I0031"}]),  # count after
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.set_gender_bulk(
        [
            {"gramps_id": "I0031", "gender": 0},
            {"gramps_id": "I0032", "gender": 1},  # this one fails
            {"gramps_id": "I0033", "gender": 1},
        ]
    )

    assert [r["gramps_id"] for r in result["results"]] == ["I0031", "I0033"]
    assert len(result["errors"]) == 1
    assert result["errors"][0]["gramps_id"] == "I0032"
    assert "PersonNotFoundError" in result["errors"][0]["error"]


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_gender_bulk_count_guard_reports_mismatch_without_raising(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    p1 = _writable_person("I0031", "h1")
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0031"}, {"gramps_id": "I0032"}]),  # before: 2
        make_response([p1]),
        make_response(None),
        make_response([{"gramps_id": "I0031"}]),  # after: 1 -- mismatch!
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.set_gender_bulk([{"gramps_id": "I0031", "gender": 0}])

    # reports the anomaly instead of raising (would otherwise discard partial results)
    assert result["count_before"] == 2
    assert result["count_after"] == 1
    assert result["count_guard_ok"] is False
    assert [r["gramps_id"] for r in result["results"]] == ["I0031"]


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_gender_bulk_empty_items(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0031"}]),  # count before
        make_response([{"gramps_id": "I0031"}]),  # count after
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.set_gender_bulk([])

    assert result["results"] == []
    assert result["errors"] == []
    assert result["count_guard_ok"] is True


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_surname_bulk_updates_multiple(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    p1 = _writable_person("I0036", "h1", surname="")
    p2 = _writable_person("I0037", "h2", surname="")
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0036"}, {"gramps_id": "I0037"}]),  # before
        make_response([p1]),
        make_response(None),
        make_response([p2]),
        make_response(None),
        make_response([{"gramps_id": "I0036"}, {"gramps_id": "I0037"}]),  # after
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.set_surname_bulk(
        [{"gramps_id": "I0036", "surname": "Prentl"}, {"gramps_id": "I0037", "surname": "Huber"}]
    )

    put1 = mock_request.call_args_list[2].kwargs["json"]
    put2 = mock_request.call_args_list[4].kwargs["json"]
    assert put1["primary_name"]["surname_list"][0]["surname"] == "Prentl"
    assert put2["primary_name"]["surname_list"][0]["surname"] == "Huber"
    assert [r["gramps_id"] for r in result["results"]] == ["I0036", "I0037"]
    assert result["errors"] == []


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_surname_bulk_applies_optional_name_type(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    p1 = _writable_person("I0036", "h1", surname="Prentl")
    p1["primary_name"]["type"] = "Birth Name"
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0036"}]),
        make_response([p1]),
        make_response(None),
        make_response([{"gramps_id": "I0036"}]),
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    client.set_surname_bulk(
        [{"gramps_id": "I0036", "surname": "Huber", "name_type": "Married Name"}]
    )

    # requests: [0] count-before, [1] get_person, [2] PUT, [3] count-after
    put_body = mock_request.call_args_list[2].kwargs["json"]
    assert put_body["primary_name"]["surname_list"][0]["surname"] == "Huber"
    assert put_body["primary_name"]["type"] == "Married Name"


# --- pre-publish review fixes ---


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_gender_bulk_item_missing_gramps_id_is_recorded_not_aborting(mock_post, mock_request):
    # review finding: a bulk item lacking "gramps_id" must be recorded in errors and
    # must NOT abort the batch or discard results already collected for earlier items.
    mock_post.return_value = make_response({"access_token": "tok123"})
    p1 = _writable_person("I0031", "h1")
    mock_request.side_effect = [
        make_response([{"gramps_id": "I0031"}]),  # count before
        make_response([p1]),                        # get I0031
        make_response(None),                        # put I0031
        make_response([{"gramps_id": "I0031"}]),   # count after (batch NOT aborted)
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.set_gender_bulk(
        [{"gramps_id": "I0031", "gender": 0}, {"gender": 1}]  # 2nd item malformed
    )

    assert [r["gramps_id"] for r in result["results"]] == ["I0031"]  # earlier write preserved
    assert len(result["errors"]) == 1
    assert result["errors"][0]["gramps_id"] is None  # unknown id -> None, not a crash
    assert "KeyError" in result["errors"][0]["error"]
    assert result["count_after"] == 1  # count-after still reported
    assert result["count_guard_ok"] is True


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_search_person_negative_limit_returns_no_results(mock_post, mock_request):
    # review finding: a negative limit must not silently drop the TAIL of the matches
    # (matches[:-1]); a non-positive cap yields no results.
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response(
        [
            {"gramps_id": "I0001", "gender": 1,
             "primary_name": {"first_name": "Hans", "surname_list": [{"surname": "Prentl"}]}},
            {"gramps_id": "I0002", "gender": 1,
             "primary_name": {"first_name": "Josef", "surname_list": [{"surname": "Prentl"}]}},
            {"gramps_id": "I0003", "gender": 0,
             "primary_name": {"first_name": "Maria", "surname_list": [{"surname": "Prentl"}]}},
        ]
    )
    client = GrampsClient("https://example.test", "bot", "secret")

    assert client.search_person("prentl", limit=-1) == []
    assert client.search_person("prentl", limit=0) == []


# --- set_family_parent (G8) ---


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_family_parent_sets_mother_handle(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    family = {
        "gramps_id": "F0005",
        "handle": "famhandle5",
        "father_handle": "h_father",
        "mother_handle": None,
    }
    person = {"gramps_id": "I0091", "handle": "h_tanja", "gender": 0}
    mock_request.side_effect = [
        make_response([family]),   # get_family
        make_response([person]),   # get_person
        make_response(None),       # put family
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.set_family_parent("F0005", "I0091", "mother")

    put_call = mock_request.call_args_list[2]
    assert put_call.args[0] == "PUT"
    assert put_call.args[1] == "https://example.test/api/families/famhandle5"
    put_body = put_call.kwargs["json"]
    assert put_body["mother_handle"] == "h_tanja"
    assert put_body["father_handle"] == "h_father"  # untouched slot preserved
    assert result == {
        "family_id": "F0005",
        "gramps_id": "I0091",
        "role": "mother",
        "previous": None,
    }


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_family_parent_sets_father_handle(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    family = {
        "gramps_id": "F0005",
        "handle": "famhandle5",
        "father_handle": None,
        "mother_handle": "h_mother",
    }
    person = {"gramps_id": "I0085", "handle": "h_philip", "gender": 1}
    mock_request.side_effect = [
        make_response([family]),
        make_response([person]),
        make_response(None),
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.set_family_parent("F0005", "I0085", "father")

    put_body = mock_request.call_args_list[2].kwargs["json"]
    assert put_body["father_handle"] == "h_philip"
    assert put_body["mother_handle"] == "h_mother"  # untouched slot preserved
    assert result["role"] == "father"
    assert result["previous"] is None


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_family_parent_role_is_explicit_not_gender(mock_post, mock_request):
    # headline G8 contract: father/mother are BLOODLINE slots, not gender. A FEMALE
    # person (gender 0) explicitly placed in the "father" slot must land there
    # verbatim; the tool must NOT reorder by sex the way add_family does.
    mock_post.return_value = make_response({"access_token": "tok123"})
    family = {"gramps_id": "F0009", "handle": "fh9", "father_handle": None,
              "mother_handle": None}
    female = {"gramps_id": "I0036", "handle": "h_ala", "gender": 0}
    mock_request.side_effect = [
        make_response([family]),
        make_response([female]),
        make_response(None),
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    client.set_family_parent("F0009", "I0036", "father")

    put_body = mock_request.call_args_list[2].kwargs["json"]
    assert put_body["father_handle"] == "h_ala"   # female in the father slot, as asked
    assert put_body["mother_handle"] is None


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_family_parent_replaces_existing_returns_previous(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    family = {"gramps_id": "F0005", "handle": "famhandle5",
              "father_handle": "h_father", "mother_handle": "h_old_mother"}
    person = {"gramps_id": "I0091", "handle": "h_tanja", "gender": 0}
    old_mother = {
        "gramps_id": "I0050", "handle": "h_old_mother", "gender": 0,
        "primary_name": {"first_name": "Maria", "surname_list": [{"surname": "Meyer"}]},
    }
    mock_request.side_effect = [
        make_response([family]),    # get_family
        make_response([person]),    # get_person (the new mother)
        make_response(old_mother),  # _summary_for_handle -> GET the displaced parent
        make_response(None),        # put family
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.set_family_parent("F0005", "I0091", "mother")

    put_body = mock_request.call_args_list[3].kwargs["json"]
    assert put_body["mother_handle"] == "h_tanja"
    # the displaced parent is resolved to a consumable gramps_id summary, not a raw handle
    assert result["previous"] == {
        "gramps_id": "I0050", "first_name": "Maria", "surname": "Meyer", "gender": 0,
    }


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_family_parent_rejects_same_person_as_both_parents(mock_post, mock_request):
    # a family cannot have one person as father AND mother: the person already in the
    # father slot must not be settable into the mother slot -> ValueError, and no PUT.
    mock_post.return_value = make_response({"access_token": "tok123"})
    family = {"gramps_id": "F0005", "handle": "famhandle5",
              "father_handle": "h_tanja", "mother_handle": None}
    person = {"gramps_id": "I0091", "handle": "h_tanja", "gender": 0}
    mock_request.side_effect = [
        make_response([family]),   # get_family
        make_response([person]),   # get_person
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(ValueError):
        client.set_family_parent("F0005", "I0091", "mother")
    assert mock_request.call_count == 2  # rejected before the PUT


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_family_parent_invalid_role_raises_before_any_request(mock_post, mock_request):
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(ValueError):
        client.set_family_parent("F0005", "I0091", "parent")
    mock_request.assert_not_called()  # validated before touching the network


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_family_parent_family_not_found_raises(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response([])  # get_family -> empty
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(FamilyNotFoundError):
        client.set_family_parent("F9999", "I0091", "mother")


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_set_family_parent_person_not_found_raises(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    family = {"gramps_id": "F0005", "handle": "famhandle5",
              "father_handle": None, "mother_handle": None}
    mock_request.side_effect = [
        make_response([family]),   # get_family ok
        make_response([]),         # get_person -> empty
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(PersonNotFoundError):
        client.set_family_parent("F0005", "I9999", "mother")


# --- remove_child_from_family (G7) ---

from gramps_client import ChildNotInFamilyError


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_remove_child_from_family_removes_ref(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    family = {
        "gramps_id": "F0003",
        "handle": "famhandle3",
        "child_ref_list": [{"_class": "ChildRef", "ref": "childhandle1"}],
    }
    child = {"gramps_id": "I0091", "handle": "childhandle1"}
    mock_request.side_effect = [
        make_response([family]),   # get_family
        make_response([child]),    # get_person
        make_response(None),       # put family
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.remove_child_from_family("F0003", "I0091")

    put_call = mock_request.call_args_list[2]
    assert put_call.args[0] == "PUT"
    assert put_call.args[1] == "https://example.test/api/families/famhandle3"
    assert put_call.kwargs["json"]["child_ref_list"] == []  # ref gone
    assert result == {"family_id": "F0003", "child_id": "I0091"}


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_remove_child_from_family_leaves_siblings(mock_post, mock_request):
    # removing one child must NOT drop the others from child_ref_list
    mock_post.return_value = make_response({"access_token": "tok123"})
    family = {
        "gramps_id": "F0003",
        "handle": "famhandle3",
        "child_ref_list": [
            {"_class": "ChildRef", "ref": "h_keep1"},
            {"_class": "ChildRef", "ref": "childhandle1"},  # the one to remove
            {"_class": "ChildRef", "ref": "h_keep2"},
        ],
    }
    child = {"gramps_id": "I0091", "handle": "childhandle1"}
    mock_request.side_effect = [
        make_response([family]),
        make_response([child]),
        make_response(None),
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    client.remove_child_from_family("F0003", "I0091")

    remaining = [r["ref"] for r in mock_request.call_args_list[2].kwargs["json"]["child_ref_list"]]
    assert remaining == ["h_keep1", "h_keep2"]  # siblings preserved, order kept


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_remove_child_from_family_not_a_child_raises(mock_post, mock_request):
    # the person is not a child of this family -> refuse, and do NOT PUT anything
    mock_post.return_value = make_response({"access_token": "tok123"})
    family = {
        "gramps_id": "F0003",
        "handle": "famhandle3",
        "child_ref_list": [{"_class": "ChildRef", "ref": "h_other"}],
    }
    child = {"gramps_id": "I0091", "handle": "childhandle1"}
    mock_request.side_effect = [
        make_response([family]),   # get_family
        make_response([child]),    # get_person
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(ChildNotInFamilyError):
        client.remove_child_from_family("F0003", "I0091")
    assert mock_request.call_count == 2  # no PUT issued


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_remove_child_from_family_family_not_found_raises(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response([])  # get_family -> empty
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(FamilyNotFoundError):
        client.remove_child_from_family("F9999", "I0091")


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_remove_child_from_family_person_not_found_raises(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    family = {"gramps_id": "F0003", "handle": "famhandle3", "child_ref_list": []}
    mock_request.side_effect = [
        make_response([family]),   # get_family ok
        make_response([]),         # get_person -> empty
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(PersonNotFoundError):
        client.remove_child_from_family("F0003", "I9999")


# --- delete_person (G9, DESTRUCTIVE) ---

from gramps_client import PersonDeleteCountMismatchError


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_delete_person_requires_confirm(mock_post, mock_request):
    # destructive: without confirm=True it must refuse BEFORE any network call
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(ValueError):
        client.delete_person("I0091")           # confirm defaults to False
    with pytest.raises(ValueError):
        client.delete_person("I0091", confirm=False)
    mock_request.assert_not_called()


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_delete_person_deletes_and_guards_count(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    person = {"gramps_id": "I0091", "handle": "h_tanja"}
    mock_request.side_effect = [
        make_response([person]),   # get_person
        make_response([{"gramps_id": "I0001"}, {"gramps_id": "I0091"}]),  # count before: 2
        make_response([{"type": "delete", "handle": "h_tanja", "_class": "Person"}]),  # DELETE
        make_response([{"gramps_id": "I0001"}]),  # count after: 1
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.delete_person("I0091", confirm=True)

    delete_call = mock_request.call_args_list[2]
    assert delete_call.args[0] == "DELETE"
    assert delete_call.args[1] == "https://example.test/api/people/h_tanja"
    assert result == {
        "gramps_id": "I0091",
        "deleted": True,
        "count_before": 2,
        "count_after": 1,
        "deleted_notes": [],  # this person had no attached notes
    }


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_delete_person_count_mismatch_raises(mock_post, mock_request):
    # count must drop by exactly one; anything else (e.g. cascade) is surfaced
    mock_post.return_value = make_response({"access_token": "tok123"})
    person = {"gramps_id": "I0091", "handle": "h_tanja"}
    mock_request.side_effect = [
        make_response([person]),   # get_person
        make_response([{"gramps_id": "I0001"}, {"gramps_id": "I0091"}]),  # before: 2
        make_response([{"type": "delete", "handle": "h_tanja", "_class": "Person"}]),
        make_response([{"gramps_id": "I0001"}, {"gramps_id": "I0091"}]),  # after: still 2!
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(PersonDeleteCountMismatchError):
        client.delete_person("I0091", confirm=True)


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_delete_person_not_found_raises(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response([])  # get_person -> empty
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(PersonNotFoundError):
        client.delete_person("I9999", confirm=True)


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_delete_person_confirm_must_be_literal_true(mock_post, mock_request):
    # the docstring promises "a stray truthy value cannot delete": only the literal
    # True proceeds. Every other truthy value must raise BEFORE any network call, so a
    # refactor of `confirm is not True` to `if not confirm:` cannot silently start
    # deleting on confirm=1 / "yes" while the suite stays green.
    client = GrampsClient("https://example.test", "bot", "secret")

    for truthy in (1, "yes", "true", [1], {"ok": 1}):
        with pytest.raises(ValueError):
            client.delete_person("I0091", confirm=truthy)
    mock_request.assert_not_called()


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_delete_person_cleans_up_orphaned_note(mock_post, mock_request):
    # a note attached ONLY to the deleted person becomes orphaned (empty backlinks)
    # and must be deleted too, so the notes count doesn't drift.
    mock_post.return_value = make_response({"access_token": "tok123"})
    person = {"gramps_id": "I0649", "handle": "h_p", "note_list": ["h_note1"]}
    mock_request.side_effect = [
        make_response([person]),   # get_person
        make_response([{"gramps_id": "I0001"}, {"gramps_id": "I0649"}]),  # count before: 2
        make_response([{"type": "delete", "handle": "h_p", "_class": "Person"}]),  # DELETE person
        make_response([{"gramps_id": "I0001"}]),  # count after: 1
        make_response({"handle": "h_note1", "backlinks": {}}),  # GET note backlinks -> orphaned
        make_response([{"type": "delete", "handle": "h_note1", "_class": "Note"}]),  # DELETE note
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.delete_person("I0649", confirm=True)

    note_get = mock_request.call_args_list[4]
    assert note_get.args == ("GET", "https://example.test/api/notes/h_note1?backlinks=1")
    note_del = mock_request.call_args_list[5]
    assert note_del.args[0] == "DELETE"
    assert note_del.args[1] == "https://example.test/api/notes/h_note1"
    assert result["deleted_notes"] == ["h_note1"]
    assert result["deleted"] is True


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_delete_person_keeps_shared_note(mock_post, mock_request):
    # a note still referenced elsewhere (non-empty backlinks) is SHARED and must be
    # left intact — deleting it would corrupt the other object.
    mock_post.return_value = make_response({"access_token": "tok123"})
    person = {"gramps_id": "I0649", "handle": "h_p", "note_list": ["h_shared"]}
    mock_request.side_effect = [
        make_response([person]),   # get_person
        make_response([{"gramps_id": "I0001"}, {"gramps_id": "I0649"}]),  # before: 2
        make_response([{"type": "delete", "handle": "h_p", "_class": "Person"}]),  # DELETE person
        make_response([{"gramps_id": "I0001"}]),  # after: 1
        make_response({"handle": "h_shared", "backlinks": {"family": ["h_fam"]}}),  # still referenced
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.delete_person("I0649", confirm=True)

    assert result["deleted_notes"] == []          # nothing deleted
    assert mock_request.call_count == 5            # no DELETE issued for the shared note


# --- delete_family (G10, DESTRUCTIVE) ---

from gramps_client import FamilyNotEmptyError, FamilyDeleteCountMismatchError


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_delete_family_requires_confirm(mock_post, mock_request):
    # destructive: without confirm=True it must refuse BEFORE any network call
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(ValueError):
        client.delete_family("F0031")            # confirm defaults to False
    with pytest.raises(ValueError):
        client.delete_family("F0031", confirm=False)
    mock_request.assert_not_called()


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_delete_family_deletes_childless_and_guards_count(mock_post, mock_request):
    # a partnered but childless (orphaned) family is deletable; parents are fine
    mock_post.return_value = make_response({"access_token": "tok123"})
    family = {"gramps_id": "F0031", "handle": "fh31",
              "father_handle": "h_dad", "mother_handle": None, "child_ref_list": []}
    mock_request.side_effect = [
        make_response([family]),   # get_family
        make_response([{"gramps_id": "F0001"}, {"gramps_id": "F0031"}]),  # count before: 2
        make_response([{"type": "delete", "handle": "fh31", "_class": "Family"}]),  # DELETE
        make_response([{"gramps_id": "F0001"}]),  # count after: 1
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    result = client.delete_family("F0031", confirm=True)

    delete_call = mock_request.call_args_list[2]
    assert delete_call.args[0] == "DELETE"
    assert delete_call.args[1] == "https://example.test/api/families/fh31"
    assert result == {
        "family_id": "F0031", "deleted": True, "count_before": 2, "count_after": 1,
    }


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_delete_family_refuses_when_children_remain(mock_post, mock_request):
    # never silently orphan children: a family that still has kids must not be deleted
    mock_post.return_value = make_response({"access_token": "tok123"})
    family = {"gramps_id": "F0031", "handle": "fh31",
              "child_ref_list": [{"_class": "ChildRef", "ref": "h_kid"}]}
    mock_request.side_effect = [
        make_response([family]),   # get_family
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(FamilyNotEmptyError):
        client.delete_family("F0031", confirm=True)
    assert mock_request.call_count == 1  # rejected before any DELETE


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_delete_family_count_mismatch_raises(mock_post, mock_request):
    # count must drop by exactly one; anything else is surfaced
    mock_post.return_value = make_response({"access_token": "tok123"})
    family = {"gramps_id": "F0031", "handle": "fh31", "child_ref_list": []}
    mock_request.side_effect = [
        make_response([family]),
        make_response([{"gramps_id": "F0001"}, {"gramps_id": "F0031"}]),  # before: 2
        make_response([{"type": "delete", "handle": "fh31", "_class": "Family"}]),
        make_response([{"gramps_id": "F0001"}, {"gramps_id": "F0031"}]),  # after: still 2!
    ]
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(FamilyDeleteCountMismatchError):
        client.delete_family("F0031", confirm=True)


@patch("gramps_client.requests.request")
@patch("gramps_client.requests.post")
def test_delete_family_not_found_raises(mock_post, mock_request):
    mock_post.return_value = make_response({"access_token": "tok123"})
    mock_request.return_value = make_response([])  # get_family -> empty
    client = GrampsClient("https://example.test", "bot", "secret")

    with pytest.raises(FamilyNotFoundError):
        client.delete_family("F9999", confirm=True)


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
