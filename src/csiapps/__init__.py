"""csiapps: Python port of the CSIO ``csiapps`` R package.

Helper functions and utilities for CSI data warehouse ingestion and Shiny (for
Python) or Dash web applications.

The public API mirrors the R package's ``NAMESPACE`` and lands module by module
across the porting phases (see ``PORTING_PLAN.md``):

* ``config``  -- set_institute, is_sandbox_mode          (phase 2)
* ``auth``    -- check_secrets, PKCE, token exchange       (phase 2)
* ``client``  -- make_request, fetch_org_options/profiles  (phase 3)
* ``sandbox`` -- register_sandbox_schema, create_*, ...     (phase 4)
* ``app``     -- ui_wrapper, server_wrapper                 (phase 5)
* ``chrome``  -- shared navbar/footer constants and copy    (phase 6)
* ``dash``    -- attach, layout_wrapper                     (phase 6)

Everything exported here is framework-independent except ``ui_wrapper`` and
``server_wrapper``, which are Shiny. Dash support lives in the ``csiapps.dash``
submodule and is imported explicitly, so it costs nothing to an app that does
not use it:

    from csiapps.dash import attach, layout_wrapper

It requires the optional dependencies: ``pip install 'csiapps[dash]'``.
"""

from .app import server_wrapper, ui_wrapper
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

__version__ = "0.2.0"

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
    "server_wrapper",
    "set_institute",
    "set_sandbox_mode",
    "token_ready",
    "ui_wrapper",
]
