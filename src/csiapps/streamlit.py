"""Native Streamlit authentication and shared CSI page chrome.

The Streamlit runtime owns the HTTP server on Posit Connect Cloud, so this
adapter performs OAuth from the dashboard script itself.  PKCE data is carried
through the redirect in short-lived AES-GCM encrypted state; the access token
is kept only in ``st.session_state`` and is never exposed to browser code or a
cookie.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import html as html_lib
import json
import os
import secrets
import time
import warnings
from collections.abc import Callable
from urllib.parse import urlencode, urlsplit

from . import auth, chrome, client, config

try:
    import httpx
    import streamlit as st
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError as exc:  # pragma: no cover - exercised by no-extra CI
    raise ImportError(
        "csiapps.streamlit requires the optional Streamlit dependencies, which "
        "are not installed. Install them with:\n\n"
        "    pip install 'csiapps[streamlit]'\n\n"
        f"(missing: {exc.name})"
    ) from exc


COOKIE_NAME = "csiapps_oauth_nonce"
SESSION_KEY = "_csiapps_streamlit_session"
LOGOUT_PARAM = "csiapps_logout"
AUTH_TTL_SECONDS = 10 * 60
MIN_SECRET_KEY_BYTES = 32
_STATE_AAD = b"csiapps.streamlit.oauth-state.v1"
_AUTH_QUERY_KEYS = {"code", "state", "error", "error_description", LOGOUT_PARAM}


def _streamlit_session() -> dict | None:
    """Return a valid authenticated session, removing an expired one."""
    try:
        session = st.session_state.get(SESSION_KEY)
    except Exception:
        return None
    if not isinstance(session, dict) or not session.get("access_token"):
        return None
    expires_at = session.get("expires_at")
    if isinstance(expires_at, (int, float)) and time.time() >= expires_at:
        st.session_state.pop(SESSION_KEY, None)
        return None
    return session


class _StreamlitTokenAdapter:
    """Resolve the token for the current Streamlit WebSocket session."""

    def read_token(self):
        session = _streamlit_session()
        return session.get("access_token") if session else None

    def handle_missing_token(self) -> bool:
        # page_wrapper stops the script before protected app logic can run.
        return False


client.register_token_adapter(_StreamlitTokenAdapter())


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _cipher(secret_key: str) -> AESGCM:
    key = hashlib.sha256(b"csiapps.streamlit:" + secret_key.encode()).digest()
    return AESGCM(key)


def _encrypt_state(
    secret_key: str,
    *,
    verifier: str,
    browser_nonce: str,
    redirect_uri: str,
    return_query: dict[str, str],
    now: float | None = None,
) -> str:
    """Seal the PKCE transaction so it can survive Streamlit navigation."""
    payload = json.dumps(
        {
            "v": verifier,
            "b": browser_nonce,
            "r": redirect_uri,
            "q": return_query,
            "iat": int(time.time() if now is None else now),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    nonce = secrets.token_bytes(12)
    return _b64encode(nonce + _cipher(secret_key).encrypt(nonce, payload, _STATE_AAD))


def _decrypt_state(
    value: str,
    secret_key: str,
    *,
    browser_nonce: str,
    redirect_uri: str,
    now: float | None = None,
) -> dict:
    """Open and validate a browser-bound OAuth transaction."""
    message = "Invalid or expired CSIAPPS authentication response."
    try:
        raw = _b64decode(value)
        payload = json.loads(
            _cipher(secret_key).decrypt(raw[:12], raw[12:], _STATE_AAD)
        )
        issued_at = payload["iat"]
        age = (time.time() if now is None else now) - issued_at
        valid = (
            isinstance(payload, dict)
            and isinstance(payload.get("v"), str)
            and isinstance(payload.get("b"), str)
            and isinstance(payload.get("q"), dict)
            and isinstance(issued_at, int)
            and -30 <= age <= AUTH_TTL_SECONDS
            and payload.get("r") == redirect_uri
            and secrets.compare_digest(payload["b"], browser_nonce)
        )
        if not valid:
            raise ValueError(message)
        return payload
    except (
        InvalidTag,
        KeyError,
        TypeError,
        ValueError,
        binascii.Error,
    ) as exc:
        raise ValueError(message) from exc


def _require_production_config() -> tuple[str, str, bool, str]:
    auth.check_secrets(sandbox=False)
    secret_key = os.environ.get("CSIAPPS_SECRET_KEY", "")
    if len(secret_key.encode()) < MIN_SECRET_KEY_BYTES:
        raise ValueError(
            "csiapps.streamlit.page_wrapper: CSIAPPS_SECRET_KEY must be at least "
            f"{MIN_SECRET_KEY_BYTES} bytes in production. It must be stable across "
            "restarts and Connect Cloud replicas."
        )

    redirect_uri = os.environ.get("CSIAPPS_REDIRECT_URI", "")
    parsed = urlsplit(redirect_uri)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or any(char in parsed.path for char in ";\r\n")
    ):
        raise ValueError(
            "csiapps.streamlit.page_wrapper: CSIAPPS_REDIRECT_URI must be the "
            "absolute public app URL without credentials, query, or fragment."
        )
    secure = parsed.scheme == "https"
    if not secure and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(
            "csiapps.streamlit.page_wrapper: production redirects must use HTTPS; "
            "plain HTTP is allowed only for localhost development."
        )

    current_url = _context_url()
    if current_url and _normalized_url(current_url) != _normalized_url(redirect_uri):
        raise ValueError(
            "csiapps.streamlit.page_wrapper: CSIAPPS_REDIRECT_URI must exactly "
            f"match this app's public URL ({_normalized_url(current_url)!r})."
        )
    cookie_path = parsed.path.rstrip("/") or "/"
    return secret_key, redirect_uri, secure, cookie_path


def _context_url() -> str | None:
    try:
        return st.context.url or None
    except Exception:
        return None


def _normalized_url(value: str) -> str:
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def _query_dict() -> dict[str, str]:
    try:
        values = st.query_params.to_dict()
    except Exception:
        values = dict(st.query_params)
    return {str(key): str(value) for key, value in values.items()}


def _set_query(values: dict[str, str]) -> None:
    st.query_params.from_dict(values)


def _without_auth_query(values: dict[str, str] | None = None) -> dict[str, str]:
    source = _query_dict() if values is None else values
    return {key: value for key, value in source.items() if key not in _AUTH_QUERY_KEYS}


def _browser_cookie() -> str:
    try:
        return st.context.cookies.get(COOKIE_NAME, "")
    except Exception:
        return ""


def _userinfo(access_token: str) -> dict | None:
    try:
        response = httpx.get(
            config.userinfo_url(),
            headers={"Authorization": f"Bearer {access_token}"},
            follow_redirects=True,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "first_name": payload.get("first_name"),
            "last_name": payload.get("last_name"),
        }
    except Exception:
        return None


def _login_transaction(
    secret_key: str, redirect_uri: str
) -> tuple[str, str]:
    pkce = auth.generate_pkce()
    browser_nonce = secrets.token_urlsafe(32)
    state = _encrypt_state(
        secret_key,
        verifier=pkce["verifier"],
        browser_nonce=browser_nonce,
        redirect_uri=redirect_uri,
        return_query=_without_auth_query(),
    )
    params = {
        "response_type": "code",
        "client_id": os.environ.get("CSIAPPS_CLIENT_ID", ""),
        "redirect_uri": redirect_uri,
        "scope": os.environ.get("CSIAPPS_SCOPE", "read write"),
        "code_challenge": pkce["challenge"],
        "code_challenge_method": pkce["method"],
        "state": state,
    }
    return config.auth_url() + "?" + urlencode(params), browser_nonce


def _js_string(value: str) -> str:
    # JSON quoting plus HTML-significant escapes prevents a value from ending
    # the trusted inline script element.
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _login_html(
    login_url: str, browser_nonce: str, cookie_path: str, secure: bool
) -> str:
    cookie = (
        f"{COOKIE_NAME}={browser_nonce}; Max-Age={AUTH_TTL_SECONDS}; "
        f"Path={cookie_path}; SameSite=Lax" + ("; Secure" if secure else "")
    )
    return f"""
