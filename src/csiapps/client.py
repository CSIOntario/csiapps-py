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

import os
import time
from urllib.parse import quote

import httpx

from . import config

# ---- token-adapter registry -------------------------------------------
#
# The core resolves the per-context access token through a small registry of
# adapters rather than knowing about any web framework. A framework submodule
# (csiapps.shiny, csiapps.dash) registers an adapter when it is imported; a pure
# ingestion script registers none and falls back to the environment. This is
# what keeps client.py free of any shiny/flask import.
#
# An adapter is any object with two methods:
#   read_token() -> str | None
#       The token for the current context if this framework is active, else
#       None (a Shiny adapter returns None with no active session, a Flask
#       adapter returns None outside a request context).
#   handle_missing_token() -> bool
#       Called by the auth gate when no token was found. Return True if handled
#       (e.g. Shiny quietly cancels a reactive), False to let the core raise.
_token_adapters: list = []


def register_token_adapter(adapter) -> None:
    """Register a framework token adapter for token resolution (idempotent)."""
    if adapter not in _token_adapters:
        _token_adapters.append(adapter)


# Transient statuses worth retrying (httr2 req_retry retried on these).
_RETRY_STATUSES = {429, 500, 502, 503, 504}


def current_token() -> str:
    """Resolve the access token: framework adapters first, then env var.

    Each registered adapter is asked in turn and the first non-empty token wins.
    Adapters are inert outside their own framework's active context, so their
    order does not matter in practice -- no process is both a live Shiny session
    and a Flask request at once. When no adapter yields a token, the
    ``CSIAPPS_ACCESS_TOKEN`` environment variable is the single-token
    development fallback; it lives in the core so pure ingestion works with no
    web framework installed, and a per-user token always outranks it.
    """
    for adapter in _token_adapters:
        tok = adapter.read_token()
        if tok:
            return tok
    return os.environ.get("CSIAPPS_ACCESS_TOKEN", "")


def token_ready() -> bool:
    """Whether a CSIAPPS access token is available for the current context.

    Returns ``True`` once a token is available: inside a Shiny app wrapped by
    :func:`csiapps.shiny.server_wrapper`, the per-session token stored at login;
    inside a Dash app wrapped by :func:`csiapps.dash.attach`, the per-request
    token from the Flask session; outside both, the ``CSIAPPS_ACCESS_TOKEN``
    environment variable.

    The check is reactive-friendly: called from a reactive context it takes a
    dependency on the session's token, so a guard like ``req(token_ready())``
    re-fires automatically when login completes instead of sticking in the
    cancelled state. :func:`make_request` and the ``fetch_*`` helpers already
    gate themselves this way, so an explicit guard is only needed for work that
    should wait for login without making an API call (e.g. processing an
    uploaded file).
    """
    return bool(current_token())


def _auth_gate(what: str):
    """Handle a missing token: let an adapter gate it, else raise loudly.

    Each registered adapter gets a chance to handle the missing token. The Shiny
    adapter, inside a reactive context, cancels the computation with
    ``req(False)`` (it took a dependency on the token in ``read_token``, so it
    re-runs once login completes). Outside a reactive context, and for Dash
    (where ``attach``'s guard means a callback never runs unauthenticated), no
    adapter handles it -- a missing token there is a real configuration error,
    so the core fails loudly.
    """
    for adapter in _token_adapters:
        if adapter.handle_missing_token():
            return
    raise RuntimeError(
        f"{what}: no CSIAPPS_ACCESS_TOKEN set; user not authenticated?"
    )


