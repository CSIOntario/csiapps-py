# Parity checklist

This page is a translation guide for the operational parity between the R and
Python versions of `csiapps`. Each row is a capability; the R and Python columns
give the function(s) that provide it. An empty cell marks a capability that
exists in only one language (see the notes below for why).

The two packages have **full feature parity** — every capability is available in
both — with two exceptions that are intentional and explained under
[Intentional divergences](#intentional-divergences).

## Configuration

| Capability | R | Python |
|---|---|---|
| Set the target institute | `set_institute()` | `set_institute()` |
| Check whether sandbox mode is on | `is_sandbox_mode()` | `is_sandbox_mode()` |
| Force sandbox mode on/off | `options(csiapps.sandbox = )` | `set_sandbox_mode()` |
| Check auth environment / secrets | `check_secrets()` | `check_secrets()` |

## REST API client

| Capability | R | Python |
|---|---|---|
| Authenticated request to any endpoint | `make_request()` | `make_request()` |
| Fetch organisation options | `fetch_org_options()` | `fetch_org_options()` |
| Fetch profiles (auto-paginating) | `fetch_profiles()` | `fetch_profiles()` |
| Fetch a single profile by id | `fetch_profile()` | `fetch_profile()` |
| Flatten a profile into a table row |  | `flatten_profile()` |

## Sandbox

| Capability | R | Python |
|---|---|---|
| Register a JSON schema for a source | `register_sandbox_schema()` | `register_sandbox_schema()` |
| Create a dummy sport organization | `create_sport_org()` | `create_sport_org()` |
| Create dummy athlete profiles | `create_profile()` | `create_profile()` |
| Clear sandbox state | `clear_sandbox()` | `clear_sandbox()` |
| Open the sandbox payload folder | `browse_sandbox()` | `browse_sandbox()` |

## Shiny app wrappers

| Capability | R | Python |
|---|---|---|
| Wrap app UI (navbar/footer/auth status) | `ui_wrapper()` | `ui_wrapper()` |
| Wrap app server (OAuth2 / sandbox login) | `server_wrapper()` | `server_wrapper()` |
| Make globals visible to the server | `global_wrapper()` |  |

## Intentional divergences

These are the only differences in the public surface, and each exists for a
concrete reason:

- **`global_wrapper()` is R-only.** It evaluates a code block in R's global
  environment so top-level objects are reachable from the `server` function — an
  R Shiny scoping concern. In Shiny for Python a module-level assignment is
  already captured by the server closure, so no equivalent is needed.
- **`flatten_profile()` is Python-only.** Shiny for Python's `render.data_frame`
  rejects the nested dicts `fetch_profiles()` returns, so this helper flattens
  one profile into scalar fields for a table. R users flatten inline with
  `data.frame()` / `do.call(rbind, ...)`, so no dedicated helper ships in R.

Beyond the public surface, a few behaviours differ by language idiom or as a
deliberate hardening — same capability, adapted shape:

- **Sandbox toggle mechanism.** R uses the `csiapps.sandbox` **option**
  (`options(csiapps.sandbox = FALSE)`); Python has no options system, so it uses
  the `set_sandbox_mode()` function. Both also respect `CSIAPPS_ENV=production`.
- **`fetch_org_options()` return shape.** R returns `label`/`value` pairs for
  `selectInput()`; Python returns a `{value: label}` dict for
  `ui.input_select(choices = )`. Same data, framework-native shape.
- **`fetch_profile()` argument order.** R is `fetch_profile(token, profile_id)`;
  Python puts the required argument first: `fetch_profile(profile_id, token)`.
  Equivalent when called by keyword.
- **Schema validator engine.** Sandbox ingestion validates with Ajv (via
  `jsonvalidate`) in R and `jsonschema` (Draft 7) in Python. Error wording and
  some `format`-keyword edge cases differ.
- **`fetch_profiles()` pagination guard.** The Python port adds a `max_pages`
  bound with cycle detection and a truncation warning; the R version paginates
  until the API stops returning a `next` link. Python is a hardened superset.

## Function reference

- **R:** [R reference](https://csiontario.github.io/csiapps/reference/) (pkgdown)
- **Python:** [Python reference](api.md) (on this site)
