"""The REST oracle against a live instance (plan D3, T1.4). Needs Docker.

Three properties, one bring-up. The third is the one with teeth: `put_merge` must preserve
what it does not touch, because a partial PUT answers 200 and blanks every omitted field —
measured, not feared (`u5_put_semantics`: `tag_list` 1 → 0, surname gone).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from harness.docker_util import NAME_PREFIX
from harness.gramps_instance import OWNER_PW, OWNER_USER, ROLE_OWNER, GrampsInstance
from harness.mcp_session import DEFAULT_IMAGE, McpSession
from harness.rest import GrampsRest
from harness.runid import new_runid


@pytest.fixture(scope="module")
def instance() -> Iterator[GrampsInstance]:
    started = GrampsInstance(new_runid())
    started.start()
    assert started.add_user(OWNER_USER, OWNER_PW, ROLE_OWNER)["rc"] == 0
    try:
        yield started
    finally:
        started.teardown()


@pytest.fixture(scope="module")
def rest(instance: GrampsInstance) -> GrampsRest:
    return GrampsRest(instance.url, OWNER_USER, OWNER_PW)


def add_person(instance: GrampsInstance, label: str, first_name: str) -> str:
    """Seed through the shipped container rather than by hand-built REST bodies."""
    session = McpSession(
        f"{NAME_PREFIX}{instance.runid}-mcp-{label}",
        DEFAULT_IMAGE,
        {
            "GRAMPS_BASE_URL": instance.internal_url,
            "GRAMPS_USERNAME": OWNER_USER,
            "GRAMPS_PASSWORD": OWNER_PW,
        },
        network=instance.network,
        runid=instance.runid,
    )
    session.initialize()
    result = session.call(
        "gramps_add_person",
        {"first_name": first_name, "surname": "Oracleprobe", "gender": 1, "birth_year": 1900},
        timeout=180,
    )
    session.close()
    assert not result.is_error, result.text
    return result.text.strip()


def test_fingerprint_is_stable_across_reads(instance: GrampsInstance, rest: GrampsRest) -> None:
    add_person(instance, "seed", "Stable")
    assert rest.fingerprint() == rest.fingerprint()


def test_fingerprint_moves_when_an_object_appears(
    instance: GrampsInstance, rest: GrampsRest
) -> None:
    before = rest.snapshot()
    add_person(instance, "second", "Appears")
    after = rest.snapshot()

    assert after["counts"]["people"] == before["counts"]["people"] + 1
    assert rest.fingerprint(after) != rest.fingerprint(before)


def test_put_merge_preserves_the_fields_it_does_not_touch(
    instance: GrampsInstance, rest: GrampsRest
) -> None:
    gramps_id = add_person(instance, "merge", "Preserved")
    before = rest.person(gramps_id)
    # add_person with a birth year gives us both a tag and an event reference — the two lists a
    # partial PUT was measured to wipe.
    assert before["tag_list"] and before["event_ref_list"]

    reply = rest.put_merge("people", before["handle"], {"gender": 0})
    assert reply.status_code < 300, reply.text

    after = rest.person(gramps_id)
    assert after["gender"] == 0
    assert after["tag_list"] == before["tag_list"]
    assert after["event_ref_list"] == before["event_ref_list"]
    assert after["primary_name"]["surname_list"] == before["primary_name"]["surname_list"]
