# End-to-end suite

The unit suite (`pytest -q`, 259 tests) is **100 % mocked** — 303 `@patch`, zero network
calls. Everything between the client and a running system is uncovered: container, env
wiring, MCP protocol, tool schemas, real Gramps Web. Both of this project's production
incidents happened in exactly that gap (a `COPY`-list that missed a module, twice; the
cold-client 429 on `gramps_delete_all_objects`).

Reference document: `docs/superpowers/plans/2026-07-30-e2e-test-suite.md`. Decision ids
(`D1`…`D26`) and finding ids (`F1`…`F5`) below point into it.

## Layout

| path | what it is |
| --- | --- |
| `harness/docker_util.py` | subprocess/Docker plumbing and the INT tripwires |
| `harness/gramps_instance.py` | disposable Gramps Web + Redis on its own network (D6, D14) |
| `harness/rest.py` | the REST oracle — the only way the suite observes tree state (D3, D22) |
| `harness/mcp_session.py` | raw JSON-RPC framing over a process it is handed (D21) |
| `harness/mcp_container.py` | which image, wired how, reused across calls, gone afterwards (D8) |
| `harness/runid.py` | run identity, Docker labels, artifact dirs |
| `harness/reaper.py` | reaps leftovers without killing a live run (D18) |
| `harness/token_audit.py` | counts token POSTs per client IP from the access log (D15) |
| `probes/probe_bringup.py` | the one bring-up probe (D10) |
| `probes/observed.json` | its committed answers — **the** platform reference |
| `stubs/fake_mcp_server.py` | a rude stdio server, so the framing is testable without Docker |
| `conftest.py` | stamps the `e2e` marker (D1); resolves the image; fails the run on a leak |
| `test_00_*.py`, `test_02_token_audit.py` | Docker-free: gate, reaper guards, teardown bookkeeping, framing, image legs, log parser |
| `test_01_instance.py` | T1.3 acceptance: two bring-ups, nothing left behind, INT untouched |
| `test_03_rest_oracle.py` | T1.4: snapshot/fingerprint, and `put_merge` preserving what it does not touch |
| `test_04_mcp_container.py` | T1.5 acceptance: handshake, `off 27 / on 31`, reuse, verified removal |

Phase 0 created the probe and the harness skeleton; T1.1 added the gate, T1.2 the reaper, T1.3
the restartable instance, T1.4 the oracle plus the token audit and T1.5 the container. The
canonical fixture, the reset and `assertions.py` are the rest of Phase 1.

### Which image is under test (D8)

Two legs, because they answer different questions. **Working tree** (the default, so a local or
PR run tests the branch): builds `gramps-remote-mcp:e2e-<runid>` from the repo root — the only
thing that catches a new root module missing from the Dockerfile's explicit `COPY` list, which
has shipped broken twice. **Released**: runs the shipped artifact, which is what the release
ritual and the nightly want.

```bash
pytest -q -m e2e                                     # working tree, built once per session
GRAMPS_E2E_IMAGE_LEG=released pytest -q -m e2e       # the artifact: gramps-remote-mcp:latest
GRAMPS_E2E_IMAGE_LEG=released GRAMPS_E2E_IMAGE=gramps-remote-mcp:v0.5.1 pytest -q -m e2e
```

A misspelt leg stops the run instead of falling back — a silent fallback to `:latest` is the
stale-image failure mode, and a stale `:latest` has already once looked exactly like "the new
tools are missing". Containers are then started **by image id**, not by tag: a concurrent run's
reaper sweeps `gramps-remote-mcp:e2e-*` by name and the release ritual re-points `:latest` by
hand, so a tag can move out from under a run in flight. The resolved id is what gets recorded.

## Running it

```bash
pytest -q                       # unit suite only: 259 passed, e2e deselected, no Docker
pytest -q -m e2e                # all 55 e2e tests, ~46 s (nine of them need Docker)
pytest -q -m e2e tests/e2e/test_00_*.py tests/e2e/test_02_token_audit.py  # no Docker, ~2 s
pytest --e2e-reap-only          # reap stale gwe2e-* resources, prune artifact dirs, exit
pytest --e2e-reap-only --e2e-force --e2e-keep-runs=1   # also remove a *running* instance

.venv/bin/python tests/e2e/probes/probe_bringup.py --json              # refresh observed.json
.venv/bin/python tests/e2e/probes/probe_bringup.py --json --verbose    # echo every record
.venv/bin/python tests/e2e/probes/probe_bringup.py --keep --verbose    # leave the instance up
```

The gate has two independent halves, because either alone is one edit away from being
useless: `addopts = "-m 'not e2e'"` in `pyproject.toml` deselects, and `conftest.py` stamps the
marker on every item under this directory so a test that forgets `@pytest.mark.e2e` is still
gated. `test_00_gate.py` pins both — it carries no marker itself, so if the stamping breaks it
starts showing up in the default run's count.

The plan writes D18's flag as `--force`; it ships as `--e2e-force` so every flag of this suite
shares one namespace in pytest's global option space. Note also that containers run with
`--rm`, so an exited one removes itself — what the reaper actually finds after a killed run is
*running* containers and orphaned networks, not exited ones.

