# csiapps (Python)

Python port of the CSIO [`csiapps`](https://github.com/CSIOntario/csiapps) R
package. Helper functions and utilities for CSI data warehouse ingestion and
[Shiny for Python](https://shiny.posit.co/py/) web applications.

The library has three layers:

- **API client** — `make_request`, `fetch_org_options`, `fetch_profiles`,
  `fetch_profile`, with OAuth2 PKCE auth.
- **Sandbox** — a local, in-memory emulation of the warehouse and registration
  API so you can develop the full schema → ingest → retrieve workflow with no
  network and no credentials. **Sandbox mode is on by default.**
- **App wrapper** — `ui_wrapper` / `server_wrapper` add a consistent CSI
  navbar/footer and handle authentication for a Shiny app.

## Installation

```bash
pip install csiapps
```

## Sandbox mode is the default

Every call routes to the local sandbox until you explicitly turn it off, so you
never hit the production warehouse by accident:

```python
import csiapps

csiapps.is_sandbox_mode()   # True

# Turn it off for deployment:
csiapps.set_sandbox_mode(False)          # or set CSIAPPS_ENV=production
```

See [Usage](usage.md) for worked examples and the [API reference](api.md) for
every function.
