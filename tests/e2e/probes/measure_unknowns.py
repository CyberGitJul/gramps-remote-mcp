"""The six open `[U]` items from §6 of the plan — one function each.

Every function returns ``{record_key: value}`` so the driver only has to merge. No
function guesses: where a shape could be either/or, both branches are recorded together
with the assertion path a test should use.
"""

from __future__ import annotations

from typing import Any

import requests
from harness.docker_util import inspect_container, jwt_payload
from harness.gramps_instance import EDITOR_PW, EDITOR_USER, OWNER_PW, OWNER_USER, GrampsInstance
from harness.rest import GrampsRest


def _shape(value: Any) -> str:
    if isinstance(value, str):
        return "bare_string"
    if isinstance(value, dict):
        return f"object:{sorted(value)}"
    return type(value).__name__


def name_type_shape(rest: GrampsRest, mcp: Any) -> dict[str, dict]:
    """[U1] Is `Name.type` a bare string or `{"_class": "NameType", ...}`?

    Load-bearing for every `Married Name` / `Birth Name` assertion in the matrices.
    """
    people = rest.get_json("/api/people/")
    ids = sorted(person["gramps_id"] for person in people)
    target = next(person for person in people if person["gramps_id"] == ids[0])

    session = mcp(label="-names")
    alternate = session.call(
        "gramps_add_alternate_name",
        {"gramps_id": target["gramps_id"], "surname": "Marriedname", "name_type": "Married Name"},
    )
    birth = session.call(
        "gramps_add_birth_name", {"gramps_id": target["gramps_id"], "surname": "Birthname"}
    )
    session.close()

    person = rest.get_json(f"/api/people/{target['handle']}")
    primary = person["primary_name"]["type"]
    alternates = [name["type"] for name in person.get("alternate_names", [])]
    return {
        "u1_name_type_shape": {
            "gramps_ids_present": ids,
            "ids_start_at": ids[0],
            "probe_person": target["gramps_id"],
            "primary_name_type_value": primary,
            "primary_name_type_shape": _shape(primary),
            "alternate_name_type_values": alternates,
            "alternate_name_type_shapes": [_shape(value) for value in alternates],
            "assert_on": (
                "person['primary_name']['type']['value']"
                if isinstance(primary, dict)
                else "person['primary_name']['type']"
            ),
            "add_alternate_name_is_error": alternate.is_error,
            "add_birth_name_is_error": birth.is_error,
        }
    }


def structured_content(rest: GrampsRest, mcp: Any) -> dict[str, dict]:
    """[U3] Does `structuredContent` appear for the three bare-`str` tools?

    `gramps_add_person`, `gramps_add_family` and `gramps_create_blog_post` are annotated
    `-> str`; the rest return `dict` or `list`. Any assertion on `structuredContent` has to
    be conditional on whatever this measures.
    """
    spouse = sorted(person["gramps_id"] for person in rest.get_json("/api/people/"))[0]
    session = mcp(label="-struct")
    probes: dict[str, tuple[dict[str, Any], str]] = {
        "gramps_add_person": ({"first_name": "Struct", "surname": "Probe", "gender": 1}, "str"),
        "gramps_add_family": ({"spouse_a_id": spouse}, "str"),
        "gramps_create_blog_post": ({"title": "Struct probe", "body": "probe body"}, "str"),
        "gramps_get_object_counts": ({}, "dict"),
        "gramps_list_people": ({}, "list"),
    }
    per_tool: dict[str, Any] = {}
    for tool, (arguments, annotation) in probes.items():
        called = session.call(tool, arguments, timeout=180)
        per_tool[tool] = {
            "return_annotation": annotation,
            "is_error": called.is_error,
            "has_structured_content": called.structured is not None,
            "structured_type": type(called.structured).__name__
            if called.structured is not None
            else None,
            "structured_keys": sorted(called.structured)
            if isinstance(called.structured, dict)
            else None,
            "content_types": [part.get("type") for part in called.content],
            "result_keys": sorted(called.raw),
        }
    session.close()
    return {
        "u3_structured_content": {
            "per_tool": per_tool,
            "conclusion": "guard every structuredContent assertion with `structured is not None`",
            "counts_after": rest.object_counts(),
        }
    }


def tools_list(mcp: Any) -> dict[str, dict]:
    """[U4] `nextCursor` at 31 tools — plus D20's gated-off tool shape from the same session."""
    off = mcp(destructive=False, label="-off")
    off_listing = off.list_tools()
    off_names = sorted(tool["name"] for tool in off_listing["tools"])
    gated = off.call("gramps_delete_all_objects", {"confirm": True})
    off.close()

    on = mcp(destructive=True, label="-on")
    on_listing = on.list_tools()
    on_names = sorted(tool["name"] for tool in on_listing["tools"])
    on.close()

    return {
        "u4_tools_list": {
            "count_destructive_off": len(off_names),
            "count_destructive_on": len(on_names),
            "destructive_only": sorted(set(on_names) - set(off_names)),
            "next_cursor_present": "nextCursor" in on_listing,
            "listing_keys": sorted(on_listing),
            "conclusion": (
                "single page at 31 tools"
                if "nextCursor" not in on_listing
                else "paginated — follow nextCursor"
            ),
            "tool_entry_keys": sorted(on_listing["tools"][0]),
            "every_tool_has_output_schema": all(
                "outputSchema" in tool for tool in on_listing["tools"]
            ),
            "tools_with_output_schema": sum("outputSchema" in tool for tool in on_listing["tools"]),
        },
        "d20_unregistered_tool": {
            "called": "gramps_delete_all_objects with GRAMPS_ENABLE_DESTRUCTIVE absent",
            "jsonrpc_error": False,
            "is_error": gated.is_error,
            "text": gated.text[:200],
            "conclusion": "successful result with isError=true — never a JSON-RPC error object",
        },
    }


