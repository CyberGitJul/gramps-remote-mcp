import copy
import unicodedata

import requests

# Tag applied to tentative records; the exact contract that couples add_person
# (applies it) to confirm_person (removes it). Keep as a single source of truth.
UNCONFIRMED_TAG = "Unbestätigt"


class PersonNotFoundError(Exception):
    pass


class PersonCountMismatchError(Exception):
    pass


class FamilyNotFoundError(Exception):
    pass


class PersonCreateCountMismatchError(Exception):
    pass


class FamilyCreateCountMismatchError(Exception):
    pass


class ChildAlreadyInFamilyError(Exception):
    pass


_DATE_MODIFIERS = {
    "exact": 0,
    "about": 3,
    "before": 1,
    "after": 2,
    "estimated": 0,
    "between": 4,
}


def _build_date(year, quality, year_to=None):
    """Build a Gramps Date dict from a year and a quality keyword.

    `quality` is one of "exact", "about", "before", "after", "estimated",
    "between" (or None, treated as "exact"). "between" requires `year_to`.
    """
    quality = quality or "exact"
    if quality not in _DATE_MODIFIERS:
        raise ValueError(f"Unknown birth_quality: {quality!r}")
    modifier = _DATE_MODIFIERS[quality]
    date_quality = 1 if quality == "estimated" else 0
    if quality == "between":
        if year_to is None:
            raise ValueError("birth_year_to is required when birth_quality='between'")
        dateval = [0, 0, year, False, 0, 0, year_to, False]
    else:
        dateval = [0, 0, year, False]
    return {
        "_class": "Date",
        "calendar": 0,
        "modifier": modifier,
        "quality": date_quality,
        "dateval": dateval,
        "text": "",
        "sortval": 0,
        "newyear": 0,
    }


def _assign_parent_handles(person_a, person_b):
    """Decide father_handle/mother_handle for a new Family.

    Whichever person has gender Female (0) takes mother_handle; the other
    takes father_handle. If genders don't disambiguate (equal, or person_b
    is None and person_a isn't female), falls back to call order:
    person_a -> father_handle, person_b -> mother_handle.
    """
    handle_a = person_a["handle"]
    if person_b is None:
        if person_a["gender"] == 0:
            return None, handle_a
        return handle_a, None
    handle_b = person_b["handle"]
    if person_a["gender"] == 0 and person_b["gender"] != 0:
        return handle_b, handle_a
    if person_b["gender"] == 0 and person_a["gender"] != 0:
        return handle_a, handle_b
    return handle_a, handle_b


