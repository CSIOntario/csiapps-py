"""Package configuration: target institute and sandbox-mode resolution.

Mirrors the R package's ``package_state`` environment, the ``csiapps.sandbox``
option, and the ``CSIAPPS_ENV`` environment variable. Ports of
``set_institute()``, ``is_sandbox_mode()``, and the ``*_URL()`` helpers from
``R/utils.R``.

ponytail: module-level state dict, not a Config class -- a direct mirror of the
R global. There is only ever one configuration per process.
"""

import os

# ---- Warehouse / registration endpoint constants ----
SPORT_ORG_ENDPOINT = "/api/registration/organization/"
PROFILE_ENDPOINT = "/api/registration/profile/"

_VALID_INSTITUTES = ("csipacific", "csiontario")

# `sandbox_override` mirrors the R `csiapps.sandbox` option: None means "unset,
# fall back to CSIAPPS_ENV"; anything else forces the mode (see is_sandbox_mode).
_state = {
    "institute": "csipacific",
    "sandbox_override": None,
}


def set_institute(institute: str = "csipacific") -> None:
    """Set the target institute for API calls.

    Args:
        institute: One of ``"csipacific"`` or ``"csiontario"``.
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
    """Force sandbox mode on or off (Python analog of R's ``options(csiapps.sandbox=)``).

    Pass ``True``/``False`` to override, or ``None`` to clear the override and
    fall back to the ``CSIAPPS_ENV`` environment variable.
    """
    _state["sandbox_override"] = enabled


def is_sandbox_mode() -> bool:
    """Whether sandbox mode is enabled globally.

    Sandbox mode is **on by default** so requests never reach production by
    accident. Resolution order (matching the R package):

    1. An explicit override set via :func:`set_sandbox_mode` always wins. Only
       the literal ``True`` enables it; any other set value is treated as
       ``False`` (mirrors R's ``isTRUE()``).
    2. Otherwise ``CSIAPPS_ENV``: only the literal ``"production"`` disables
       sandbox; any other non-empty value keeps it on (fail-safe against typos).
    3. Otherwise ``True``.
    """
    override = _state["sandbox_override"]
    if override is not None:
        return override is True
    env = os.environ.get("CSIAPPS_ENV", "")
    if env:
        return env != "production"
    return True


def clear_token() -> None:
    """Remove the process-wide ``CSIAPPS_ACCESS_TOKEN`` environment variable."""
    os.environ.pop("CSIAPPS_ACCESS_TOKEN", None)
