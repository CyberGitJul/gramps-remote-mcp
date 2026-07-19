import os

from mcp.server.fastmcp import FastMCP

from gramps_client import GrampsClient


def create_server(client):
    mcp = FastMCP("gramps-remote-mcp")
    tools = {}

    def register(fn):
        tools[fn.__name__] = mcp.tool()(fn)
        return tools[fn.__name__]

    @register
    def gramps_get_person(gramps_id: str) -> dict:
        """Fetch the current live record for a person by Gramps ID (e.g. 'I0024')."""
        return client.get_person(gramps_id)

    @register
    def gramps_set_gender(gramps_id: str, gender: int) -> dict:
        """Set a person's gender. 0=Female, 1=Male, 2=Unknown, 3=Other."""
        return client.set_gender(gramps_id, gender)

    @register
    def gramps_set_surname(gramps_id: str, surname: str, name_type: str | None = None) -> dict:
        """Set a person's primary surname, optionally also the name type (e.g. 'Married Name')."""
        return client.set_surname(gramps_id, surname, name_type)

    @register
    def gramps_set_gender_bulk(items: list[dict]) -> dict:
        """Set gender for many people in one call, under a single count-guard.

        items: [{"gramps_id": "I0031", "gender": 0}, ...]
        gender: 0=Female, 1=Male, 2=Unknown, 3=Other.
        Best-effort (not atomic): a failing item is recorded in `errors` and does
        NOT abort the rest. Returns count_before/count_after/count_guard_ok, a
        `results` list (per-person before/after), and an `errors` list.
        """
        return client.set_gender_bulk(items)

    @register
    def gramps_set_surname_bulk(items: list[dict]) -> dict:
        """Set the primary surname for many people in one call, under a single count-guard.

        items: [{"gramps_id": "I0036", "surname": "Prentl", "name_type": "Married Name"}, ...]
        `name_type` is optional per item. Best-effort (not atomic): a failing item
        is recorded in `errors` and does NOT abort the rest. Returns
        count_before/count_after/count_guard_ok, `results`, and `errors`.
        """
        return client.set_surname_bulk(items)

    @register
    def gramps_add_birth_name(gramps_id: str, surname: str, first_name: str | None = None) -> dict:
        """Add a 'Birth Name' alternate name entry with the given surname."""
        return client.add_birth_name(gramps_id, surname, first_name)

    @register
    def gramps_search_person(query: str, limit: int | None = None) -> list:
        """Search people by name (case-insensitive substring match).

        Matches on first name, surname, the combined 'First Surname', the
        nickname, and any alternate/maiden names. `limit` optionally caps the
        number of results returned.
        """
        return client.search_person(query, limit)

    @register
    def gramps_add_person(
        first_name: str,
        surname: str,
        gender: int,
        birth_year: int | None = None,
        birth_quality: str | None = None,
        birth_year_to: int | None = None,
        note: str | None = None,
    ) -> str:
        """Create a new person, tagged 'Unbestätigt' (unconfirmed). Returns the new Gramps ID.

        gender: 0=Female, 1=Male, 2=Unknown, 3=Other.
        birth_quality: one of 'exact', 'about', 'before', 'after', 'estimated',
        'between' (the last requires birth_year_to). Omit birth_year for no birth event.
        """
        return client.add_person(
            first_name, surname, gender, birth_year, birth_quality, birth_year_to, note
        )

    @register
    def gramps_add_family(spouse_a_id: str, spouse_b_id: str | None = None) -> str:
        """Create a new Family linking one or two spouses. Returns the new family's Gramps ID.

        Parent-slot assignment: whichever spouse has gender Female (0) becomes the
        mother, the other the father. With a single spouse, they are the father
        unless female. If gender does not disambiguate (same gender, or neither is
        female), call order wins: spouse_a -> father, spouse_b -> mother.
        """
        return client.add_family(spouse_a_id, spouse_b_id)

    @register
    def gramps_add_child_to_family(family_id: str, child_id: str) -> dict:
        """Link an existing person as a child of an existing family."""
        return client.add_child_to_family(family_id, child_id)

    @register
    def gramps_confirm_person(gramps_id: str) -> dict:
        """Remove the 'Unbestätigt' tag from a person, marking them as confirmed."""
        return client.confirm_person(gramps_id)

    @register
    def gramps_get_descendants(gramps_id: str, grade: int = 1) -> dict:
        """Return the person and their descendants as a nested tree.

        Descends `grade` generations (grade=1 = only children, 2 = + grandchildren).
        The queried person is the root node; each node carries a `children` list.
        """
        return client.get_descendants(gramps_id, grade)

    @register
    def gramps_get_object_counts() -> dict:
        """Return the tree's object counts (people, families, events, notes, media, ...).

        Read-only overview, handy as a before/after guard around bulk operations.
        """
        return client.object_counts()

    @register
    def gramps_list_people(
        keys: list[str] | None = None,
        page: int | None = None,
        pagesize: int | None = None,
    ) -> list:
        """List people, optionally selecting fields and paginating.

        keys: field names to return (e.g. ['gramps_id','gender','family_list',
        'parent_family_list']); omit for full records. page is 1-based; pagesize
        caps rows per page (server default 20, no maximum). Omit both page and
        pagesize to return every person in one call.
        """
        return client.list_people(keys, page, pagesize)

    @register
    def gramps_get_ancestors(gramps_id: str, grade: int = 1) -> dict:
        """Return the person and their ancestors as a nested tree.

        Ascends `grade` generations (grade=1 = only parents, 2 = + grandparents).
        The queried person is the root node; each node carries a `parents` list.
        Note: father/mother are bloodline slots, not gender; each node reports its
        own `gender` separately, so do not infer sex from a parent's slot.
        """
        return client.get_ancestors(gramps_id, grade)

    @register
    def gramps_get_relations(gramps_id: str) -> dict:
        """Return a person's family context: parent families and own families.

        `parent_families`: families where the person is a child (father/mother
        slot + family_gramps_id). `families`: families where the person is a
        spouse/parent (partner + children). father/mother/partner are bloodline
        slots, NOT gender — every person carries its own `gender`, so never infer
        sex from which slot someone occupies.
        """
        return client.get_relations(gramps_id)

    return mcp, tools


def main():
    client = GrampsClient(
        base_url=os.environ["GRAMPS_BASE_URL"],
        username=os.environ["GRAMPS_USERNAME"],
        password=os.environ["GRAMPS_PASSWORD"],
    )
    mcp, _ = create_server(client)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