def _perform(method, url, *, params=None, json=None, headers=None, timeout=20, max_tries=3):
    # Minimal retry on transient statuses; connection retries are left to httpx.
    # ponytail: fixed 3-try exponential backoff capped at 10s, matching the R
    # req_retry(max_tries = 3, max_seconds = 10). Swap for tenacity only if the
    # retry policy needs to grow.
    delay = 0.5
    resp = None
    for attempt in range(max_tries):
        resp = httpx.request(
            method, url, params=params, json=json, headers=headers, timeout=timeout,
            follow_redirects=True,
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
        _auth_gate("make_request")

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
    endpoint: str,
    method: str = "GET",
    body: dict | None = None,
    query: dict | None = None,
    headers: dict | None = None,
    token: str | None = None,
    timeout: float = 20,
    verbose: bool = False,
    paginate: bool = False,
    max_pages: int = 50,
    sandbox: bool | None = None,
) -> dict | list:
    """Make an authenticated request to a CSIAPPS warehouse endpoint.

    This is the low-level primitive the ``fetch_*`` helpers build on; reach for
    it directly when you need an endpoint those helpers do not cover. In sandbox
    mode the request is served by the local emulator with no network or auth; in
    production it is sent over HTTPS with a bearer token and a bounded retry on
    transient failures.

    Args:
        endpoint: Warehouse endpoint path, with or without leading/trailing
            slashes (e.g. ``"api/warehouse/data-records"``).
        method: HTTP method, e.g. ``"GET"`` or ``"POST"``. Defaults to ``"GET"``.
        body: JSON-serialisable request body for write methods. ``None`` sends no
            body.
        query: Query-string parameters as a dict. ``None`` is treated as ``{}``.
        headers: Extra request headers merged over the ``Authorization`` header.
            Ignored in sandbox mode.
        token: Bearer token to authenticate with. When omitted it is resolved
            per-session and then from the ``CSIAPPS_ACCESS_TOKEN`` environment
            variable (see the `current_token` resolver).
        timeout: Per-request timeout in seconds. Defaults to ``20``.
        verbose: If ``True``, print the method, endpoint, params, and raw
            response body for debugging. Defaults to ``False``.
        paginate: If ``True``, follow the response's ``next`` links and return a
            list of pages rather than a single response. Defaults to ``False``.
        max_pages: Upper bound on pages fetched when ``paginate`` is ``True``,
            guarding against an unbounded loop. Defaults to ``50``.
        sandbox: Force sandbox (``True``) or production (``False``) routing.
            ``None`` (the default) resolves via
            [`is_sandbox_mode`][csiapps.config.is_sandbox_mode].

    Returns:
        dict | list: The parsed JSON response. A single request returns the
        decoded body (typically a ``dict``); with ``paginate=True`` it returns a
        ``list`` of page bodies. An empty response body yields ``[]``.

    Raises:
        RuntimeError: If no token is available in production mode, or if the API
            responds with a status of 400 or higher.

    Example:
        Read ingested records back from the sandbox warehouse:

        ```python
        import csiapps

        csiapps.set_sandbox_mode(True)
        csiapps.make_request(
            "api/warehouse/data-records",
            query={"source_uuid": "my-source"},
        )
        # -> {'count': 0, 'next': None, 'previous': None, 'results': []}
        ```

    Note:
        In sandbox mode only warehouse endpoints are emulated (see the
        `csiapps.sandbox` module); an unrecognised endpoint raises a
        ``RuntimeError`` with a 501-style message. Transient production failures
        (429/500/502/503/504) are retried up to three times with exponential
        backoff.
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


def fetch_org_options(token: str | None = None, sandbox: bool | None = None) -> dict:
    """Fetch sport-organisation options as a ``{value: label}`` dict.

    The returned mapping plugs directly into a Shiny select input's ``choices=``
    argument, which maps each value to its displayed label.

    Args:
        token: Bearer token to authenticate with. When omitted it is resolved
            per-session and then from ``CSIAPPS_ACCESS_TOKEN``.
        sandbox: Force sandbox (``True``) or production (``False``) routing.
            ``None`` (the default) resolves via
            [`is_sandbox_mode`][csiapps.config.is_sandbox_mode].

    Returns:
        dict: A mapping of organisation id to organisation name, ready to pass as
        ``ui.input_select(..., choices=...)``. Empty if no organisations are
        available.

    Raises:
        RuntimeError: If no token is available in production mode, or the API
            responds with a status of 400 or higher.

    Example:
        ```python
        import csiapps

        csiapps.set_sandbox_mode(True)
        csiapps.create_sport_org("Rowing", id=7)
        csiapps.fetch_org_options()   # -> {7: 'Rowing'}
        ```

    Note:
        The ``{value: label}`` shape differs from the R package, which returned
        ``label``/``value`` pairs for R's ``selectInput`` — that shape raises
        ``TypeError: unhashable type: 'dict'`` in Shiny for Python. In sandbox
        mode the options come from the local registry populated by
        [`create_sport_org`][csiapps.sandbox.create_sport_org].
    """
    if sandbox is None:
        sandbox = config.is_sandbox_mode()
    if sandbox:
        from . import sandbox as _sb

        return _sb._sandbox_org_options()

    if not token:
        token = current_token()
    if not token:
        _auth_gate("fetch_org_options")

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
        return {it.get("id"): it.get("name") for it in items["results"]}
    if isinstance(items, list):
        return {v: v for v in items}
    return {}


def fetch_profiles(
    token: str | None = None,
    filters: dict | None = None,
    sandbox: bool | None = None,
    max_pages: int = 50,
) -> list:
    """Fetch all registration profiles accessible to the token, auto-paginating.

    Follows the API's pagination automatically and returns every profile as a
    flat list. Pass the result through
    [`flatten_profile`][csiapps.client.flatten_profile] before rendering it in a
    table.

    Args:
        token: Bearer token to authenticate with. When omitted it is resolved
            per-session and then from ``CSIAPPS_ACCESS_TOKEN``.
        filters: Query parameters narrowing the result, e.g.
            ``{"sport_org_id": 42}``. In sandbox mode only ``sport_org_id`` is
            honoured. ``None`` fetches all accessible profiles.
        sandbox: Force sandbox (``True``) or production (``False``) routing.
            ``None`` (the default) resolves via
            [`is_sandbox_mode`][csiapps.config.is_sandbox_mode].
        max_pages: Upper bound on pages fetched, matching
            [`make_request`][csiapps.client.make_request]. Defaults to ``50``.

    Returns:
        list: A list of profile dicts (nested, production-shaped). Empty if no
        profiles match.

    Raises:
        RuntimeError: If no token is available in production mode, or the API
            responds with a status of 400 or higher.

    Warns:
        UserWarning: If ``max_pages`` is reached while the server still
            advertises more pages; the result may be truncated. Pass a larger
            ``max_pages`` to fetch the rest.

    Example:
        ```python
        import csiapps

        csiapps.set_sandbox_mode(True)
        profiles = csiapps.fetch_profiles(filters={"sport_org_id": 7})
        rows = [csiapps.flatten_profile(p) for p in profiles]
        ```

    Note:
        Pagination terminates if the server ever repeats a ``next`` URL, so a
        misbehaving or hostile server cannot hang the app in an unbounded loop
        with unbounded memory growth.
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
        _auth_gate("fetch_profiles")

    url = config.site_url().rstrip("/") + config.PROFILE_ENDPOINT
    params = {**filters, "limit": 100, "offset": 0}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    out = []
    next_url = url
    seen = set()
    for _ in range(max_pages):
        if next_url in seen:  # server cycled `next` back to a page we already fetched
            break
        seen.add(next_url)
        resp = _perform("GET", next_url, params=params, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"fetch_profiles failed ({resp.status_code}): {resp.text}")
        payload = resp.json()
        out.extend(payload.get("results") or [])
        next_url = payload.get("next")
        params = None  # `next` already carries its query
        if not next_url:
            break
    else:
        # Loop hit max_pages with more pages still advertised. Warn loudly rather
        # than silently truncate, so a genuinely large result is never dropped
        # without notice (raise max_pages to fetch the rest).
        if next_url:
            import warnings

            warnings.warn(
                f"fetch_profiles: stopped after max_pages={max_pages}; results may be "
                f"truncated. Pass a larger max_pages to fetch all profiles.",
                stacklevel=2,
            )
    return out


