"""Authenticated CSIAPPS API client.

Ports ``make_request()`` and the registration helpers (``fetch_org_options``,
``fetch_profiles``, ``fetch_profile``) from ``R/utils.R``. The sandbox branches
delegate to :mod:`csiapps.sandbox` (phase 4) via the internal functions:

* ``sandbox._make_sandbox_request(...)``       -- warehouse request router
* ``sandbox._sandbox_org_options()``            -- dummy sport-org options
* ``sandbox._sandbox_profiles(sport_org_id)``   -- dummy profiles
* ``sandbox._sandbox_profile(profile_id)``      -- one dummy profile or None

They are imported lazily so this module has no import-time dependency on the
sandbox layer.
"""

import contextvars
import os
import time
from urllib.parse import quote

import httpx

from . import config

# Per-session access token, set by the Shiny app wrapper (server_wrapper) so
# concurrent users never share a token. Outside a session it is None and we fall
# back to the process-wide env var -- mirrors R's .current_token() reading
# session$userData$csiapps_token then CSIAPPS_ACCESS_TOKEN.
_session_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "csiapps_session_token", default=None
)

# Transient statuses worth retrying (httr2 req_retry retried on these).
_RETRY_STATUSES = {429, 500, 502, 503, 504}


def current_token() -> str:
    """Resolve the access token: per-session first, then ``CSIAPPS_ACCESS_TOKEN``."""
    tok = _session_token.get()
    if tok:
        return tok
    return os.environ.get("CSIAPPS_ACCESS_TOKEN", "")


def _perform(method, url, *, params=None, json=None, headers=None, timeout=20, max_tries=3):
    # Minimal retry on transient statuses; connection retries are left to httpx.
    # ponytail: fixed 3-try exponential backoff capped at 10s, matching the R
    # req_retry(max_tries = 3, max_seconds = 10). Swap for tenacity only if the
    # retry policy needs to grow.
    delay = 0.5
    resp = None
    for attempt in range(max_tries):
        resp = httpx.request(
            method, url, params=params, json=json, headers=headers, timeout=timeout
        )
        if resp.status_code not in _RETRY_STATUSES or attempt == max_tries - 1:
            return resp
        time.sleep(min(delay, 10))
        delay *= 2
    return resp


def _parse_response(resp, *, endpoint="", method="GET", query=None, verbose=False):
    status = resp.status_code
    txt = resp.text

    if len(txt) == 0:
        return []

    if verbose:
        print(f"{method} request to {endpoint} returned status {status}")
        if query:
            print("  params:", ", ".join(f"{k}={v}" for k, v in query.items()))
        print("  response:\n", txt)

    if status >= 400:
        raise RuntimeError(f"API request failed ({status}): {txt}")

    try:
        return resp.json()
    except ValueError as e:
        return {"raw": txt, "error": "json_parse_error", "message": str(e)}


def _http_request(
    endpoint, method, body, query, headers, token, timeout, verbose, paginate, max_pages
):
    if not token:
        raise RuntimeError(
            "make_request: no CSIAPPS_ACCESS_TOKEN set; user not authenticated?"
        )

    url = config.site_url().rstrip("/") + "/" + endpoint.lstrip("/")
    req_headers = {"Authorization": f"Bearer {token}"}
    if headers:
        req_headers.update(headers)

    if paginate:
        pages = []
        next_url = url
        params = query
        for _ in range(max_pages):
            resp = _perform(
                method, next_url, params=params, json=body, headers=req_headers, timeout=timeout
            )
            page = _parse_response(
                resp, endpoint=endpoint, method=method, query=params, verbose=verbose
            )
            pages.append(page)
            next_url = page.get("next") if isinstance(page, dict) else None
            params = None  # `next` already carries its query
            if not next_url:
                break
        return pages

    resp = _perform(method, url, params=query, json=body, headers=req_headers, timeout=timeout)
    return _parse_response(resp, endpoint=endpoint, method=method, query=query, verbose=verbose)


