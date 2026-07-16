# csiapps: R → Python porting plan

Port of the CSIO [`csiapps`](https://github.com/CSIOntario/csiapps) R package to
Python, with full feature parity. Target web framework for the app-wrapper
layer: **[Shiny for Python](https://shiny.posit.co/py/)** (chosen because it is a
near-1:1 match for the R Shiny wrapper).

## The package is three layers

| Layer | R source | Nature | Port difficulty |
|---|---|---|---|
| **API client** | `R/utils.R` | HTTP + OAuth2 PKCE + JSON, pure logic | Mechanical |
| **Sandbox** | `R/sandbox.R` | In-memory warehouse emulation, JSON-schema validation, dummy data registry | Mechanical (two dep swaps) |
| **App wrapper** | `R/shiny.R` | Shiny UI/server wrapper, navbar/footer chrome, in-app OAuth redirect | Mechanical *because* target is Shiny for Python |

## Repository decision

Separate repo (`csiapps-py`, publishing package `csiapps` to PyPI) rather than a
monorepo or mixing into the R repo. R packaging artifacts (`DESCRIPTION`,
`NAMESPACE`, `man/`, roxygen, pkgdown) don't coexist cleanly with
`pyproject.toml`/pytest/ruff. The two implementations stay in sync via the
**shared REST API contract + JSON schemas**, not shared code.

## Dependency mapping

Most R deps collapse to Python stdlib.

| R dependency | Used for | Python replacement |
|---|---|---|
| `httr2` | HTTP requests, retries, pagination | `httpx` |
| `httr2` OAuth2 PKCE helpers | auth flow | **stdlib** `secrets` + `hashlib.sha256` + `base64.urlsafe_b64encode` |
| `jsonlite` | JSON parse/serialize | **stdlib** `json` |
| `openssl` | base64, `rand_bytes` | **stdlib** `base64`, `secrets` |
| `jsonvalidate` (Ajv) | sandbox schema validation | `jsonschema` |
| `babynames` | random dummy athlete names | `faker` |
| `shiny` / `shinyjs` | app wrapper | `shiny` (Shiny for Python); `shinyjs` → custom-message JS handler |

Runtime deps: `httpx`, `jsonschema`, `faker`, `shiny`. Everything else is stdlib.

## Function map (mirrors the R `NAMESPACE`)

All exported functions carry over; names stay `snake_case`.

**config.py** — `set_institute`, `is_sandbox_mode`. R uses a `package_state`
environment + `options(csiapps.sandbox=)` + `CSIAPPS_ENV`. Python: module-level
state object + `CSIAPPS_ENV` env var. Do **not** build a Config class — mirror
the R global.

**auth.py** — `check_secrets`, PKCE helpers (`pkce_state_encode/decode`,
base64url), `exchange_code_for_token`. All stdlib, ~30 lines of PKCE.

**client.py** — `make_request` (with `paginate`, `sandbox` dispatch),
`fetch_org_options`, `fetch_profiles` (auto-paginate), `fetch_profile`.
Two-tier token resolution `.current_token()`: per-session token first, then
`CSIAPPS_ACCESS_TOKEN` env var.

**sandbox.py** — `register_sandbox_schema`, `clear_sandbox`, `browse_sandbox`,
`create_sport_org`, `create_profile`, plus the internal request router
(`data-sources/{uuid}`, `ingestion/primary`, `data-records`) and
`sandbox_ingest`. Keep **read-time** subject resolution so athletes registered
after ingestion backfill.

**app.py** — `ui_wrapper`, `server_wrapper`, navbar/footer/CSS chrome, sandbox
banner, `/me` load, logout re-seed. (`global_wrapper` was ported then dropped in
the ponytail audit — a no-op in Python; module scope covers the R use case.)

### Shiny R → Shiny for Python

| R (Shiny) | Python (Shiny for Python) |
|---|---|
| `reactiveVal(NULL)` | `reactive.value(None)` |
| `observe({...})` | `@reactive.effect` |
| `observeEvent(x(), {...})` | `@reactive.effect` + `@reactive.event(x)` |
| `renderUI` / `uiOutput` | `@render.ui` / `ui.output_ui` |
| `tags$div`, `HTML`, `tagList`, `fluidPage` | `ui.tags.div`, `ui.HTML`, `ui.TagList`, `ui.page_fluid` |
| `session$sendCustomMessage(...)` | `session.send_custom_message` |
| `getDefaultReactiveDomain()` | `shiny.session.get_current_session()` |
| `parseQueryString(session$clientData$url_search)` | `session.clientdata.url_search()` |

Two gotchas:

* **`shinyjs` has no Python port.** Only used for
  `runjs("window.location.href = ...")`. Replace with the same custom-message
  handler the redirect already uses — no dependency; `useShinyjs()` drops out.
* **Per-session token** (`session$userData$csiapps_token`): implemented as a
  `WeakKeyDictionary` keyed on the Shiny session (`client.set_session_token`),
  read back by `client.current_token()` via `shiny.session.get_current_session()`,
  falling back to `CSIAPPS_ACCESS_TOKEN`. (A `contextvar`, tried in phase 3, does
  **not** work: Shiny reactive contexts don't propagate it across effects.)

## Verification strategy

The R package's `testthat` suite is the spec. Port each test to `pytest`
alongside the module it covers:

| R test | Python |
|---|---|
| `tests/testthat/test-sandbox.R` | `tests/test_sandbox.py` |
| `tests/testthat/test-sandbox-wrapper.R` | `tests/test_sandbox_wrapper.py` |
| `tests/testthat/test-sandbox-registry.R` | `tests/test_sandbox_registry.py` |
| `tests/testthat/test-sandbox-mode.R` | `tests/test_sandbox_mode.py` |

## Phases

- [x] **Phase 1 — Scaffold.** New repo, `pyproject.toml` (uv/hatchling), CI
  (pytest + ruff), package skeleton, this plan.
- [x] **Phase 2 — config + auth.** `config.py` (set_institute, is_sandbox_mode,
  set_sandbox_mode, URL helpers) + `auth.py` (PKCE stdlib, token exchange,
  check_secrets). Ported the `is_sandbox_mode` portion of `test-sandbox-mode.R`
  plus PKCE round-trip / token-exchange / check_secrets tests (16 passing).
- [x] **Phase 3 — client.** `client.py`: `make_request` (retry + pagination),
  `fetch_org_options`, `fetch_profiles` (auto-paginate), `fetch_profile`, and
  per-session `current_token()` (contextvar → `CSIAPPS_ACCESS_TOKEN`). Sandbox
  branches delegate to `csiapps.sandbox` internals (lazy import) landing in
  phase 4. HTTP paths tested with `respx` (27 passing).

  > **Phase 4 must provide these in `sandbox.py`:** `_make_sandbox_request(...)`,
  > `_sandbox_org_options()`, `_sandbox_profiles(sport_org_id)`,
  > `_sandbox_profile(profile_id)` — the delegation targets `client.py` calls.
  > `flatten_record` (R internal, unexported, uncalled within the package) was
  > **skipped**; add it only if a consuming app actually needs it.
- [x] **Phase 4 — sandbox.** `sandbox.py`: request router, ingest + Draft-7
  `jsonschema` validation, dummy sport-org/athlete registry (`faker` names),
  read-time subject resolution, and the four `_sandbox_*` delegation functions
  `client.py` calls. Ported `test-sandbox.R`, `test-sandbox-registry.R`, the
  `make_request` routing + `fetch_*` sandbox cases (69 passing total). Validator
  wording adapted Ajv→jsonschema ("too short" vs "fewer than 10 characters").
  `flatten_record` and its test remain skipped (see phase 3 note).
- [x] **Phase 5 — app wrapper.** `app.py` (Shiny for Python): `ui_wrapper`
  (navbar/footer/chrome, sandbox banner, auth-status), `server_wrapper` (OAuth2
  PKCE login + simulated sandbox login, per-session token, `/me` load, logout),
  `global_wrapper`. `shinyjs` dropped (replaced by a `csip_reset`
  custom-message handler); message-sending effects are `async`. Ported the
  `ui_wrapper`/`server_wrapper`/`global_wrapper` cases from
  `test-sandbox-wrapper.R` (77 passing); the deep `testServer` reactive cases
  have no simple Shiny-for-Python equivalent, so sandbox-seed + auth-status
  logic is covered via extracted pure helpers, and a real `App` is smoke-built.
  Phase 3's token mechanism was corrected here (contextvar → session
  `WeakKeyDictionary`).
- [x] **Phase 6 — docs + packaging.** mkdocs-material + mkdocstrings site
  (`mkdocs.yml`, `docs/`), runnable `examples/` (`warehouse_ingest.py`,
  `app.py`), README quickstart. `uv build` produces sdist + wheel; wheel
  verified to install and import in a clean venv.
  **PyPI publish is intentionally NOT done here** — it is an outward-facing
  release and requires credentials the assistant cannot handle. Publish with
  `uv publish` (or `twine upload dist/*`) when ready.

Phases 1–4 are framework-independent and safe to build in any order after 1.

## Dashboard dogfooding findings (fixed)

Building a real Shiny for Python dashboard against the package surfaced three
issues from common workflows, all fixed:

1. **`fetch_org_options` shape** — returned R's `[{"label","value"}]`, which
   crashed `ui.input_select` (`TypeError: unhashable type: 'dict'`). Now returns
   a `{value: label}` dict that feeds `input_select(choices=...)` directly.
   Deliberate divergence from R (whose shape suited `selectInput`).
2. **No path from nested results to a table** — `fetch_profiles` returns deeply
   nested dicts; `render.DataGrid` rejected them ("Unsupported dataframe type").
   Added `flatten_profile` producing scalar rows; docs show wrapping in a
   pandas/polars frame (kept out of package deps).
3. **Fixed-bottom footer overlapped content** — the R wrapper's
   `padding-bottom: 80px` was dropped in the port; restored in `ui_wrapper`.

## Ponytail audit cleanup

Repo-wide over-engineering pass, all applied: dropped `global_wrapper` (no-op in
Python) and `flatten_record` (no caller; only `flatten_profile` is used); deleted
`config.clear_token` (dead) and `tests/test_smoke.py` (redundant vs 78 real
tests); hoisted the duplicated `_message` helper into `config`; inlined the
`_rmtree` wrapper to `shutil.rmtree`. ~55 lines removed, no dep changes.

## Parity caveats carried over from R

* **Validation parity is approximate.** Sandbox ingestion validates against the
  JSON Schema with `jsonschema` (Python) where R used Ajv via `jsonvalidate`.
  Validators differ on edge cases (e.g. `format` keyword enforcement), and the
  real server enforces things the schema can't (subject resolution against
  registered profiles, duplicate/dataset handling, token permissions). A green
  sandbox run is **necessary but not sufficient** for production acceptance.
* **Subject linkage** is emulated only for athletes registered with
  `create_profile()`, resolved at read time; unresolved → `subject = None`
  (production would reject).
