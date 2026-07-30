"""The control mechanisms the suite's non-vacuity rests on, plus the §5 shape re-measures.

Each of these guards a specific way the suite could be green while proving nothing:

* **D6** — with the image's default CMD, counting token POSTs from `docker logs` returns 0.
* **D15** — a `tok(ip) == 0` assertion is `0 == 0` unless the log parser is proven to find
  a request it *should* find. That positive control is session-fatal by design.
* **D14** — a 2-request `200,429` probe straddles flask-limiter's calendar-aligned window
  often enough to be a 5–30 % flake; a 3-burst asserting `429 in statuses` does not.
* **D11/F1** — `confirm="yes"` over the wire: pydantic coerces before `confirm is not True`
  ever runs, so the shipped tool is armed by a string the unit tests say is inert.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests
from harness.gramps_instance import OWNER_PW, OWNER_USER, GrampsInstance
from harness.rest import MIN_LOGIN_GAP_S, GrampsRest


def access_log_and_oracle(instance: GrampsInstance, rest: GrampsRest) -> dict[str, dict]:
    """D6 (the log exists at all) and D15 (the parser finds a known-good request)."""
    access = instance.access_log()
    before = instance.token_post_log()

    rest.token(force=True)
    after = instance.token_post_log()
    new_lines = after[len(before) :]
    ips = sorted({line.split()[1] for line in new_lines}) if new_lines else []

    return {
        "d6_access_log": {
            "cmd_override_applied": True,
            "access_lines": len(access),
            "token_post_lines": len(before),
            "sample": before[-1] if before else None,
            "default_cmd_has_access_log": False,
            "conclusion": "token POSTs are countable only with the D6 CMD override",
        },
        "d15_token_oracle": {
            "single_post_visible": len(new_lines) >= 1,
            "new_lines": new_lines,
            "attributed_ips": ips,
            "status_200_present": any(
                line.endswith(" 200") or " 200" in line for line in new_lines
            ),
            "conclusion": "positive control passed — a tok(ip)==0 assertion is not vacuous",
        },
    }


def limiter_burst(instance: GrampsInstance, burst: int = 3) -> dict[str, dict]:
    """D14: the limiter must be deterministically sharp, and Redis must be its writer.

    The burst waits out the current window first. Measured the hard way on 2026-07-30:
    without the wait, the token minted for D15 twelve milliseconds earlier had already
    spent the window, so all three statuses came back 429. That satisfies
    `429 in statuses` while proving nothing about whether the instance still answers at
    all — a broken instance would look "sharp". Exactly one success is D14's own control.
    """
    time.sleep(MIN_LOGIN_GAP_S)
    statuses: list[int] = []
    started = time.monotonic()
    for _ in range(burst):
        reply = requests.post(
            f"{instance.url}/api/token/",
            json={"username": OWNER_USER, "password": OWNER_PW},
            timeout=30,
        )
        statuses.append(reply.status_code)
    burst_ms = round((time.monotonic() - started) * 1000)
    keys = instance.redis_keys()

    followup = requests.post(
        f"{instance.url}/api/token/",
        json={"username": OWNER_USER, "password": OWNER_PW},
        timeout=30,
    )
    retry_after = followup.headers.get("Retry-After") if followup.status_code == 429 else None
    time.sleep(MIN_LOGIN_GAP_S)

    return {
        "d14_limiter_burst": {
            "burst_size": burst,
            "statuses": statuses,
            "burst_ms": burst_ms,
            "sharp": 429 in statuses,
            "successes": statuses.count(200),
            "one_success_control": statuses.count(200) == 1,
            "redis_limit_keys": keys,
            "redis_control_non_empty": bool(keys),
            "followup_status": followup.status_code,
            "retry_after_header": retry_after,
            "reference_2026_07_30": "10 POSTs in 152 ms -> exactly 1 success",
        }
    }


def cycle(
    rest: GrampsRest,
    mcp: Any,
    fixture_name: str,
    backup_dir: Path,
    runid: str,
) -> dict[str, dict]:
    """§5 shapes, F2's additive import, and D11's coerced `confirm` — in that order.

    Ordering is deliberate: the wipe runs last so a half-landed delete cannot poison an
    earlier measurement.
    """
    notes = rest.get_json("/api/notes/")
    note_text = notes[0]["text"] if notes else None

    session = mcp(destructive=True, label="-cycle")
    export_name = f"probe-{runid}.gramps"
    exported = session.call("gramps_export_tree", {"filename": export_name}, timeout=300)
    export_path = backup_dir / export_name
    magic = export_path.read_bytes()[:2] if export_path.exists() else b""

    before = rest.object_counts()
    started = time.monotonic()
    second = session.call("gramps_import_file", {"filename": fixture_name}, timeout=300)
    second_s = round(time.monotonic() - started, 2)
    after = rest.object_counts()

    total = sum(after.values())
    coerced = session.call(
        "gramps_delete_all_objects", {"confirm": "yes", "expected_count": total}, timeout=960
    )
    wiped = rest.object_counts()
    session.close()

    return {
        "s5_shapes": {
            "note_text_value": note_text,
            "note_text_shape": (
                f"object:{sorted(note_text)}"
                if isinstance(note_text, dict)
                else type(note_text).__name__
            ),
            "assert_on": "note['text']['string']"
            if isinstance(note_text, dict)
            else "note['text']",
            "export_is_error": exported.is_error,
            "export_magic_bytes": magic.hex(),
            "export_is_gzip": magic == b"\x1f\x8b",
            "export_size": export_path.stat().st_size if export_path.exists() else None,
        },
        "s5_import_additivity": {
            "second_import_is_error": second.is_error,
            "second_import_seconds": second_s,
            "total_before": sum(before.values()),
            "total_after": sum(after.values()),
            "people_before": before.get("people"),
            "people_after": after.get("people"),
            "additive": sum(after.values()) > sum(before.values()),
            "finding": "F2 — a silent additive retry duplicates the tree",
        },
        "d11_confirm_coercion": {
            "sent": {"confirm": "yes", "expected_count": total},
            "is_error": coerced.is_error,
            "text_head": coerced.text[:200],
            "total_after": sum(wiped.values()),
            "armed_destructive_tool": sum(wiped.values()) == 0 and not coerced.is_error,
            "finding": "F1 — pydantic coerces 'yes' to True before `confirm is not True` runs",
        },
    }