**Cleanup is verified, not assumed.** A full run once finished green while leaving a redis
container, its network and both volumes behind: every removal runs with `check=False` (a
teardown must not raise over a resource that is already gone), so nothing noticed. `teardown()`
now re-checks each resource, retries once — docker refuses to drop a network or volume while a
container still holds it — and records what survived in `instance.leftovers`. A session fixture
in `conftest.py` reaps anything still registered and **fails the run**, so a leak can never be
silent again. `test_00_teardown.py` pins that bookkeeping without Docker, because a guard that
cannot fire is worth nothing.

Needs: Docker, the `.venv` (`requests`), the images `ghcr.io/gramps-project/grampsweb:latest`,
`redis:alpine` and `gramps-remote-mcp:latest`, plus the synthetic fixture
(`--fixture`, default `.claude/worknotes/e2e-suite/e2e-fixture.gramps`). One run is ~2 min
and ends with `status: complete`; exit code is 0 only if the measurements succeeded **and**
INT was untouched.

The unit suite is unaffected — none of these files is a `test_*.py`, so `pytest -q` does not
collect them. Verified after every change: still 259 passed, no Docker.

## `observed.json` — and why it is authoritative

D10: there is exactly one probe, one run, one answer file. **No test may ship with an
either/or assertion about platform behaviour once this file exists.** If a test needs to
know a shape, it reads it here or the probe grows a measurement.

Keys are grouped by what they settle: `u1`…`u6` are the open `[U]` items of the plan's §6,
`d6`/`d11`/`d14`/`d15`/`d20` are the control mechanisms, `s5_*` re-measures the plan's
reference table, `meta` is provenance (image ids, `mcp` version, commit) and `safety` is the
INT evidence. Re-run the probe after a Gramps Web or `mcp` bump and diff the file — a
changed shape is a real finding, not noise.

## Reading a failure

* The probe prints `== <step>` per phase, so the last header is where it died.
* On failure it still writes `observed.json` with `"status": "failed"` and `"error"`, plus
  whatever was measured before the failure. Look there first, not at the console.
* Containers are torn down even on failure. To inspect, re-run with `--keep` and then:

  ```bash
  docker logs gwe2e-<runid>-web | grep ACCESS       # request log (exists only via the D6 override)
  docker exec gwe2e-<runid>-redis redis-cli --scan --pattern 'LIMIT*'
  ```

* `McpSession` keeps the container's stderr; a `ProbeError` from a tool call quotes its tail.
* A `429` from `/api/token/` in *harness* code means the harness spent the window — the
  limiter is per-IP with a ~1 s TTL. `GrampsRest` enforces a 1.1 s minimum login gap (D22)
  precisely so this never competes with the code under test.

## INT safety

The INT instance is `gramps-grampsweb-1` on port 5055, with `grampsweb_celery` and
`grampsweb_redis`. It holds the real family tree; nothing here may touch it.

* Every container the suite creates is named `gwe2e-<runid>-*`.
* `assert_ours()` is the single choke point — stopping, removing or `exec`-ing anything
  without that prefix raises instead of running. The INT names are additionally blocklisted.
* Ports come from the kernel, never hardcoded, and 5055 is refused explicitly.
* The probe records all three INT containers' id, start time and status **before and after**
  the run and asserts they are identical. A mismatch is exit 1 even if everything else passed.
* The MCP container must never run with `--network host`: it would share the limiter bucket
  with the harness. (The INT config in the monorepo *does* use it — that config is not for
  this suite.)
* Reaping stale containers from *earlier* runs is Phase 1 (D18). The probe cleans up only
  what it created, so a killed run can leave `gwe2e-*` containers behind; remove them by name.

Traffic is plain HTTP to `127.0.0.1` and to container DNS names on a private bridge network.
The disposable instance serves no TLS and holds only synthetic data — the real tree is never
part of an E2E run, and the fixture contains invented people because this repo is public.

## Scope limits (plan §3, verbatim)

1. **Stage 1 proves the count guards do not fire on healthy operations. It cannot prove they
   fire on unhealthy ones.** Eight of the nine guards in the repo trigger on a server-side
   race that no deterministic test can create; only `delete_all_objects`' `expected_count` is
   reachable, because it is a *caller argument*. The only mechanism that can certify guard
   coverage is mutation testing — a manual `make e2e-mutation` target with one mutant per
   guard, not a CI job.
2. **The `TokenRateLimitError` *error* path stays unit-only** (17 unit tests cover it).
   Harness and MCP container sit in different limiter buckets by design — the key includes
   the source IP — so nothing in the harness can spend the code-under-test's token budget.
3. **Stage 2 grades tool *selection* but cannot separate it from *lookup* failure** without
   help: every write UC first resolves a name to a `gramps_id`. A mandatory class-B pre-gate
   makes a run with no successful lookup call `INCONCLUSIVE-LOOKUP`, not `FAILED`.
4. **Stage 2 measures retrieval *and* selection.** MCP tools are deferred in Claude Code:
   the model calls `ToolSearch` with a self-authored query before it can call anything. A
   failure may mean "the docstring was never surfaced" — still a docstring defect, but a
   different one.
5. **The async (Celery) profile is out of scope for this wave.** The disposable instance runs
   the **sync** profile, where every task runs inline; the importer answers **201** and the
   wipe **200**, never 202. That is *extra* coverage — the sync path is exactly what
   `DELETE_ALL_HTTP_TIMEOUT`, `IMPORT_HTTP_TIMEOUT` and the `ReadTimeout` fallthrough exist
   for, and the INT instance (with Celery) never exercises it.