def put_semantics(rest: GrampsRest) -> dict[str, dict]:
    """[U5] Trailing slash, and whether a partial PUT blanks the omitted fields."""
    person = rest.get_json("/api/people/")[0]
    handle = person["handle"]
    full = rest.get_json(f"/api/people/{handle}")

    no_slash = rest.request("PUT", f"/api/people/{handle}", json=full)
    with_slash = rest.request("PUT", f"/api/people/{handle}/", json=full)

    partial_body = {"handle": handle, "gramps_id": full["gramps_id"], "gender": full["gender"]}
    partial = rest.request("PUT", f"/api/people/{handle}", json=partial_body)
    after = rest.get_json(f"/api/people/{handle}")

    def summarise(record: dict[str, Any]) -> dict[str, Any]:
        names = (record.get("primary_name") or {}).get("surname_list") or [{}]
        return {
            "tag_list_len": len(record.get("tag_list", [])),
            "event_ref_list_len": len(record.get("event_ref_list", [])),
            "has_primary_name": bool(record.get("primary_name")),
            "surname": names[0].get("surname"),
        }

    before_summary, after_summary = summarise(full), summarise(after)
    return {
        "u5_put_semantics": {
            "put_without_trailing_slash": no_slash.status_code,
            "put_with_trailing_slash": with_slash.status_code,
            "partial_put_status": partial.status_code,
            "partial_put_body_keys": sorted(partial_body),
            "before": before_summary,
            "after_partial_put": after_summary,
            "partial_put_blanks_omitted_fields": partial.status_code < 300
            and after_summary != before_summary,
            "conclusion": "read-modify-write the full record (put_merge); never send a partial body",
            "error_head": partial.text[:200] if partial.status_code >= 300 else None,
        }
    }


def relogin_trigger(instance: GrampsInstance, rest: GrampsRest) -> dict[str, dict]:
    """The residual `[U]`: how does a test provoke the 401-relogin path?

    `GRAMPSWEB_JWT_ACCESS_TOKEN_EXPIRES` turned out to be ignored, so the 15-minute expiry
    cannot be waited out. The candidate measured here is a secret-key rotation: replace the web
    container with a different `GRAMPSWEB_SECRET_KEY` and every issued token stops verifying,
    while the user database and the tree survive on their volumes — which is the part that makes
    it usable as a test, because the client has to be able to log in again with the same
    credentials.
    """
    counts_before = rest.object_counts()
    stale_token = rest.token()

    restart_s = instance.restart_web(
        GRAMPSWEB_SECRET_KEY=f"rotated-{instance.runid}-not-a-real-secret"
    )
    stale = requests.get(
        f"{instance.url}/api/metadata/",
        headers={"Authorization": f"Bearer {stale_token}"},
        timeout=30,
    )

    after = GrampsRest(instance.url, OWNER_USER, OWNER_PW)
    try:
        relogin_status: int | None = after.request("GET", "/api/metadata/").status_code
        counts_after = after.object_counts()
        relogin_error = None
    except requests.RequestException as exc:
        relogin_status, counts_after, relogin_error = None, {}, f"{type(exc).__name__}: {exc}"

    return {
        "u7_relogin_trigger": {
            "method": "restart the web container with a rotated GRAMPSWEB_SECRET_KEY",
            "restart_seconds": restart_s,
            "stale_token_status": stale.status_code,
            "stale_token_rejected": stale.status_code == 401,
            "stale_token_body_head": stale.text[:160],
            "relogin_status": relogin_status,
            "credentials_survived": relogin_status == 200,
            "relogin_error": relogin_error,
            "stale_token_is_401": stale.status_code == 401,
            "consequence": (
                "the product re-logs in on 401 only; a signature failure answers 422, so a "
                "rotated secret key does NOT exercise the relogin path"
            ),
            "counts_before": sum(counts_before.values()),
            "counts_after": sum(counts_after.values()) if counts_after else None,
            "tree_survived": bool(counts_after)
            and sum(counts_after.values()) == sum(counts_before.values()),
            "usable_as_a_test": (
                stale.status_code == 401 and relogin_status == 200 and bool(counts_after)
            ),
        }
    }


