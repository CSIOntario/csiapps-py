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
from urllib.parse import quote, urljoin

import httpx
import pandas as pd

from . import config

# ---- token-adapter registry -------------------------------------------
#
# The core resolves the per-context access token through a small registry of
# adapters rather than knowing about any web framework. A framework submodule
# (csiapps.shiny, csiapps.dash) registers an adapter when it is imported; a pure
# ingestion script registers none and falls back to the environment. This is
# what keeps client.py free of any shiny/flask import.
#
# Adapters distinguish inactive contexts (None) from signed-out sessions (""),
# and invalidate only the current session when its token is rejected.
# An adapter implements:
#   read_token() -> str | None
#       The token for the current context if this framework is active, else
#       None (a Shiny adapter returns None with no active session, a Flask
#       adapter returns None outside a request context).
#   invalidate_token(token) -> bool
#       Clear matching credentials; return whether this session was invalidated.
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

    The first active framework adapter owns resolution, even when signed out.
    Only scripts outside every framework context use CSIAPPS_ACCESS_TOKEN.
    """
    for adapter in _token_adapters:
        tok = adapter.read_token()
        if tok is not None:
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


class AuthenticationError(RuntimeError):
    """An API rejected credentials; session_expired identifies a cleared session."""

    def __init__(self, message, *, session_expired=False):
        super().__init__(message)
        self.session_expired = session_expired


def _invalidate_token(token):
    for adapter in _token_adapters:
        if adapter.read_token() is not None:
            return adapter.invalidate_token(token)
    return False


def _positive_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _perform(method, url, *, params=None, json=None, headers=None, timeout=20,
             max_tries=3, is_transient=None):
    delay = 0.5
    for attempt in range(max_tries):
        resp = httpx.request(
            method, url, params=params, json=json, headers=headers, timeout=timeout,
            follow_redirects=True,
        )
        # Authentication failures must reach session invalidation immediately.
        if resp.status_code == 401 or attempt == max_tries - 1:
            return resp
        retry = (resp.status_code in _RETRY_STATUSES if is_transient is None
                 else is_transient(resp))
        if not retry:
            return resp
        time.sleep(min(delay, 10))
        delay *= 2


def _parse_response(resp, *, endpoint="", method="GET", query=None, verbose=False):
    status = resp.status_code
    txt = resp.text
    if status >= 400:
        error = AuthenticationError if status == 401 else RuntimeError
        raise error(f"API request failed ({status}): {txt}")
    if not txt:
        return []
    if verbose:
        print(f"{method} request to {endpoint} returned status {status}")
        if query:
            print("  params:", ", ".join(f"{k}={v}" for k, v in query.items()))
        print("  response:\n", txt)
    try:
        return resp.json()
    except ValueError as e:
        return {"raw": txt, "error": "json_parse_error", "message": str(e)}


def _http_request(
    endpoint, method, body, query, headers, token, timeout, verbose, paginate, max_pages,
    retry_max_tries=3, retry_is_transient=None,
):
    if not token:
        _auth_gate("make_request")
    url = config.site_url().rstrip("/") + "/" + endpoint.lstrip("/")
    req_headers = httpx.Headers({"Authorization": f"Bearer {token}"})
    req_headers.update(headers or {})
    # Compare the credentials actually sent, including a caller's header override.
    bearer = req_headers.get("Authorization", "")
    sent_token = bearer[7:] if bearer.lower().startswith("bearer ") else ""
    pages, seen = [], set()
    params = query
    offset = int(query.get("offset", 0)) if paginate else 0
    while True:
        request_url = str(httpx.Request(method, url, params=params).url)
        if request_url in seen:
            raise RuntimeError("Pagination repeated a page URL before completion.")
        seen.add(request_url)
        resp = _perform(method, url, params=params, json=body, headers=req_headers,
                        timeout=timeout, max_tries=retry_max_tries,
                        is_transient=retry_is_transient)
        try:
            page = _parse_response(resp, endpoint=endpoint, method=method,
                                   query=params, verbose=verbose)
        except AuthenticationError as error:
            error.session_expired = bool(sent_token and _invalidate_token(sent_token))
            raise
        if not paginate:
            return page
        pages.append(page)
        if not isinstance(page, dict):
            raise RuntimeError("Pagination expected a JSON object containing results.")
        results = page.get("results")
        next_url = page.get("next")
        if results == [] and (next_url or (page.get("count") or 0) > offset):
            raise RuntimeError("Pagination ended before all records were returned.")
        if next_url:
            url = urljoin(request_url, next_url)
            # Never forward bearer credentials to another origin through a next link.
            previous, following = httpx.URL(request_url), httpx.URL(url)
            if (previous.scheme, previous.host, previous.port) != (
                following.scheme, following.host, following.port
            ):
                raise RuntimeError("Pagination next link points to another origin.")
            params = None
            offset = int(following.params.get("offset", offset + len(results or [])))
        elif page.get("count") is not None and isinstance(results, list):
            offset += len(results)
            if offset >= page["count"]:
                break
            if not results:
                raise RuntimeError("Pagination ended before all records were returned.")
            # Keep all query parameters, including repeated subject values.
            url = str(httpx.URL(request_url).copy_set_param("offset", offset))
            params = None
        else:
            break
        if len(pages) >= max_pages:
            raise RuntimeError("Pagination exceeds max_pages; increase it to retrieve all records.")
    return pages


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
    *,
    retry_max_tries: int = 3,
    retry_is_transient=None,
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
        query: Query-string parameters as a dict. List values produce repeated
            parameters (e.g. ``{"subject": [1, 2]}``). ``None`` means ``{}``.
        headers: Extra request headers merged over the ``Authorization`` header.
            Ignored in sandbox mode.
        token: Bearer token to authenticate with. When omitted it is resolved
            from the active session, or from ``CSIAPPS_ACCESS_TOKEN`` only
            outside a browser session. Explicit tokens remain supported.
        timeout: Per-request timeout in seconds. Defaults to ``20``.
        verbose: If ``True``, print the method, endpoint, params, and raw
            response body for debugging. Defaults to ``False``.
        paginate: Follow ``next`` links, or count/offset when links are absent
            or null. Returns a list of pages. Defaults to ``False``.
        max_pages: Upper bound on pages fetched when ``paginate`` is ``True``,
            raising an error if more results remain. Defaults to ``50``.
        retry_max_tries: Attempts per page; defaults to 3. Use 1 for writes
            that must not be retried automatically.
        retry_is_transient: Optional predicate accepting an httpx.Response.
            None preserves the default status policy. A 401 is never retried.
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
    _positive_integer(retry_max_tries, "retry_max_tries")
    if paginate:
        _positive_integer(max_pages, "max_pages")
    if retry_is_transient is not None and not callable(retry_is_transient):
        raise ValueError("retry_is_transient must be callable or None")
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
        endpoint, method, body, query, headers, token, timeout, verbose, paginate, max_pages,
        retry_max_tries, retry_is_transient,
    )


def fetch_ams_mapping(
    source_uuid: str | None = None,
    token: str | None = None,
    sandbox: bool | None = None,
    max_pages: int = 50,
) -> pd.DataFrame:
    """Fetch the current four-column AMS athlete mapping.

    Sandbox sources contain the already-current mapping, so their four core
    fields are returned directly. Production sources are append-only: this
    helper selects the latest record for each mapping identity and drops an
    identity when its latest record is inactive.

    Args:
        source_uuid: AMS mapping data-source identifier. When omitted, read
            ``AMS_MAPPING_UUID`` from the environment.
        token: Bearer token passed to :func:`make_request`. Ignored in sandbox
            mode.
        sandbox: Force sandbox (``True``) or production (``False``) routing.
            ``None`` resolves via :func:`csiapps.config.is_sandbox_mode`.
        max_pages: Maximum number of production response pages to fetch.

    Returns:
        pandas.DataFrame: The current mapping with columns ``id``, ``vendor``,
        ``vendor_profile_id``, and ``vendor_profile_name``.

    Raises:
        ValueError: If no source UUID is supplied or a record is missing a core
            mapping field.
        RuntimeError: If the production request fails.
    """
    if source_uuid is None:
        source_uuid = os.environ.get("AMS_MAPPING_UUID", "")
    if not isinstance(source_uuid, str) or not source_uuid.strip():
        raise ValueError("fetch_ams_mapping: pass source_uuid or set AMS_MAPPING_UUID.")
    source_uuid = source_uuid.strip()
    if sandbox is None:
        sandbox = config.is_sandbox_mode()

    columns = ["id", "vendor", "vendor_profile_id", "vendor_profile_name"]
    pages = make_request(
        "api/warehouse/data-records",
        query={"source_uuid": source_uuid},
        token=token,
        paginate=True,
        max_pages=max_pages,
        sandbox=sandbox,
    )
    records = [record for page in pages for record in (page.get("results") or [])]

    rows = []
    for position, record in enumerate(records):
        data = record.get("data") if isinstance(record, dict) else None
        missing = [
            field
            for field in columns
            if not isinstance(data, dict)
            or field not in data
            or data[field] is None
            or isinstance(data[field], (dict, list))
        ]
        if missing:
            raise ValueError(
                f"fetch_ams_mapping: record {position + 1} is missing core field(s): "
                + ", ".join(missing)
                + "."
            )

        row = {
            "id": data["id"],
            "vendor": str(data["vendor"]),
            "vendor_profile_id": str(data["vendor_profile_id"]),
            "vendor_profile_name": str(data["vendor_profile_name"]),
        }
        if not sandbox:
            active = data.get("active")
            if active is None or (isinstance(active, str) and active.strip().upper() in {"", "NA"}):
                active = True
            elif isinstance(active, bool):
                pass
            else:
                active = str(active).strip().upper() not in {"FALSE", "F", "0", "NO", "N"}
            record_id = record.get("id")
            try:
                numeric_id = float(record_id)
                id_kind = 1
            except (TypeError, ValueError):
                numeric_id = float("-inf")
                id_kind = 0
            row.update(
                _active=active,
                _order=(
                    str(record.get("updated_at") or ""),
                    id_kind,
                    numeric_id,
                    str(record_id or ""),
                    position,
                ),
            )
        rows.append(row)

    if sandbox or not rows:
        return pd.DataFrame(rows, columns=columns)

    ordered = sorted(rows, key=lambda row: row["_order"])
    latest = {}
    for index, row in enumerate(ordered):
        latest[tuple(row[field] for field in columns)] = index
    current = [
        {field: row[field] for field in columns}
        for index, row in enumerate(ordered)
        if latest[tuple(row[field] for field in columns)] == index and row["_active"]
    ]
    return pd.DataFrame(current, columns=columns)


def fetch_org_options(token: str | None = None, sandbox: bool | None = None) -> dict:
    """Fetch sport-organisation options as a ``{value: label}`` dict.

    The returned mapping plugs directly into a Shiny select input's ``choices=``
    argument, which maps each value to its displayed label.

    Args:
        token: Bearer token to authenticate with. When omitted it is resolved
            from the active session; outside a session, from ``CSIAPPS_ACCESS_TOKEN``.
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

    items = make_request(config.SPORT_ORG_ENDPOINT, token=token,
                         query={"limit": 1000}, headers={"Accept": "application/json"},
                         sandbox=False)
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
            from the active session; outside a session, from ``CSIAPPS_ACCESS_TOKEN``.
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

    Incomplete results (a page limit, repeated URL, or premature empty page)
    raise RuntimeError; partial profiles are never returned as a success.

    Example:
        ```python
        import csiapps

        csiapps.set_sandbox_mode(True)
        profiles = csiapps.fetch_profiles(filters={"sport_org_id": 7})
        rows = [csiapps.flatten_profile(p) for p in profiles]
        ```

    Note:
        Pagination raises if the server ever repeats a ``next`` URL, so a
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

    pages = make_request(config.PROFILE_ENDPOINT, token=token,
                         query={**filters, "limit": 100, "offset": 0},
                         headers={"Accept": "application/json"}, paginate=True,
                         max_pages=max_pages, sandbox=False)
    return [profile for page in pages for profile in (page.get("results") or [])]


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
            from the active session; outside a session, from ``CSIAPPS_ACCESS_TOKEN``.
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

    enc_id = quote(str(profile_id), safe="")
    return make_request(config.PROFILE_ENDPOINT + enc_id + "/", token=token,
                        headers={"Accept": "application/json"}, sandbox=False)


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
