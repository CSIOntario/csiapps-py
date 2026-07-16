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

**app.py** — `ui_wrapper`, `server_wrapper`, `global_wrapper`, navbar/footer/CSS
chrome, sandbox banner, `/me` load, logout re-seed.

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
* **Per-session token** (`session$userData$csiapps_token`): the Shiny for Python
  server closure is already per-session, so the cross-user-leakage fix is free.
  `make_request()` is called outside that closure, so `.current_token()` finds
  the active session via a `contextvar` set on session start, falling back to
  the `CSIAPPS_ACCESS_TOKEN` env var (exactly as R does).

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
- [ ] **Phase 3 — client.** `client.py`: `make_request`, pagination, the three
  `fetch_*`. Mock HTTP with `respx`.
- [ ] **Phase 4 — sandbox.** `sandbox.py`: router, ingest, schema validation,
  dummy registry. Port `test-sandbox*.R` → pytest.
- [ ] **Phase 5 — app wrapper.** `app.py` (Shiny for Python).
- [ ] **Phase 6 — docs + publish.** mkdocs-material (≈ pkgdown), examples,
  PyPI release.

Phases 1–4 are framework-independent and safe to build in any order after 1.

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