def fetch_profile(
    profile_id: int | str,
    token: str | None = None,
    sandbox: bool | None = None,
) -> dict | None:
    """Fetch a single registration profile by id.

    Args:
        profile_id: The profile's id. Coerced to a string and URL-encoded before
            being placed in the request path, so an unusual value cannot alter
            the URL.
        token: Bearer token to authenticate with. When omitted it is resolved
            per-session and then from ``CSIAPPS_ACCESS_TOKEN``.
        sandbox: Force sandbox (``True``) or production (``False``) routing.
            ``None`` (the default) resolves via
            [`is_sandbox_mode`][csiapps.config.is_sandbox_mode].

    Returns:
        dict | None: The profile dict. In sandbox mode returns ``None`` when no
        profile with that id exists.

    Raises:
        RuntimeError: If no token is available in production mode, or the API
            responds with a status of 400 or higher.

    Example:
        ```python
        import csiapps

        csiapps.set_sandbox_mode(True)
        csiapps.fetch_profile(1)
        # -> {'id': 1, 'person': {...}, 'sport': {...}, 'status': 'ACTIVE', ...}
        ```

    Note:
        A production ``GET`` for a missing id raises ``RuntimeError`` (from the
        4xx status) rather than returning ``None``; only the sandbox reader
        distinguishes "not found" as ``None``.
    """
    if sandbox is None:
        sandbox = config.is_sandbox_mode()
    if sandbox:
        from . import sandbox as _sb

        return _sb._sandbox_profile(profile_id)

    if not token:
        token = current_token()
    if not token:
        _auth_gate("fetch_profile")

    # URL-encode the id so an unusual value can't alter the request path.
    # Keep the trailing slash: the DRF detail route is `.../profile/<id>/`, and
    # a slash-less URL 301-redirects (empty body) on APPEND_SLASH backends.
    enc_id = quote(str(profile_id), safe="")
    url = config.site_url().rstrip("/") + config.PROFILE_ENDPOINT + enc_id + "/"
    resp = _perform(
        "GET", url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"fetch_profile failed ({resp.status_code}): {resp.text}")
    return resp.json()