<style>
#csiapps-login {{
  background:#d81f26; border:0; border-radius:4px; color:white;
  cursor:pointer; font-weight:600; padding:10px 18px;
}}
</style>
<button id="csiapps-login" type="button">Sign in with CSI</button>
<script>
document.getElementById("csiapps-login").addEventListener("click", () => {{
  document.cookie = {_js_string(cookie)};
  window.location.assign({_js_string(login_url)});
}});
</script>
"""


def _finish_login(secret_key: str, redirect_uri: str) -> str | None:
    query = _query_dict()
    if query.get("error"):
        _set_query(_without_auth_query(query))
        return "CSIAPPS authentication was denied."
    if not ({"code", "state"} & query.keys()):
        return None
    if not query.get("code") or not query.get("state") or not _browser_cookie():
        _set_query(_without_auth_query(query))
        return "Invalid or expired CSIAPPS authentication response."

    try:
        transaction = _decrypt_state(
            query["state"],
            secret_key,
            browser_nonce=_browser_cookie(),
            redirect_uri=redirect_uri,
        )
    except ValueError:
        _set_query(_without_auth_query(query))
        return "Invalid or expired CSIAPPS authentication response."
    try:
        token = auth.exchange_code_for_token(query["code"], transaction["v"])
    except Exception:
        _set_query(_without_auth_query(query))
        return "CSIAPPS token exchange failed. Please try signing in again."
    access_token = token.get("access_token") if isinstance(token, dict) else None
    if not access_token:
        _set_query(_without_auth_query(query))
        return "CSIAPPS token exchange failed. Please try signing in again."

    session = {"access_token": access_token, "user": _userinfo(access_token)}
    expires_in = token.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        session["expires_at"] = time.time() + expires_in
    st.session_state[SESSION_KEY] = session
    _set_query(
        {
            str(key): str(value)
            for key, value in transaction["q"].items()
            if key not in _AUTH_QUERY_KEYS
        }
    )
    st.rerun()
    return None


def _authenticate() -> dict:
    secret_key, redirect_uri, secure, cookie_path = _require_production_config()
    query = _query_dict()
    if LOGOUT_PARAM in query:
        st.session_state.pop(SESSION_KEY, None)
        _set_query(_without_auth_query(query))

    session = _streamlit_session()
    if session:
        return session

    error = _finish_login(secret_key, redirect_uri)
    if error:
        st.error(error)
    login_url, browser_nonce = _login_transaction(secret_key, redirect_uri)
    st.title("Sign in")
    st.write("Sign in with your CSI account to continue.")
    st.html(
        _login_html(login_url, browser_nonce, cookie_path, secure),
        unsafe_allow_javascript=True,
    )
    st.stop()
    raise RuntimeError("unreachable")  # pragma: no cover


def _warn_if_multiworker() -> None:
    raw = os.environ.get("WEB_CONCURRENCY", "")
    try:
        workers = int(raw)
    except ValueError:
        return
    if workers > 1:
        warnings.warn(
            f"csiapps.streamlit: sandbox mode with WEB_CONCURRENCY={workers}. "
            "The sandbox registry is per-process, so use one worker.",
            stacklevel=3,
        )


_STREAMLIT_CSS = f"""
<style>
{chrome.CHROME_CSS}
#csi-navbar .csiapps-nav-inner {{
  display:flex; align-items:center; justify-content:space-between;
  min-height:64px; padding:6px 16px;
}}
#csi-navbar .csiapps-brand img {{
  height:{chrome.LOGO_HEIGHT}; width:auto; max-width:none;
}}
#csi-navbar .csiapps-auth {{
  display:flex; align-items:center; gap:12px; font-size:14px;
}}
#csi-navbar .csiapps-logout {{ color:#b91c1c; text-decoration:none; }}
#footer .csiapps-footer-inner {{
  display:flex; align-items:center; min-height:52px; padding:0 16px;
}}
.stMainBlockContainer {{ padding-bottom:90px; }}
</style>
"""


def _chrome_html(sandbox: bool, session: dict | None) -> str:
    if sandbox and not auth.seed_sandbox_token().get("access_token"):
        status = chrome.UNAUTHENTICATED_TEXT
    else:
        user = session.get("user") if session else None
        status = chrome.signed_in_text(user, sandbox)
    logo = html_lib.escape(chrome.logo_src(), quote=True)
    logout = ""
    if not sandbox:
        logout = (
            f'<a class="csiapps-logout" href="?{LOGOUT_PARAM}=1" '
            'target="_self">Log out</a>'
        )
    banner = ""
    if sandbox:
        banner = (
            f'<div class="{chrome.SANDBOX_BANNER_CLASS}" '
            f'style="{chrome.SANDBOX_BANNER_STYLE}">'
            f"{html_lib.escape(chrome.SANDBOX_BANNER_TEXT)}</div>"
        )
    return f"""
{_STREAMLIT_CSS}
<nav id="csi-navbar">
  <div class="csiapps-nav-inner">
    <a class="csiapps-brand" href="#"><img src="{logo}" alt="CSI logo"></a>
    <div class="csiapps-auth"><span>{html_lib.escape(status)}</span>{logout}</div>
  </div>
</nav>
{banner}
"""


def _footer_html() -> str:
    return f"""
<footer id="footer">
  <div class="csiapps-footer-inner"><p>{html_lib.escape(chrome.footer_text())}</p></div>
</footer>
"""


def page_wrapper(
    app_specific_logic: Callable[[], None], sandbox: bool | None = None
) -> None:
    """Authenticate, then render CSI chrome around a Streamlit page function.

    Keep all protected reads and UI inside ``app_specific_logic``. Production
    mode stops the script before that callable runs until OAuth completes.
    """
    if not callable(app_specific_logic):
        raise TypeError("app_specific_logic must be callable")
    resolved = config.is_sandbox_mode() if sandbox is None else sandbox
    st.set_page_config(page_icon=chrome.FAVICON)
    if resolved:
        _warn_if_multiworker()
        session = None
    else:
        session = _authenticate()
    st.html(_chrome_html(resolved, session))
    app_specific_logic()
    st.html(_footer_html())


__all__ = ["page_wrapper"]
