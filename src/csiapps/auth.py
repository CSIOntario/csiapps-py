"""OAuth2 PKCE helpers, token exchange, and secret checks.

Ports the PKCE / token-exchange section of ``R/utils.R`` and ``check_secrets()``.
The R package leaned on ``httr2``/``openssl`` for these; in Python the PKCE
pieces are pure stdlib (``secrets`` + ``hashlib`` + ``base64``) and only the
token POST needs ``httpx``.
"""

import base64
import hashlib
import json
import os
import re
import secrets

import httpx

from . import config
from .config import _message

# ---- PKCE base64url ----


def _b64url(raw: bytes) -> str:
    """base64url-encode bytes with padding stripped (matches R's pkce_base64url)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = -len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * pad)


def generate_pkce() -> dict:
    """Generate a PKCE verifier/challenge pair.

    Replaces ``httr2::oauth_flow_auth_code_pkce()``, which R's ``server_wrapper``
    called inline. Returns ``{"verifier", "challenge", "method"}``.
    """
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return {"verifier": verifier, "challenge": challenge, "method": "S256"}


def pkce_state_encode(verifier: str) -> str:
    """Encode a PKCE verifier into the opaque ``state`` string for the auth request."""
    payload = json.dumps(
        {"v": verifier, "r": secrets.randbelow(10**9) + 1},
        separators=(",", ":"),
    )
    return _b64url(payload.encode("utf-8"))


def pkce_state_decode(state: str) -> dict:
    """Reverse :func:`pkce_state_encode`; returns the decoded ``{"v", "r"}`` dict."""
    return json.loads(_b64url_decode(state).decode("utf-8"))


# ---- Token exchange ----


def exchange_code_for_token(code: str, code_verifier: str | None = None) -> dict:
    """Exchange an authorization code for tokens at the CSIAPPS token endpoint.

    HTTP error statuses do **not** raise (they are returned as an ``error`` dict,
    mirroring the R ``req_error(is_error = FALSE)`` behaviour); transport-level
    failures propagate as ``httpx`` exceptions, as they did in R.
    """
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": os.environ.get("CSIAPPS_REDIRECT_URI", ""),
        "code_verifier": code_verifier,
    }
    # Drop unset fields (httr2's req_body_form omits NULLs).
    form = {k: v for k, v in form.items() if v is not None}

    resp = httpx.post(
        config.token_url(),
        auth=(
            os.environ.get("CSIAPPS_CLIENT_ID", ""),
            os.environ.get("CSIAPPS_CLIENT_SECRET", ""),
        ),
        data=form,
    )

    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}

    if 200 <= resp.status_code < 300:
        return body
    return {
        "error": "token_exchange_http_error",
        "status": resp.status_code,
        "payload": body,
    }


# ---- Secret / env checks ----


def check_secrets(verbose: bool = False, sandbox: bool | None = None) -> bool:
    """Check that the environment is configured for authentication.

    In sandbox mode the OAuth secret checks are skipped (sandbox simulates the
    login and needs no client credentials); instead the presence of
    ``CSIAPPS_ACCESS_TOKEN`` is reported. Never raises in sandbox mode. Outside
    sandbox, raises :class:`ValueError` if any required URL is missing/malformed.
    """
    if sandbox is None:
        sandbox = config.is_sandbox_mode()

    if sandbox:
        if os.environ.get("CSIAPPS_ACCESS_TOKEN", ""):
            _message(
                "csiapps sandbox: CSIAPPS_ACCESS_TOKEN found - real registration reads enabled"
            )
        else:
            _message(
                "csiapps sandbox: no CSIAPPS_ACCESS_TOKEN set - running unauthenticated "
                "(set a token to emulate login and load /me)"
            )
        return True

    url_re = re.compile(r"^https?://")
    bad = []
    if not url_re.match(config.auth_url()):
        bad.append("CSIAPPS_AUTH_URL")
    if not url_re.match(config.token_url()):
        bad.append("CSIAPPS_TOKEN_URL")
    if not url_re.match(os.environ.get("CSIAPPS_REDIRECT_URI", "")):
        bad.append("CSIAPPS_REDIRECT_URI")
    if bad:
        raise ValueError("Invalid or missing URL env vars: " + ", ".join(bad))

    if verbose:
        _message(
            f"AUTH_URL: '{config.auth_url()}'  "
            f"REDIRECT_URI: '{os.environ.get('CSIAPPS_REDIRECT_URI', '')}'"
        )
        env_dump = {
            "CSIAPPS_CLIENT_ID": os.environ.get("CSIAPPS_CLIENT_ID", ""),
            "CSIAPPS_CLIENT_SECRET_SET": bool(os.environ.get("CSIAPPS_CLIENT_SECRET", "")),
            "CSIAPPS_AUTH_URL": config.auth_url(),
            "CSIAPPS_TOKEN_URL": config.token_url(),
            "CSIAPPS_REDIRECT_URI": os.environ.get("CSIAPPS_REDIRECT_URI", ""),
            "CSIAPPS_SCOPE": os.environ.get("CSIAPPS_SCOPE", "read write"),
            "CSIAPPS_USERINFO_URL": config.userinfo_url(),
        }
        _message("CSIAPPS environment on startup:")
        _message(json.dumps(env_dump, indent=2))

    return True
