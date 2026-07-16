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
import weakref
from urllib.parse import quote

import httpx

from . import config

# Per-session access token, set by the Shiny app wrapper (server_wrapper) so
# concurrent users never share a token. Keyed on the Shiny session object; read
# back via the active session -- the faithful analog of R's .current_token()
# reading session$userData$csiapps_token, then falling back to the process-wide
# CSIAPPS_ACCESS_TOKEN env var outside any session.
_session_tokens: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()

# Transient statuses worth retrying (httr2 req_retry retried on these).
_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _get_current_session():
    # Indirection so tests can stub the active session without a running app.
    try:
        from shiny.session import get_current_session

        return get_current_session()
    except Exception:
        return None


def set_session_token(session, token) -> None:
    """Store (or clear, when ``token`` is falsy) the access token for a session."""
    if token:
        _session_tokens[session] = token
    else:
        _session_tokens.pop(session, None)


def current_token() -> str:
    """Resolve the access token: per-session first, then ``CSIAPPS_ACCESS_TOKEN``."""
    session = _get_current_session()
    if session is not None:
        tok = _session_tokens.get(session)
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
    """Fetch organisation options as a ``{value: label}`` dict.

    The shape plugs directly into ``ui.input_select(choices=...)`` (Shiny for
    Python maps value -> displayed label). This differs from the R package, which
    returned ``label``/``value`` pairs for R's ``selectInput`` -- that shape
    raises ``TypeError: unhashable type: 'dict'`` in Shiny for Python.
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
        return {it.get("id"): it.get("name") for it in items["results"]}
    if isinstance(items, list):
        return {v: v for v in items}
    return {}


def fetch_profiles(token=None, filters=None, sandbox=None, max_pages=50):
    """Fetch all profiles accessible to the token, auto-paginating.

    ``filters`` is a dict of query parameters (e.g. ``{"sport_org_id": 42}``).
    In sandbox mode only ``sport_org_id`` is honoured.

    Pagination is bounded by ``max_pages`` (default 50, matching
    :func:`make_request`) and terminates if the server ever repeats a ``next``
    URL, so a misbehaving or hostile server can't hang the app in an unbounded
    loop with unbounded memory growth.
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


# fetch_profiles() returns deeply nested dicts; passing them straight to a Shiny
# data frame fails ("Unsupported dataframe type"). flatten_profile turns one
# profile into scalar fields so a list of them builds a table (wrap in
# pandas/polars for render.data_frame).
def flatten_profile(p):
    """Flatten a registration profile into a scalar row for tables/selection."""
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