def editor_profile(rest: GrampsRest, mcp: Any) -> dict[str, dict]:
    """D12: EDITOR is the *recommended* deployment role, so a tool that silently needs OWNER
    is a live risk. Everyday writes and an export must work; import and the wipe must not.

    **Who refuses matters.** The first version of this measurement sent the wipe without
    `expected_count`, so the tool's own argument guard rejected it before any HTTP call — and
    the record then claimed "destructive refused", which would have been just as true for an
    OWNER. The call now carries a valid `expected_count`, and each refusal is attributed to the
    server or to our own guard instead of being counted as a pass on its own.

    Runs last in the probe: if the wipe turns out to be permitted, it can no longer distort an
    earlier measurement.
    """
    session = mcp(destructive=True, label="-editor", user=EDITOR_USER, password=EDITOR_PW)
    # Arguments are built lazily, one call before use. Read eagerly, `expected_count` is the
    # total from *before* the add_person above — and a stale count is rejected by our own
    # argument guard before the request is ever sent, which is precisely the false pass this
    # measurement exists to rule out. It fooled this probe twice before the lambda.
    calls: list[tuple[str, Any]] = [
        ("gramps_add_person", lambda: {"first_name": "Editor", "surname": "Probe", "gender": 1}),
        ("gramps_export_tree", lambda: {"filename": "editor-probe.gramps"}),
        ("gramps_import_file", lambda: {"filename": "editor-probe.gramps"}),
        ("gramps_delete_all_objects", lambda: {"confirm": True, "expected_count": rest.total()}),
    ]
    results: dict[str, Any] = {}
    for tool, build_arguments in calls:
        arguments = build_arguments()
        called = session.call(tool, arguments, timeout=300)
        by_server = "403" in called.text or "FORBIDDEN" in called.text.upper()
        results[tool] = {
            "arguments": arguments,
            "is_error": called.is_error,
            "text_head": called.text[:200],
            "refused_by": "server-403" if by_server else ("our-guard" if called.is_error else None),
        }
    session.close()

    allowed = not (
        results["gramps_add_person"]["is_error"] or results["gramps_export_tree"]["is_error"]
    )
    refused_by_server = sorted(
        tool
        for tool in ("gramps_import_file", "gramps_delete_all_objects")
        if results[tool]["refused_by"] == "server-403"
    )
    return {
        "u8_editor_profile": {
            "role": "EDITOR (3)",
            "per_tool": results,
            "everyday_writes_allowed": allowed,
            "destructive_refused_by_server": refused_by_server,
            "matches_d12_expectation": allowed and len(refused_by_server) == 2,
            "note": "a refusal from our own argument guard is no evidence about the role",
        }
    }


def celery(instance: GrampsInstance) -> dict[str, dict]:
    """[U6] The worker command for the deferred async profile.

    Evidence is the *running* INT worker, read with `docker inspect` — a live, working
    command beats a command copied out of documentation. INT is never modified.
    """
    return {
        "u6_celery": {
            "evidence": "read-only docker inspect of the running INT worker",
            "int_worker_cmd": inspect_container("grampsweb_celery", "{{json .Config.Cmd}}"),
            "int_worker_entrypoint": inspect_container(
                "grampsweb_celery", "{{json .Config.Entrypoint}}"
            ),
            "int_worker_image": inspect_container("grampsweb_celery", "{{.Config.Image}}"),
            "celery_app_module": instance.exec_out(
                "python3", "-c", "import gramps_webapi.celery as c; print(c.__file__)"
            ),
            "celery_binary": instance.exec_out("which", "celery") or None,
            "note": "Phase 5 only — this wave runs the sync profile (§3.5)",
        }
    }


def jwt(
    instance: GrampsInstance,
    rest: GrampsRest,
    control: GrampsInstance,
    override_s: int,
) -> dict[str, dict]:
    """[U2] Access-token lifetime, the `fresh` claim, and the env var that shortens it.

    The control instance is started by the driver with the override set, so the answer is
    a measured lifetime rather than a hopeful reading of the config surface.
    """
    payload = jwt_payload(rest.token())
    control_rest = GrampsRest(control.url, OWNER_USER, OWNER_PW)
    try:
        control_payload = jwt_payload(control_rest.token())
        control_lifetime: int | None = int(control_payload["exp"]) - int(control_payload["iat"])
        control_error = None
    except Exception as exc:  # noqa: BLE001 - a rejected override is itself the measurement
        control_lifetime, control_error = None, f"{type(exc).__name__}: {exc}"

    return {
        "u2_jwt": {
            "default_lifetime_s": int(payload["exp"]) - int(payload["iat"]),
            "fresh_claim": payload.get("fresh"),
            "fresh_claim_type": type(payload.get("fresh")).__name__,
            "claims": sorted(payload),
            "env_var": "GRAMPSWEB_JWT_ACCESS_TOKEN_EXPIRES",
            "env_var_override_s": override_s,
            "env_var_effective_lifetime_s": control_lifetime,
            "env_var_honoured": control_lifetime == override_s,
            "env_var_error": control_error,
            "source_hits": instance.exec_out(
                "grep", "-rn", "JWT_ACCESS_TOKEN_EXPIRES", "/app/src"
            ).splitlines()[:8],
        }
    }
