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
    def gramps_add_birth_name(gramps_id: str, surname: str, first_name: str | None = None) -> dict:
        """Add a 'Birth Name' alternate name entry with the given surname."""
        return client.add_birth_name(gramps_id, surname, first_name)

    @register
    def gramps_search_person(query: str) -> list:
        """Search people by first or last name (case-insensitive substring match)."""
        return client.search_person(query)

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
