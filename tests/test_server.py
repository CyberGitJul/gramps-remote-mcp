from unittest.mock import MagicMock

from server import create_server


def test_gramps_set_gender_calls_client():
    client = MagicMock()
    client.set_gender.return_value = {"gramps_id": "I0024", "before": {}, "after": {}}
    _, tools = create_server(client)

    result = tools["gramps_set_gender"]("I0024", 1)

    client.set_gender.assert_called_once_with("I0024", 1)
    assert result == {"gramps_id": "I0024", "before": {}, "after": {}}


def test_gramps_get_person_calls_client():
    client = MagicMock()
    client.get_person.return_value = {"gramps_id": "I0024"}
    _, tools = create_server(client)

    result = tools["gramps_get_person"]("I0024")

    client.get_person.assert_called_once_with("I0024")
    assert result == {"gramps_id": "I0024"}


def test_gramps_set_surname_calls_client():
    client = MagicMock()
    client.set_surname.return_value = {"gramps_id": "I0036", "before": {}, "after": {}}
    _, tools = create_server(client)

    tools["gramps_set_surname"]("I0036", "Jones", "Married Name")

    client.set_surname.assert_called_once_with("I0036", "Jones", "Married Name")


def test_gramps_add_birth_name_calls_client():
    client = MagicMock()
    client.add_birth_name.return_value = {"gramps_id": "I0061", "before": {}, "after": {}}
    _, tools = create_server(client)

    tools["gramps_add_birth_name"]("I0061", "Smith", "Mary")

    client.add_birth_name.assert_called_once_with("I0061", "Smith", "Mary")


def test_gramps_search_person_calls_client():
    client = MagicMock()
    client.search_person.return_value = [
        {"gramps_id": "I0024", "first_name": "John", "surname": "Smith", "gender": 1}
    ]
    _, tools = create_server(client)

    result = tools["gramps_search_person"]("john")

    client.search_person.assert_called_once_with("john")
    assert result == [{"gramps_id": "I0024", "first_name": "John", "surname": "Smith", "gender": 1}]


def test_gramps_add_person_calls_client():
    client = MagicMock()
    client.add_person.return_value = "I0163"
    _, tools = create_server(client)

    result = tools["gramps_add_person"]("John", "Smith", 1, 1795, "estimated", None, "note text")

    client.add_person.assert_called_once_with("John", "Smith", 1, 1795, "estimated", None, "note text")
    assert result == "I0163"


def test_gramps_add_family_calls_client():
    client = MagicMock()
    client.add_family.return_value = "F0002"
    _, tools = create_server(client)

    result = tools["gramps_add_family"]("I0024", "I0050")

    client.add_family.assert_called_once_with("I0024", "I0050")
    assert result == "F0002"


def test_gramps_add_child_to_family_calls_client():
    client = MagicMock()
    client.add_child_to_family.return_value = {"family_id": "F0002", "child_id": "I0163"}
    _, tools = create_server(client)

    result = tools["gramps_add_child_to_family"]("F0002", "I0163")

    client.add_child_to_family.assert_called_once_with("F0002", "I0163")
    assert result == {"family_id": "F0002", "child_id": "I0163"}


def test_gramps_confirm_person_calls_client():
    client = MagicMock()
    client.confirm_person.return_value = {"gramps_id": "I0163", "before": {}, "after": {}}
    _, tools = create_server(client)

    result = tools["gramps_confirm_person"]("I0163")

    client.confirm_person.assert_called_once_with("I0163")
    assert result == {"gramps_id": "I0163", "before": {}, "after": {}}


def test_gramps_get_descendants_calls_client():
    client = MagicMock()
    client.get_descendants.return_value = {
        "gramps_id": "I0024", "first_name": "John", "surname": "Smith",
        "gender": 1, "children": [],
    }
    _, tools = create_server(client)

    result = tools["gramps_get_descendants"]("I0024", 2)

    client.get_descendants.assert_called_once_with("I0024", 2)
    assert result == {
        "gramps_id": "I0024", "first_name": "John", "surname": "Smith",
        "gender": 1, "children": [],
    }


def test_gramps_get_descendants_defaults_to_grade1():
    # regression: omitting grade delegates with the default grade=1
    client = MagicMock()
    client.get_descendants.return_value = {"gramps_id": "I0024", "children": []}
    _, tools = create_server(client)

    tools["gramps_get_descendants"]("I0024")

    client.get_descendants.assert_called_once_with("I0024", 1)
