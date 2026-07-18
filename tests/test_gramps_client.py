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
        "https://example.test/api/people/?keys=gramps_id,primary_name,gender",
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