# fetch_profiles() returns deeply nested dicts; passing them straight to a Shiny
# data frame fails ("Unsupported dataframe type"). flatten_profile turns one
# profile into scalar fields so a list of them builds a table (wrap in
# pandas/polars for render.data_frame).
def flatten_profile(p: dict) -> dict:
    """Flatten a nested registration profile into a scalar row.

    [`fetch_profiles`][csiapps.client.fetch_profiles] returns deeply nested
    dicts; passing them straight to a Shiny data frame fails with "Unsupported
    dataframe type". This picks out the commonly displayed fields into a flat,
    one-level dict so a list of them builds a table (wrap in pandas/polars for
    ``render.data_frame``).

    Args:
        p: A single profile dict as returned by
            [`fetch_profiles`][csiapps.client.fetch_profiles] or
            [`fetch_profile`][csiapps.client.fetch_profile]. Missing nested
            sections are tolerated (treated as empty).

    Returns:
        dict: A flat row with keys ``id``, ``first_name``, ``last_name``,
        ``email``, ``dob``, ``sport_id``, ``sport``, and ``status``. Any absent
        source field is ``None``.

    Example:
        ```python
        import csiapps
        import pandas as pd

        profiles = csiapps.fetch_profiles()
        df = pd.DataFrame(csiapps.flatten_profile(p) for p in profiles)
        ```
    """
    person = p.get("person") or {}
    sport = p.get("sport") or {}
    return {
        "id": p.get("id"),
        "first_name": person.get("first_name"),
        "last_name": person.get("last_name"),
        "email": person.get("email"),
        "dob": person.get("dob"),
        "sport_id": sport.get("id"),
        "sport": sport.get("name"),
        "status": p.get("status"),
    }
