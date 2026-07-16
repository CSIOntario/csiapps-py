"""csiapps: Python port of the CSIO ``csiapps`` R package.

Helper functions and utilities for CSI data warehouse ingestion and Shiny
(for Python) web applications.

The public API mirrors the R package's ``NAMESPACE`` and lands module by module
across the porting phases (see ``PORTING_PLAN.md``):

* ``config``  -- set_institute, is_sandbox_mode          (phase 2)
* ``auth``    -- check_secrets, PKCE, token exchange       (phase 2)
* ``client``  -- make_request, fetch_org_options/profiles  (phase 3)
* ``sandbox`` -- register_sandbox_schema, create_*, ...     (phase 4)
* ``app``     -- ui_wrapper, server_wrapper, global_wrapper (phase 5)
"""

from .auth import check_secrets
from .config import is_sandbox_mode, set_institute, set_sandbox_mode

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "check_secrets",
    "is_sandbox_mode",
    "set_institute",
    "set_sandbox_mode",
]
