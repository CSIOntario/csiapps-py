"""Package configuration: target institute and sandbox-mode resolution.

Mirrors the R package's ``package_state`` environment, the ``csiapps.sandbox``
option, and the ``CSIAPPS_ENV`` environment variable. Ports of
``set_institute()``, ``is_sandbox_mode()``, and the ``*_URL()`` helpers from
``R/utils.R``.

ponytail: module-level state dict, not a Config class -- a direct mirror of the
R global. There is only ever one configuration per process.
"""

import os
import sys

# ---- Warehouse / registration endpoint constants ----
SPORT_ORG_ENDPOINT = "/api/registration/organization/"
PROFILE_ENDPOINT = "/api/registration/profile/"

_VALID_INSTITUTES = ("csipacific", "csiontario")


def _message(text: str) -> None:
    # Informational output to stderr (mirrors R's message()), keeping stdout clean
    # for actual return values. Shared by auth.py and sandbox.py.
    print(text, file=sys.stderr)

# `sandbox_override` mirrors the R `csiapps.sandbox` option: None means "unset,
# fall back to CSIAPPS_ENV"; anything else forces the mode (see is_sandbox_mode).
_state = {
    "institute": "csipacific",
    "sandbox_override": None,
}


def set_institute(institute: str = "csipacific") -> None:
    """Set the target institute for all subsequent API calls.

    The institute determines the base host every request is sent to and which
    logo the Shiny chrome renders. It is process-wide global state (a direct
    mirror of the R package's ``package_state`` environment): there is only ever
    one configured institute per process, so set it once at startup.

    Args:
        institute: Which CSI institute to target. Must be one of
            ``"csipacific"`` or ``"csiontario"``. Defaults to ``"csipacific"``.

    Raises:
        ValueError: If ``institute`` is not one of the two supported values.

    Example:
        ```python
        import csiapps

        csiapps.set_institute("csiontario")
        ```

    Note:
        This only selects the target host; it does not authenticate. See
        [`check_secrets`][csiapps.auth.check_secrets] for credential setup and
        [`is_sandbox_mode`][csiapps.config.is_sandbox_mode] for whether requests
        actually reach the network.
    """
    if not (isinstance(institute, str) and institute in _VALID_INSTITUTES):
        raise ValueError(
            f"institute must be one of {_VALID_INSTITUTES!r}, got {institute!r}"
        )
    _state["institute"] = institute


def get_institute() -> str:
    return _state["institute"]


def site_url() -> str:
    return f"https://apps.{_state['institute']}.ca"


def auth_url() -> str:
    return f"{site_url()}/o/authorize/"


def token_url() -> str:
    return f"{site_url()}/o/token/"


def userinfo_url() -> str:
    return f"{site_url()}/api/csiauth/me"


def set_sandbox_mode(enabled: bool | None) -> None:
    """Force sandbox mode on or off, or clear the override.

    The Python analog of R's ``options(csiapps.sandbox = ...)``. An override set
    here takes precedence over the ``CSIAPPS_ENV`` environment variable when
    [`is_sandbox_mode`][csiapps.config.is_sandbox_mode] resolves the effective
    mode.

    Args:
        enabled: ``True`` to force sandbox mode on, ``False`` to force it off, or
            ``None`` to clear the override and fall back to ``CSIAPPS_ENV``.

    Example:
        Pin sandbox mode on for a test, then restore environment-based
        resolution afterwards:

        ```python
        import csiapps

        csiapps.set_sandbox_mode(True)
        csiapps.is_sandbox_mode()   # -> True

        csiapps.set_sandbox_mode(None)   # back to CSIAPPS_ENV
        ```

    Note:
        Only the literal ``True`` enables sandbox mode; any other truthy value is
        treated as ``False`` (mirrors R's ``isTRUE()``). See
        [`is_sandbox_mode`][csiapps.config.is_sandbox_mode] for the full
        resolution order.
    """
    _state["sandbox_override"] = enabled


def is_sandbox_mode() -> bool:
    """Report whether sandbox mode is currently enabled.

    Sandbox mode is the fail-safe default: when it is on, the ``fetch_*`` and
    [`make_request`][csiapps.client.make_request] helpers route to the local
    in-memory emulator in the `csiapps.sandbox` module instead of the network, so no
    request can reach production by accident. Every helper that hits the API
    consults this function when its own ``sandbox`` argument is left as ``None``.

    Resolution order (matching the R package):

    1. An explicit override set via
       [`set_sandbox_mode`][csiapps.config.set_sandbox_mode] always wins. Only
       the literal ``True`` enables it; any other set value is treated as
       ``False`` (mirrors R's ``isTRUE()``).
    2. Otherwise the ``CSIAPPS_ENV`` environment variable: only the literal
       ``"production"`` disables sandbox; any other non-empty value keeps it on
       (fail-safe against typos such as ``"prod"``).
    3. Otherwise ``True``.

    Returns:
        bool: ``True`` if sandbox mode is active (requests are emulated
        locally), ``False`` if requests go to the live warehouse.

    Example:
        ```python
        import os
        import csiapps

        csiapps.set_sandbox_mode(None)              # use the environment
        os.environ["CSIAPPS_ENV"] = "production"
        csiapps.is_sandbox_mode()                   # -> False
        ```
    """
    override = _state["sandbox_override"]
    if override is not None:
        return override is True
    env = os.environ.get("CSIAPPS_ENV", "")
    if env:
        return env != "production"
    return True