def make_request(
    endpoint,
    method="GET",
    body=None,
    query=None,
    headers=None,
    token=None,
    timeout=20,
    verbose=False,
    paginate=False,
    max_pages=50,
    sandbox=None,
):
    """Make an authenticated API request to CSIAPPS.

    When ``sandbox`` is ``True`` (the default in development) the request is
    routed to the local sandbox instead of the network; only warehouse endpoints
    are emulated (see :mod:`csiapps.sandbox`). Otherwise a real HTTP request is
    made, with the token resolved per-session then from ``CSIAPPS_ACCESS_TOKEN``.
    """
    if query is None:
        query = {}
    if headers is None:
        headers = {}
    if sandbox is None:
        sandbox = config.is_sandbox_mode()

    if sandbox:
        from . import sandbox as _sb

        return _sb._make_sandbox_request(
            endpoint=endpoint,
            method=method,
            body=body,
            query=query,
            verbose=verbose,
            paginate=paginate,
        )

    if not token:
        token = current_token()

    return _http_request(
        endpoint, method, body, query, headers, token, timeout, verbose, paginate, max_pages
    )


def fetch_org_options(token=None, sandbox=None):
    """Fetch organisation options as a list of ``{"label", "value"}`` dicts.

    Suitable for Shiny ``ui.input_select`` choices.
    """
    if sandbox is None:
        sandbox = config.is_sandbox_mode()
    if sandbox:
        from . import sandbox as _sb

        return _sb._sandbox_org_options()

    if not token:
        token = current_token()
    if not token:
        raise RuntimeError(
            "fetch_org_options: no CSIAPPS_ACCESS_TOKEN set; user not authenticated?"
        )

    url = config.site_url().rstrip("/") + config.SPORT_ORG_ENDPOINT
    resp = _perform(
        "GET",
        url,
        params={"limit": 1000},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"fetch_org_options failed ({resp.status_code}): {resp.text}")

    items = resp.json()
    if isinstance(items, dict) and items.get("results") is not None:
        return [{"label": it.get("name"), "value": it.get("id")} for it in items["results"]]
    if isinstance(items, list):
        return [{"label": v, "value": v} for v in items]
    return []


def fetch_profiles(token=None, filters=None, sandbox=None):
    """Fetch all profiles accessible to the token, auto-paginating.

    ``filters`` is a dict of query parameters (e.g. ``{"sport_org_id": 42}``).
    In sandbox mode only ``sport_org_id`` is honoured.
    """
    if filters is None:
        filters = {}
    if sandbox is None:
        sandbox = config.is_sandbox_mode()
    if sandbox:
        from . import sandbox as _sb

        return _sb._sandbox_profiles(filters.get("sport_org_id"))

    if not token:
        token = current_token()
    if not token:
        raise RuntimeError(
            "fetch_profiles: no CSIAPPS_ACCESS_TOKEN set; user not authenticated?"
        )

    url = config.site_url().rstrip("/") + config.PROFILE_ENDPOINT
    params = {**filters, "limit": 100, "offset": 0}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    out = []
    next_url = url
    while next_url:
        resp = _perform("GET", next_url, params=params, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"fetch_profiles failed ({resp.status_code}): {resp.text}")
        payload = resp.json()
        out.extend(payload.get("results") or [])
        next_url = payload.get("next")
        params = None  # `next` already carries its query
    return out


def fetch_profile(profile_id, token=None, sandbox=None):
    """Fetch a single profile by id. Returns the profile dict, or ``None`` in
    sandbox mode when no such id exists."""
    if sandbox is None:
        sandbox = config.is_sandbox_mode()
    if sandbox:
        from . import sandbox as _sb

        return _sb._sandbox_profile(profile_id)

    if not token:
        token = current_token()
    if not token:
        raise RuntimeError(
            "fetch_profile: no CSIAPPS_ACCESS_TOKEN set; user not authenticated?"
        )

    # URL-encode the id so an unusual value can't alter the request path.
    enc_id = quote(str(profile_id), safe="")
    url = config.site_url().rstrip("/") + config.PROFILE_ENDPOINT + enc_id
    resp = _perform(
        "GET", url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"fetch_profile failed ({resp.status_code}): {resp.text}")
    return resp.json()
