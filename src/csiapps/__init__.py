"""csiapps: Python port of the CSIO ``csiapps`` R package.

Helper functions and utilities for CSI data warehouse ingestion and Shiny (for
Python) or Dash web applications.

The public API mirrors the R package's ``NAMESPACE``, module by module:

* ``config``  -- set_institute, is_sandbox_mode
* ``auth``    -- check_secrets, PKCE, token exchange
* ``client``  -- make_request, fetch_org_options/profiles
* ``sandbox`` -- register_sandbox_schema, create_*, ...
* ``chrome``  -- shared navbar/footer constants and copy

Everything exported here is framework-independent: ingestion, auth, the HTTP
client, and the sandbox depend on no web framework, so ``import csiapps`` pulls
in neither Shiny nor Dash. The web-app wrappers live in framework submodules,
imported explicitly, each behind its own optional dependency:

    from csiapps.shiny import ui_wrapper, server_wrapper   # pip install 'csiapps[shiny]'
    from csiapps.dash import layout_wrapper, attach        # pip install 'csiapps[dash]'
"""

from .auth import check_secrets
from .client import (
    fetch_org_options,
    fetch_profile,
    fetch_profiles,
    flatten_profile,
    make_request,
    token_ready,
)
from .config import is_sandbox_mode, set_institute, set_sandbox_mode
from .sandbox import (
    browse_sandbox,
    clear_sandbox,
    create_profile,
    create_sport_org,
    register_sandbox_schema,
)

__version__ = "0.3.2"

__all__ = [
    "__version__",
    "browse_sandbox",
    "check_secrets",
    "clear_sandbox",
    "create_profile",
    "create_sport_org",
    "fetch_org_options",
    "fetch_profile",
    "fetch_profiles",
    "flatten_profile",
    "is_sandbox_mode",
    "make_request",
    "register_sandbox_schema",
    "set_institute",
    "set_sandbox_mode",
    "token_ready",
]
