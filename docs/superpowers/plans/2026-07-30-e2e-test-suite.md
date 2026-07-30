# Plan — end-to-end test suite for gramps-remote-mcp

Written 2026-07-30. Supersedes the ad-hoc E2E scripts used for the `v0.5.0` and `v0.5.1`
releases. Input material: six design sections and three adversarial critiques produced by a
10-agent fan-out (local, `.claude/worknotes/e2e-suite/design-2026-07-30/`), plus a bootstrap
probe run against a disposable Gramps Web instance the same day
(`.claude/worknotes/e2e-suite/bootstrap-probe-report.md`). **Every constant in §5 is measured,
not assumed.**

The design material is deliberately *not* committed: it contains six mutually incompatible
fixtures and two directory layouts, which this plan resolves. The plan is the tracked artifact;
the code it produces replaces the material.

---

## 1. What the suite is for

The repo has **259 unit tests that are 100 % mocked** (303 `@patch`, zero network calls). The
entire layer between the client and a running system — container, env wiring, MCP protocol, tool
schemas, real Gramps Web — has no automated coverage. That is where both of this project's
production incidents happened:

| incident | how it shipped | what would have caught it |
| --- | --- | --- |
| Image builds, crashes on import (Dockerfile's explicit `COPY` list missed a module) — **twice** | unit tests green | in-image `import` of all four modules |
| `gramps_delete_all_objects` dies with 429 on a cold client (`v0.5.0`) | unit tests green | a wipe as the first call of a fresh session against a rate-limit-sharp instance |

**Two stages, different questions.**

- **Stage 1** — a scripted JSON-RPC client drives the real stdio container against a real
  disposable Gramps Web instance. Zero tokens, CI-capable, deterministic. Answers *does the
  shipped artifact work*.
- **Stage 2** — a real LLM (`claude -p` headless) sees only the tool docstrings and chooses for
  itself. Answers *is "docstrings are the contract" true*. This is the only thing Stage 1
  structurally cannot test, and per the user's decision of 2026-07-30 it covers **all 31 tools**.

---

## 2. Settled decisions

Thirteen items were flagged as blocking by the completeness critic. All are settled here.
`[V]` = verified by measurement or by reading the repo today.

| # | decision | why |
| --- | --- | --- |
| **D1** | **Layout: nested `tests/e2e/`.** Gate = a `pytest` marker: `addopts = "-m 'not e2e'"`, marker auto-stamped by `tests/e2e/conftest.py` so a forgotten marker cannot leak Docker into `pytest -q`. | `.dockerignore:1` is `tests/`, which already excludes the tree recursively `[V]`. PR #13 cut the build context to **174 B**; a sibling `tests_e2e/` would need a new ignore line to keep that, i.e. the same edit the nested layout gets for free. |
| **D2** | **One canonical fixture**, `tests/e2e/fixtures/synthetic-tree.gramps`, plain (ungzipped) XML, with **>20 people, >20 families, >20 sources**. One `cast.py` holds every id; **no test body may contain an id literal**. `test_00_fixture.py` asserts the read-back shape. | Six incompatible fixtures existed. The >20 sizing is not cosmetic: `count_people`/`count_families`/`count_sources` are `len(unpaginated list)` and the server's default pagesize is 20 (`server.py:204` `[V]`), so under 20 the pagination assumption is untestable — the same vacuity class decision (g) exists to kill. Plain XML because a gzipped export embeds a **UUID and an mtime** `[V probe §5]`, so its bytes are not reproducible and a committed `.gramps` would not be diff-stable. |
| **D3** | **One REST oracle**: `tests/e2e/harness/rest.py` — `GrampsRest` (`token/person/people/families/sources/notes/tags/object_counts/put_merge`) plus `snapshot()`/`fingerprint()`. **Stage 2's grader imports the same module.** | Four incompatible oracle vocabularies existed; every assertion in every matrix is written in one of them. |
| **D4** | **Stage-2 §0–§3 recovered** — the coverage map and UC1–UC27 with prompts and assertions are in `design-stage2-all31-FULL.md`. The agent split its report across two messages and the workflow captured only the last. Coverage must be extended from **27 primary slots to 31**. | The critic correctly refused to plan Stage-2 tasks without them. |
| **D5** | **Ordered chains become classes.** `@pytest.mark.usefixtures("seeded_tree")` on a class, `seeded_tree` **class-scoped**, cases as ordered methods sharing `self.state`, skip-on-earlier-failure. | An autouse *function*-scoped reset and 44-case chains that accumulate captured ids are mutually exclusive. Class scope makes `-k TestBlogChain` reproduce a whole chain and turns a mid-chain failure into 1 red + N skipped. |
| **D6** | **Keep 8 gunicorn workers; override the CMD once, in `start_instance()`, only to add an access log.** Shell form, so the image's env defaults keep working: `sh -c 'gunicorn -w ${GUNICORN_NUM_WORKERS:-8} -b 0.0.0.0:5000 gramps_webapi.wsgi:app --timeout ${GUNICORN_TIMEOUT:-300} --limit-request-line 8190 --access-logfile - --access-logformat "ACCESS %(h)s %(r)s %(s)s"'` | **Measured `[V]`:** the image's default CMD *is* shell form (`-w ${GUNICORN_NUM_WORKERS:-8} … --timeout ${GUNICORN_TIMEOUT:-120}`), so env overrides are honoured — `GUNICORN_NUM_WORKERS=1` demonstrably changed limiter behaviour. And the default CMD has **no `--access-logfile`**: `docker logs \| grep -c 'POST /api/token/'` returned **0**. One worker is rejected because a single worker serialises the server while a sync wipe or import is in flight, turning confirmation polls into false timeouts. |
| **D7** | **Dynamic container IPs, read with `docker inspect` into `run.json`.** No static `--ip`, no `--subnet`. | `--ip` requires a user-configured subnet; the bring-up creates a plain bridge network (the probe network took an auto-assigned `172.25.0.0/16` `[V]`), so every static-IP assertion would fail to start its container. IPs are needed only for **log attribution**, never for connectivity. |
| **D8** | **Two image legs.** PR/local: build `gramps-remote-mcp:e2e-<runid>` from the working tree. Release ritual + nightly: the **released tag pinned by digest**. The 429 regression runs on both. | They answer different questions (does the tree work / does the artifact work). A 429 test against `:v0.5.1` does not exercise an unreleased branch. |
| **D9** | **Stage 2 bind-mounts the working tree over the image:** `-v $REPO/server.py:/app/server.py:ro` (and the other three modules). A `--rebuild` flag switches to the real image for the release run. | The docstring under test lives in `server.py` and the image holds a *copy* (`Dockerfile:8`). The Stage-2 workflow is *edit docstring → rerun*; a rebuild per iteration is pure latency. The Stage-2 config writer inherits the INT-safety tripwires from `tests/e2e/conftest.py` — it does not re-implement them. |
| **D10** | **One consolidated bring-up probe**, `tests/e2e/probes/probe_bringup.py --json`, output committed as `observed.json`. It owns every `[U]`; **no test may ship with an either/or assertion after it has run.** §5 already answers 14 of them. | ~46 `[U]`s were spread across five lists with heavy overlap and three separate probe scripts. |
| **D11** | **`confirm` coercion settled `[V]`:** pydantic 2.13.4 lax mode maps `1`, `"1"`, `"true"`, `"yes"`, `"on"` → `True`. So over the wire `confirm="yes"` **arms a destructive tool**, even though the unit tests pin `confirm is not True` as "literal True only". One matrix expected a schema error here and was wrong. → §7 finding F1. | The two matrices expected opposite results; this is one command, not a debate. |
| **D12** | **Owner creation: the simple form**, `docker exec <c> python3 -m gramps_webapi user add owner '<pw>' --role 4`. **Plus an EDITOR (role 3) profile in scope** for this wave: one test that a representative write and `export_tree` succeed as EDITOR, and that `import_file`/`delete_all_objects` fail 403-shaped. | Measured `[V]`: the simple form returns rc=0 on a fresh single-tree instance with no `SECRET_KEY` export and no `--tree`; it prints a harmless `Gtk-CRITICAL` on stderr that must not be read as failure. EDITOR is the **documented recommended** deployment (README:124-125), so shipping a tool that silently needs OWNER is a live risk. |
| **D13** | **Plan is tracked** at this path; findings go to §7 and are promoted to GitHub issues individually. The agent design material and `PROGRESS.md` stay local (`.gitignore:29`). | A gitignored plan cannot be the reference for a tracked test suite. |
| **D14** | **Limiter sharpness: a 3-request burst asserting `429 in statuses`**, not two requests asserting `200,429`. The control names its writer: `redis-cli --scan --pattern 'LIMIT*'` must be non-empty, not `DBSIZE > 0`. | flask-limiter's `fixed-window` is calendar-aligned, so two sequential POSTs separated by *g* ms straddle the boundary with probability ≈ *g*/1000 — a 5–30 % session-fatal flake on a healthy instance. Measured `[V]`: 10 POSTs in 152 ms yield exactly **1** success, so any burst ≥3 is deterministic. The observed key is `LIMITS:LIMITER/<ip>/api.token/1/1/second`. |
| **D15** | **The token oracle needs a positive control, session-fatal.** After bring-up the harness issues one `POST /api/token/` and requires `TokenAudit` to find *that* request, attributed to the calling IP, with status 200. | Without it every `tok(ip) == 0`-shaped assertion is `0 == 0` — vacuously green with the fix reverted, with the guard deleted, with anything. This is the `memory://` failure class transplanted into the log parser. |
| **D16** | **`assert_guarded_write` must re-read over REST.** Drop `before != after` entirely; require an explicit `expect_person`/`expect_counts` map asserted against `rest.*` after the call. | `_guarded_write` snapshots the **local dict** after `mutate_fn` (`gramps_client.py:699-704`), so `before != after` holds *whether or not the PUT was sent*. Deleting `self._put_person(...)` turns seven write tools into silent no-ops that the helper certifies as successful — and the same helper false-reds on a legitimate no-op (`confirm_person` on an already-confirmed person). |
| **D17** | **Reset clears the backup directory** as step 0: remove the host dir, recreate 0777, copy the exact expected input set from `fixtures/`, assert against a committed manifest. Every test taking two exports passes an explicit `filename=`. | The backup dir is a bind mount that survives runs, and three assertion sets read it — a leftover export makes "the model exported a file" pass even when it never ran. `resolve_export_path` defaults to a **1-second-resolution** timestamp (`backup_store.py:19`), so two default exports in one second silently overwrite. |
| **D18** | **`reap_stale` never kills a live run:** skip any labelled container that is `Running` **and** started within 60 min, unless `--e2e-reap-only --force`. Print what it skipped. It also reaps stale `:e2e-<runid>` **images**, and `--e2e-keep-runs=N` (default 3) prunes artifact dirs. | Two terminals, or a `--e2e-keep` instance the design explicitly advertises, plus a new run: the reaper as specified destroys the first run mid-suite. |
| **D19** | **`image-contract` keeps the in-image `import` as the required assertion; the Dockerfile `COPY`-list *text* audit is dropped** (or replaced by an AST-derived import graph). | The text audit is a false-red generator: any root-level `*.py` that must not ship, or a second `COPY … ./` line, breaks it. The in-image `python -c 'import backup_store, gramps_client, gramps_blog, server'` catches the real defect with zero false-positive surface. |
| **D20** | **Unregistered-tool shape settled `[V]`:** a `tools/call` for a gated-off tool returns a **successful result with `isError: true`** and text `Unknown tool: <name>` — *not* a JSON-RPC `error` object. Server stderr also logs `Tool '<name>' not listed, no validation will be performed`. | One design section asserted a JSON-RPC error, which would have been a false red on the single test that proves structural destructive gating over the wire. |
| **D21** | **One driver**: `harness/mcp_session.py`. `ToolResult` gains `structured: Any \| None` (raw `structuredContent`) and `content: list[dict]`; every `structuredContent` assertion is conditional on `structured is not None`, with the observed `mcp` version in `run.json`. Per-call `timeout=` is required (the wipe needs 960 s). | Four incompatible driver APIs existed; two assertions referenced a field the proposed dataclass did not have. |
| **D22** | **The run token is minted, never refreshed.** `GrampsRest.token()` re-mints on 401 or age > 13 min and enforces `min_login_gap = 1.1 s`. | A refreshed token is not `fresh`, and `POST /api/objects/delete/` is a `FreshProtectedResource` — the harness's own `wipe()` would 401, re-mint, and risk a 429. |
| **D23** | **Upstream-pinned assertions are marked** `@pytest.mark.upstream_pin`: excluded from the floating-digest nightly leg, and mapped to an `upstream` class in the issue filer so they comment on an infra tracker instead of opening a bug against this repo. | ~15 assertions grade *Gramps Web* (NameType serialisation, 404-vs-`200 []`, errno text, unicode normalisation). A Gramps Web release would otherwise auto-file bugs here for code that is fine. |

### D24–D26 — decided by the user, 2026-07-30

| # | decision | consequence to design for |
| --- | --- | --- |
| **D24** | **Stage-2 gate: K=3 of N=5**, with sequential escalation and early stop (abandon as soon as K is reached or unreachable). | ~0.82 expected findings and ~0.13 spurious drafts per pass. Print the raw ratio (`3/5`) in `summary.md` so the human triages, and emit a one-line **watch** row at 2 of 5 instead of a draft. |
| **D25** | **Stage 2 runs mid-session, on demand** — no schedule. | This is the expensive combination together with D26, so the cheap paths are **not optional extras**: (a) `--only ucNN --repeats 5` (15 runs, ~9 min, ~$2.25) is the mandated post-fix confirmation — never a full re-pass, which re-rolls 24 UCs' wobble dice for nothing; (b) a hard `--max-spend-usd` ceiling summed from each run's `total_cost_usd`, which **aborts** the pass rather than warning; (c) `summary.md` prints cumulative spend and token usage per pass, so the window drain is visible rather than inferred; (d) `--tier 1` (8 UCs, ~4.5 min, ~$1.32) stays available as the quick check even though the default is the full pass. |
| **D26** | **All 31 tools per pass**, not denominated over a week. | ~25 UCs per pass, ~$5.85, ~24 min expected. The report header must state that tier-3 tools are **advisory-only**, so "all 31 covered" is not "all 31 gated" — and that 17 of the 25 UCs have predicted `p_fail ≤ 0.1`, i.e. they are a coverage claim rather than a test. Ordering still matters: `delete_all_objects` runs **last** so a half-landed wipe cannot poison a later UC. |

---

## 3. Scope limits — stated, not discovered later

1. **Stage 1 proves the count guards do not fire on healthy operations. It cannot prove they fire
   on unhealthy ones.** Eight of the nine guards in the repo trigger on a server-side race that no
   deterministic test can create; only `delete_all_objects`' `expected_count` is reachable, because
   it is a *caller argument*. The only mechanism that can certify guard coverage is mutation
   testing — a manual `make e2e-mutation` target with one mutant per guard, not a CI job. Today's
   probe already demonstrated the method: `:v0.5.0` (pre-fix) **429s**, `:latest` **succeeds**, on
   the identical cold wipe.
2. **The `TokenRateLimitError` *error* path stays unit-only** (17 unit tests cover it). Harness and
   MCP container sit in different limiter buckets by design — the key includes the source IP `[V]`
   — so nothing in the harness can spend the code-under-test's token budget. A stretch task
   (`--network container:<netholder>`, so two MCP containers share one IP) is listed in Phase 5;
   until it lands, this is a documented gap, not an oversight.
3. **Stage 2 grades tool *selection* but cannot separate it from *lookup* failure** without help:
   every write UC first resolves a name to a `gramps_id`. A mandatory class-B pre-gate makes a run
   with no successful lookup call `INCONCLUSIVE-LOOKUP`, not `FAILED`, with its own dedup key.
4. **Stage 2 measures retrieval *and* selection.** MCP tools are deferred in Claude Code 2.1.220:
   the model calls `ToolSearch` with a self-authored query before it can call anything. A failure
   may mean "the docstring was never surfaced" — still a docstring defect, but a different one.
5. **The async (Celery) profile is out of scope for this wave.** The disposable instance runs the
   **sync** profile, where every task runs inline; measured `[V]`, the importer answers **201** and
   the wipe **200**, never 202. That is *extra* coverage — the sync path is exactly what
   `DELETE_ALL_HTTP_TIMEOUT`, `IMPORT_HTTP_TIMEOUT` and the `ReadTimeout` fallthrough exist for, and
   the INT instance (with Celery) never exercises it. The 202 path stays manually verified.

---

## 4. Phases

Ordered so that the user's stated priority — the real-AI stage — comes as early as its
dependencies allow. Phase 1 is not optional scope: Stage 2 cannot grade anything without the
fixture, the reset and the REST oracle.

### Phase 0 — settle and probe (½ day)

- **T0.1** Run `probe_bringup.py --json` once; commit `tests/e2e/probes/observed.json`. It must
  answer, at minimum, the items in §6. 14 are pre-answered in §5 and go in as measured values.
- **T0.2** Write `tests/e2e/README.md`: how to run, how to read a failure, the INT-safety
  tripwires, and the §3 scope limits verbatim.

### Phase 1 — foundation (2–3 days)

- **T1.1** `pyproject.toml`: `[tool.pytest.ini_options]` with `testpaths`, the `e2e` marker and
  `addopts = "-m 'not e2e'"`. Acceptance: `pytest -q` still reports **259 passed** with no Docker.
- **T1.2** `harness/docker_util.py` + `runid.py` — including D18's reaper guards.
- **T1.3** `harness/gramps_instance.py` — bring-up (D6 CMD override, Redis, own network),
  readiness (`GET /api/openapi.json`, budget 30 s; measured 5.7 s), owner + editor users (D12),
  **D14** limiter burst, **D15** token-oracle positive control, teardown. Acceptance: two
  consecutive bring-ups in one session, INT untouched, no `gwe2e-*` left behind.
- **T1.4** `harness/rest.py` (D3, D22) + `harness/token_audit.py`.
- **T1.5** `harness/mcp_session.py` (D21) + `mcp_container.py`.
- **T1.6** The canonical fixture (D2): `cast.py`, `synthetic-tree.gramps`, `test_00_fixture.py`.
  Acceptance: import → snapshot → the cast reads back exactly; `len(GET /api/families/) ==
  object_counts["families"] > 20`, same for people and sources.
- **T1.7** `harness/assertions.py` (D16, D17) — `assert_guarded_write` with mandatory REST
  re-read, `assert_refusal(contains=…)` as the **single** error-text helper, plus a meta-test that
  greps every `contains=` literal out of the three product modules and fails if absent (so no test
  can assert framework wording).

### Phase 2 — Stage 2, the real-AI stage (3–4 days) ← the user's priority

- **T2.1** Complete the coverage map from 27 primary slots to **31**. Every tool needs one UC where
  it is primary, or an explicit written reason why it cannot be.
- **T2.2** Fix the fixture-dependent literals in UC1–UC27: ids start at **`I0000`**, not `I0001`
  `[V]`, and ids are resolved from the snapshot rather than hardcoded.
- **T2.3** Per-namespace count deltas for every UC. `add_person` **creates** the `Unbestätigt` tag
  when absent, so a naive `people +1, events +1` assertion is a false red — measured `[V]`: tags
  went 0 → 1 on the first `add_person` and 1 → 2 on the first blog post. Seed both tags in the
  fixture. Express the standing no-mass-deletion guard **per namespace**, with `MAXDROP` per UC.
- **T2.4** The runner: config writer (D9 bind-mount), the verified `claude -p` invocation, the
  five forbidden flags asserted as a guard rather than documented as prose, harness self-check
  (`n_skills == 0`, `n_plugins == 0`, one MCP server) and the `^Verstanden` canary.
- **T2.5** Grading: class A only, from `rest.snapshot()` (D3). The model's prose is not a
  parameter of the grading function. Class-B lookup pre-gate (§3.3).
- **T2.6** Gates: N=5 with **K=3** (D24), sequential escalation with early stop, dedup on
  `(use_case, assertion_id)` via `gh issue list` + local `contains` (never `gh search`, whose index
  lags minutes).
- **T2.7** Mechanize the false-pass audit as a **token-level lint** — no Docker, no LLM, so it runs
  in `lint.yml`: fail if any prompt token is unique to one tool's name. One YAML per UC holding
  prompt, assertions, `MAXDROP` and tier, so a docstring edit touches one file.
- **T2.8** Report + draft writer, incl. `total_cost_usd` and cumulative window consumption.
  Acceptance: one full tier-1 pass green, with a deliberately broken docstring producing a draft.

### Phase 3 — the regression trio + required CI (1 day)

Cheap once Phase 1 exists, and it guards the two failure classes that actually bit.

- **T3.1** `test_00_protocol.py` — handshake, `serverInfo.name`, negotiated protocol version,
  27-vs-31, `expected_tools.json` snapshot (full `inputSchema` **and** `outputSchema`), D20's
  unregistered-tool shape, and **one assertion that each `tools/list` description equals
  `inspect.getdoc()` of the working-tree function** — that is what makes a Stage-2 finding
  attributable to `server.py`.
- **T3.2** `test_01_env_wiring.py` — missing `GRAMPS_BASE_URL`, missing credentials, wrong
  password, unroutable host, absent `GRAMPS_BACKUP_DIR`, the destructive-gate fail-open probe, and
  the EDITOR-role profile (D12).
- **T3.3** `test_80_regressions.py` — the cold-client 429 with the `TokenAudit` counter (a
  pass/fail-only test misses a partial revert: reverting just the mint guard leaves the retry in
  place, so the wipe still succeeds with `tok` delta 3 and one 429 — only the **count** catches
  it), the in-image import (D19), the `mcp<2` pin, `IMPORT_MIN_SETTLE`.
- **T3.4** `.github/workflows/e2e.yml` — `image-contract` **required** from day one (~90 s, no
  Gramps Web, no secrets); full Stage 1 on PR but **advisory**, promoted to required after 20
  consecutive infra-green runs; the matrix nightly with artifact upload.

### Phase 4 — the three Stage-1 matrices (4–6 days)

135 cases across people-names (48), blog/backup/destructive (44) and family-graph (43), rewritten
against D2's fixture and D5's class model. Budget: ~7 min warm / ~11 min cold for the PR tier.

- **T4.1** Mechanically rewrite all three matrices' ids and absolute counts against `cast.py`
  (~60 literals) in the same commit as the fixture.
- **T4.2–T4.4** One phase per matrix; `@pytest.mark.refusal` on the ~40 negative/error-text cases,
  nightly only — they carry the highest rot and the least signal per second.
- **T4.5** Fold family-graph's REST-built `graph3` substrate into the fixture XML. It duplicates
  and contradicts the canonical fixture, costs ~700 REST writes and 40–170 s per run, and its own
  justification (keep the read oracle independent of the write tools) is served *better* by
  hand-authored XML than by a substrate the code under test builds.

### Phase 5 — deferred, listed so nobody promotes them silently

Async/Celery profile · the `TokenRateLimitError` provocation via a shared network namespace ·
`make e2e-mutation` with one mutant per guard (manual) · the floating-digest nightly leg ·
Stage-2 tiers 2–3 until tier 1 has run clean twice · fault-injection cases.

---

## 5. Measured constants — the reference table

All from the 2026-07-30 probe (`bootstrap-probe-report.md`). Use these; do not re-guess.

| quantity | measured | note |
| --- | --- | --- |
| bring-up → `GET /api/openapi.json` == 200 | **5.7 s** | no health endpoint exists; port-answers is a valid signal because the tree is created synchronously before the bind |
| `GET /api/token/create_owner/` | **200** before any user, **405** after | usable as a "already bootstrapped" signal; it is itself rate-limited, so poll it at most once |
| `GET /api/metadata/` unauthenticated | 401 | unusable as readiness |
| limiter, Redis storage, 8 workers | 10 POSTs in 152 ms → **1** success | deterministic |
| limiter, no storage URI, 8 workers | 10 POSTs in 171 ms → **2** successes | leaky *and* nondeterministic — worse than "off" for a gate |
| limiter, no storage URI, 1 worker | 10 POSTs in 130 ms → **1** success | `GUNICORN_NUM_WORKERS` is an honoured env hook |
| `Retry-After` on the 429 | **absent** | confirms the assumption behind `TOKEN_RETRY_WAIT` |
| limiter key | `LIMITS:LIMITER/<ip>/api.token/1/1/second`, value counts **all** attempts, TTL ≈ 1 s | per-IP; a live instrument, not an audit trail |
| access log by default | **none** — `docker logs \| grep -c 'POST /api/token/'` = 0 | hence D6 |
| gramps_ids | start at **`I0000`** — `I0000…I0004`, `F0000`, `S0000`, `N0000`, `E0000…E0003` | the salvaged Stage-2 design says `I0001…I0005`; it is wrong |
| export → wipe → import | `gramps_id` **and** `handle` byte-identical | assertions may reference handles |
| `object_counts` key set | citations, events, families, media, notes, people, places, repositories, sources, **tags** | `tags` **is** a counted namespace |
| `add_person` without `birth_year` | creates **no** event | 5 people → 4 events |
| tags | 0 → 1 on the first `add_person` (`Unbestätigt`), 1 → 2 on the first blog post (`Blog`) | both create branches are real |
| `POST /api/importers/gramps/file` | **201**, empty body, 1727 ms | not 200 (as the research digest said) and not 202 |
| a second identical import | **201**, additive: 14 → 28 objects, ids continue `I0005…I0009` | silent, and it looks successful |
| `POST /api/objects/delete/` | **200**, body `null`, 444 ms | sync |
| `note["text"]` | `{"string": …, "tags": []}` | assert `["text"]["string"]` |
| blog source | `title` + `tag_list` holding a tag **handle** | blog posts are identifiable only via the resolved tag handle |
| export format | gzip, 1159 B for 14 objects; inner name is a **UUID** + mtime | committed fixtures must be plain XML |
| seed 5 people (one stdio session) | 4.8 s | includes container start |
| wipe + import + confirm (one session) | **14.6 s** | the reset-cycle number for the cost model |
| host-side REST snapshot | 150–400 ms | one token per invocation |
| pydantic bool coercion | `1`, `"1"`, `"true"`, `"yes"`, `"on"` → `True` (2.13.4) | D11 |
| unregistered tool over the wire | successful result, `isError: true`, `Unknown tool: <name>` | D20 |

---

## 6. Remaining `[U]` items — answered by T0.1 on 2026-07-30

All six were measured by `tests/e2e/probes/probe_bringup.py`; the committed answers are in
`tests/e2e/probes/observed.json` under the keys in the last column. D10 is now in force: no test
may ship with an either/or assertion about any of these.

| # | question | measured answer | key |
| --- | --- | --- | --- |
| 1 | `Name.type` read shape | **bare string** — `"Birth Name"`, `"Married Name"`. Assert `person['primary_name']['type']`; there is no `{"_class": …}` wrapper | `u1_name_type_shape` |
| 2 | JWT lifetime, and the env var that shortens it | **900 s**, and `fresh` is a real `bool`. `GRAMPSWEB_JWT_ACCESS_TOKEN_EXPIRES=45` is **ignored** — the control instance still minted 900 s, because `config.py:117` sets `datetime.timedelta(minutes=15)` and the env layer does not override it. → residual `[U]` below | `u2_jwt` |
| 3 | `structuredContent` presence | **the inverse of the assumption.** The three bare-`str` tools *do* carry it, wrapped as `{"result": …}`, and they are the only three with an `outputSchema`; the `dict`- and `list`-returning tools carry **none**. D21's rule stands, its rationale flips | `u3_structured_content` |
| 4 | `tools/list` pagination | **single page** at 31 tools: no `nextCursor`, the listing has exactly one key. 27 off / 31 on over the wire | `u4_tools_list` |
| 5 | trailing slash, and partial PUT | `PUT /api/people/<handle>` → **200**; with a trailing slash → **405**. A partial body → **200 and it blanks every omitted field** (measured: `tag_list` 1 → 0, `event_ref_list` 1 → 0, surname → `null`). All five PUT sites in the product read-modify-write a full GET'ed object and use no trailing slash, so this is a harness constraint, not a product bug | `u5_put_semantics` |
| 6 | Celery worker command | `["celery","-A","gramps_webapi.celery","worker","--loglevel=INFO","--concurrency=2"]`, read from the running INT worker; the binary is at `/venv/bin/celery` and the app module ships in the image | `u6_celery` |

### Residual `[U]` — measured in T1.3, and the candidate does not work

**How does an E2E test provoke the 401-relogin path?** The candidate was a `GRAMPSWEB_SECRET_KEY`
rotation. It was built (`GrampsInstance.restart_web`, with named volumes so the tree and the user
database survive a container replacement) and measured — `u7_relogin_trigger`:

| step | measured |
| --- | --- |
| restart with a rotated key | 4.7 s, tree intact (18 → 18 objects), credentials intact |
| the old token afterwards | **422**, `{"message": "Signature verification failed"}` — *not* 401 |
| logging in again | 200 |

So the mechanism works but **answers the wrong status**: `_authed_request` re-logs in on **401**
only, so a rotated key produces a hard error instead of the relogin path. The `[U]` therefore
stays open with a narrower question — *what makes this server answer 401 on a previously valid
token?* Remaining candidates, unmeasured: waiting out the 900 s expiry (nightly only), or deleting
and re-creating the user between calls. Until one lands, the 401 path stays unit-only (17 tests
cover it) — a documented gap, not an oversight. The 422 observation is filed as **F6**.

**D12 is settled by the same task** (`u8_editor_profile`): as EDITOR, `add_person` and
`export_tree` succeed, while `import_file` **and** `delete_all_objects` are refused by the server
with **403**. That measurement was wrong twice before it was right, both times in the same
direction — the wipe was rejected by our *own* argument guard (missing, then stale
`expected_count`) and never reached the server, while the record cheerfully claimed "destructive
refused". Every refusal is now attributed to `server-403` or `our-guard`, and only the former
counts. **Any role assertion must name who refused.**

### What the probe corrected about itself

D14's burst needed its own positive control. First run: the token minted for D15 twelve
milliseconds earlier had already spent the window, so all three statuses came back **429** — which
satisfies `429 in statuses` while proving nothing about whether the instance answers at all, so a
dead instance would read as "sharp". The burst now waits out the window and asserts **exactly one**
success; second run: `[200, 429, 429]`, one success, Redis holding
`LIMITS:LIMITER/<ip>/api.token/1/1/second`.

---

## 7. Findings to file separately — not test bugs, product observations

| # | finding | evidence | proposed handling |
| --- | --- | --- | --- |
| **F1** | Over the wire, `confirm="yes"` / `confirm=1` **arms** a destructive tool: pydantic coerces them to `True` before `confirm is not True` ever runs. The unit tests pin "literal `True` only", which is true of the Python function and false of the MCP tool. | D11 `[V]`, pydantic 2.13.4 | Decide deliberately: accept (the JSON schema does say boolean) and document it in the docstrings, or take `confirm: str` and compare literally. Either way, an E2E case must pin the shipped behaviour. |
| **F2** | `import_file` is additive, silent, and has **no** state-unknown wrapper: a retry duplicates the tree (14 → 28, ids continue) while looking successful. An LLM that reads "nothing happened" and retries doubles the genealogy. | §5 `[V]`; previously only inferred (`PROGRESS.md` Offene Entscheidungen #10b) | Own change: a typed error analogous to `DeleteAllStateUnknownError`, with a message that does not claim nothing happened. |
| **F3** | `add_birth_name`'s docstring (1 line) is out-competed by `add_alternate_name`'s (4 lines, and it names the *maiden or married name* concept). Predicted outcome: `add_alternate_name` wins retrieval and the outcome is identical. | Stage-2 §5 prediction | If Stage 2 confirms it, the honest fix is to **delete** `add_birth_name` or document it as a deprecated alias — not to re-word it. |
| **F4** | The two `*_bulk` docstrings contain a superset of their singles' vocabulary (`set_gender_bulk` repeats the whole enum), so `ToolSearch` may return the bulk tool above the single for a single-person task. Every class-A assertion would still pass; only the tool log shows it. | Stage-2 §5 prediction | Confirm in tier 1, then trim the bulk docstrings' vocabulary. |
| **F5** | `main()` performs no connection attempt, so a wrong password or unreachable host yields a *successful* `initialize` and fails on the first `tools/call`. | `server.py:378-386` | Probably intended (a stdio server must not block on startup) — document it, and pin it with T3.2. |
| **F6** | A token that fails **signature** verification answers **422**, not 401. `_authed_request` re-logs in on 401 only, so if the server's `GRAMPSWEB_SECRET_KEY` changes under a live session — a redeploy without a pinned key — every subsequent call raises a bare `HTTPError` instead of re-authenticating, until the MCP session is restarted. | `u7_relogin_trigger` `[V]`: rotated key → `422 {"message": "Signature verification failed"}`, while the same credentials still log in | Low severity (the token only lives inside one session), but the relogin condition is narrower than it looks. Decide deliberately whether `_authed_request` should treat 422-with-that-body as "re-login", or whether the honest fix is a clearer error message. |

---

## 8. Release-ritual integration

`CLAUDE.md`'s ritual gains one step between step 3 (README tool count) and step 4 (tag):

> **3a.** Run `tests/e2e/run.sh --image <the tag being released>`. Stage 1 must be green; Stage 2
> tier 1 must be green or its failures triaged.

Steps 3 (tool count) and 5 (container smoke) become assertions inside `test_00_protocol.py` and
`image-contract` respectively, so the manual one-liners stop drifting. Step 6 (restart the MCP
connection) stays manual — nothing can verify it from inside the repo.