class GrampsClient:
    def __init__(self, base_url, username, password):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._access_token = None

    def _login(self):
        resp = requests.post(
            f"{self.base_url}/api/token/",
            json={"username": self.username, "password": self.password},
            timeout=10,
        )
        resp.raise_for_status()
        self._access_token = resp.json()["access_token"]

    def _request(self, method, path, json_body=None):
        if self._access_token is None:
            self._login()
        headers = {"Authorization": f"Bearer {self._access_token}"}
        resp = requests.request(
            method, f"{self.base_url}{path}", json=json_body, headers=headers, timeout=10
        )
        if resp.status_code == 401:
            self._login()
            headers = {"Authorization": f"Bearer {self._access_token}"}
            resp = requests.request(
                method, f"{self.base_url}{path}", json=json_body, headers=headers, timeout=10
            )
        resp.raise_for_status()
        return resp.json() if resp.content else None

    def get_person(self, gramps_id):
        people = self._request("GET", f"/api/people/?gramps_id={gramps_id}")
        if not people:
            raise PersonNotFoundError(gramps_id)
        return people[0]

    def count_people(self):
        people = self._request("GET", "/api/people/?keys=gramps_id")
        return len(people)

    def count_families(self):
        families = self._request("GET", "/api/families/?keys=gramps_id")
        return len(families)

    def get_family(self, family_id):
        families = self._request("GET", f"/api/families/?gramps_id={family_id}")
        if not families:
            raise FamilyNotFoundError(family_id)
        return families[0]

    def _create_object(self, resource, obj_dict):
        trans = self._request("POST", f"/api/{resource}/", json_body=obj_dict)
        for item in trans:
            if item["type"] == "add" and item["_class"] == obj_dict["_class"]:
                return item["new"]
        raise ValueError(f"No 'add' transaction item found for {obj_dict['_class']}")

    def _put_person(self, handle, obj):
        return self._request("PUT", f"/api/people/{handle}", json_body=obj)

    def _snapshot(self, person):
        return copy.deepcopy({
            "gender": person.get("gender"),
            "primary_name": person.get("primary_name"),
            "alternate_names": person.get("alternate_names"),
            "tag_list": person.get("tag_list"),
        })

    def _guarded_write(self, gramps_id, mutate_fn):
        count_before = self.count_people()
        person = self.get_person(gramps_id)
        before = self._snapshot(person)
        mutate_fn(person)
        self._put_person(person["handle"], person)
        count_after = self.count_people()
        if count_after != count_before:
            raise PersonCountMismatchError(
                f"Person count changed: {count_before} -> {count_after}"
            )
        after = self._snapshot(person)
        return {"gramps_id": gramps_id, "before": before, "after": after}

    def set_gender(self, gramps_id, gender):
        def mutate(person):
            person["gender"] = gender

        return self._guarded_write(gramps_id, mutate)

    def set_surname(self, gramps_id, surname, name_type=None):
        def mutate(person):
            person["primary_name"]["surname_list"][0]["surname"] = surname
            if name_type is not None:
                person["primary_name"]["type"] = name_type

        return self._guarded_write(gramps_id, mutate)

    def add_birth_name(self, gramps_id, surname, first_name=None):
        def mutate(person):
            primary_name = person["primary_name"]
            primary_surname = primary_name.get("surname_list", [{}])[0]

            # Build fresh name record with content fields from primary_name
            # and metadata fields set to Gramps Web API defaults
            birth_name = {
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
                "type": "Birth Name",
            }
            person.setdefault("alternate_names", []).append(birth_name)

        return self._guarded_write(gramps_id, mutate)

    def confirm_person(self, gramps_id):
        tag_handle = self._find_tag_handle(UNCONFIRMED_TAG)

        def mutate(person):
            if tag_handle is not None and tag_handle in person.get("tag_list", []):
                person["tag_list"].remove(tag_handle)

        return self._guarded_write(gramps_id, mutate)

    def search_person(self, query):
        query_lower = (query or "").strip().lower()
        if not query_lower:
            return []  # empty/whitespace query must not match the whole tree
        people = self._request("GET", "/api/people/?keys=gramps_id,primary_name,gender")
        matches = []
        for person in people:
            primary_name = person.get("primary_name") or {}
            first_name = primary_name.get("first_name") or ""
            surname_list = primary_name.get("surname_list") or []
            surname = surname_list[0]["surname"] if surname_list else ""
            if query_lower in first_name.lower() or query_lower in surname.lower():
                matches.append(
                    {
                        "gramps_id": person["gramps_id"],
                        "first_name": first_name,
                        "surname": surname,
                        "gender": person.get("gender"),
                    }
                )
        return matches

    def _find_tag_handle(self, name):
        # Compare on NFC-normalized forms so a tag created outside this code
        # (e.g. in the Gramps Web UI with a decomposed umlaut) still matches.
        target = unicodedata.normalize("NFC", name)
        tags = self._request("GET", "/api/tags/?keys=handle,name")
        for tag in tags:
            if unicodedata.normalize("NFC", tag["name"]) == target:
                return tag["handle"]
        return None

    def _get_or_create_tag(self, name):
        handle = self._find_tag_handle(name)
        if handle is not None:
            return handle
        tag_dict = {"_class": "Tag", "name": name, "color": "#000000000000", "priority": 0}
        new_tag = self._create_object("tags", tag_dict)
        return new_tag["handle"]

    def _create_birth_event(self, year, quality, year_to=None):
        event_dict = {
            "_class": "Event",
            "type": {"_class": "EventType", "value": 12, "string": ""},  # Birth
            "date": _build_date(year, quality, year_to),
        }
        new_event = self._create_object("events", event_dict)
        return new_event["handle"]

    def _create_note(self, text):
        note_dict = {
            "_class": "Note",
            "text": {"_class": "StyledText", "tags": [], "string": text},
        }
        new_note = self._create_object("notes", note_dict)
        return new_note["handle"]

    def add_person(
        self,
        first_name,
        surname,
        gender,
        birth_year=None,
        birth_quality=None,
        birth_year_to=None,
        note=None,
    ):
        # refuse to create a nameless person or an empty note
        if not (first_name or "").strip() and not (surname or "").strip():
            raise ValueError("add_person requires a non-empty first_name or surname")

        count_before = self.count_people()

        event_handle = None
        if birth_year is not None:
            event_handle = self._create_birth_event(birth_year, birth_quality, birth_year_to)

        note_handle = None
        if note and note.strip():
            note_handle = self._create_note(note)

        tag_handle = self._get_or_create_tag(UNCONFIRMED_TAG)

        person_dict = {
            "_class": "Person",
            "gender": gender,
            "primary_name": {
                "_class": "Name",
                "first_name": first_name,
                "surname_list": [{"_class": "Surname", "surname": surname, "primary": True}],
            },
            "tag_list": [tag_handle],
            "note_list": [note_handle] if note_handle else [],
            "event_ref_list": (
                [
                    {
                        "_class": "EventRef",
                        "ref": event_handle,
                        "role": {"_class": "EventRoleType", "value": 1, "string": ""},
                    }
                ]
                if event_handle
                else []
            ),
            "birth_ref_index": 0 if event_handle else -1,
        }
        new_person = self._create_object("people", person_dict)

        count_after = self.count_people()
        if count_after != count_before + 1:
            raise PersonCreateCountMismatchError(
                f"Person count changed unexpectedly: {count_before} -> {count_after}"
            )
        return new_person["gramps_id"]

    def add_family(self, spouse_a_id, spouse_b_id=None):
        count_before = self.count_families()

        person_a = self.get_person(spouse_a_id)
        person_b = self.get_person(spouse_b_id) if spouse_b_id is not None else None
        father_handle, mother_handle = _assign_parent_handles(person_a, person_b)

        family_dict = {
            "_class": "Family",
            "father_handle": father_handle,
            "mother_handle": mother_handle,
        }
        new_family = self._create_object("families", family_dict)

        count_after = self.count_families()
        if count_after != count_before + 1:
            raise FamilyCreateCountMismatchError(
                f"Family count changed unexpectedly: {count_before} -> {count_after}"
            )
        return new_family["gramps_id"]

    def _put_family(self, handle, obj):
        return self._request("PUT", f"/api/families/{handle}", json_body=obj)

    def add_child_to_family(self, family_id, child_id):
        family = self.get_family(family_id)
        child = self.get_person(child_id)

        existing_refs = [ref["ref"] for ref in family.get("child_ref_list", [])]
        if child["handle"] in existing_refs:
            raise ChildAlreadyInFamilyError(family_id, child_id)

        child_ref = {
            "_class": "ChildRef",
            "ref": child["handle"],
            "private": False,
            "citation_list": [],
            "note_list": [],
            "frel": {"_class": "ChildRefType", "value": 1, "string": ""},
            "mrel": {"_class": "ChildRefType", "value": 1, "string": ""},
        }
        family.setdefault("child_ref_list", []).append(child_ref)
        self._put_family(family["handle"], family)
        return {"family_id": family_id, "child_id": child_id}

    def _get_person_by_handle(self, handle):
        return self._request("GET", f"/api/people/{handle}")

    def _get_family_by_handle(self, handle):
        return self._request("GET", f"/api/families/{handle}")

    def _descendant_node(self, person, children):
        primary = person.get("primary_name") or {}
        surname_list = primary.get("surname_list") or []
        return {
            "gramps_id": person["gramps_id"],
            "first_name": primary.get("first_name") or "",
            "surname": surname_list[0]["surname"] if surname_list else "",
            "gender": person.get("gender"),
            "children": children,
        }

    def _build_descendant_node(self, person, remaining_depth):
        children = []
        if remaining_depth > 0:
            for fam_handle in person.get("family_list", []):
                family = self._get_family_by_handle(fam_handle)
                for child_ref in family.get("child_ref_list", []):
                    child = self._get_person_by_handle(child_ref["ref"])
                    children.append(
                        self._build_descendant_node(child, remaining_depth - 1)
                    )
        return self._descendant_node(person, children)

    def get_descendants(self, gramps_id, grade=1):
        if grade < 1:
            raise ValueError("grade must be >= 1")
        root = self.get_person(gramps_id)
        return self._build_descendant_node(root, grade)
