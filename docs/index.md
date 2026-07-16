# csiapps

Helper functions and utilities for CSI data warehouse ingestion and
[Shiny](https://shiny.posit.co/) web applications. `csiapps` ships as **two
packages with full feature parity** — one for R, one for Python — so a team can
work in either language against the same CSIAPPS warehouse and registration API.

This site documents **both**: every tutorial shows the R and the Python code
side by side. Pick your language with the tabs and it stays selected across the
whole page.

The library has three layers:

- **API client** — `make_request`, `fetch_org_options`, `fetch_profiles`,
  `fetch_profile`, with OAuth2 PKCE auth.
- **Sandbox** — a local, in-memory emulation of the warehouse and registration
  API so you can develop the full schema → ingest → retrieve workflow with no
  network and no credentials. **Sandbox mode is on by default.**
- **App wrapper** — `ui_wrapper` / `server_wrapper` add a consistent CSI
  navbar/footer and handle authentication for a Shiny app.

## Installation

=== "R"

    ```r
    # install.packages("remotes")
    remotes::install_github("CSIOntario/csiapps")
    ```

=== "Python"

    ```bash
    pip install csiapps
    ```

## Sandbox mode is the default

Every call routes to the local sandbox until you explicitly turn it off, so you
never hit the production warehouse by accident:

=== "R"

    ```r
    library(csiapps)

    is_sandbox_mode()   # TRUE

    # Turn it off for deployment:
    options(csiapps.sandbox = FALSE)   # or set CSIAPPS_ENV=production
    ```

=== "Python"

    ```python
    import csiapps

    csiapps.is_sandbox_mode()   # True

    # Turn it off for deployment:
    csiapps.set_sandbox_mode(False)          # or set CSIAPPS_ENV=production
    ```

## Where to go next

- **[Shiny apps](shiny-apps.md)** — wrap an app's UI and server with CSIAPPS
  authentication and chrome.
- **[REST API](rest-api.md)** — `make_request` and the registration helpers.
- **[Sandbox mode](sandbox.md)** — the full local warehouse/registration
  workflow.
- **[Parity checklist](parity.md)** — how each R function maps to its Python
  equivalent.

## Function reference

- **Python:** [Python reference](api.md) (on this site).
- **R:** [R reference](https://csiontario.github.io/csiapps/reference/)
  (pkgdown).
