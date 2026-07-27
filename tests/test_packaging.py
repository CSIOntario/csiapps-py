"""Import-surface guarantees for existing installs.

Adding Dash support must be invisible to an app that does not use it. These run
in subprocesses because they assert things about a *fresh* interpreter's
sys.modules, which the test session has already polluted.
"""

import importlib.util
import subprocess
import sys
import textwrap

import pytest

import csiapps

PY = sys.executable

requires_dash = pytest.mark.skipif(
    importlib.util.find_spec("dash") is None, reason="csiapps[dash] not installed"
)


def run(code):
    proc = subprocess.run(
        [PY, "-c", textwrap.dedent(code)], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    return proc.stdout.strip()


# ---- the public API is unchanged ---------------------------------------


def test_public_api_is_unchanged():
    # The exact __all__ an existing app may rely on. csiapps.dash is imported as
    # a submodule, deliberately never exported here.
    assert set(csiapps.__all__) == {
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
    }
    for name in csiapps.__all__:
        assert hasattr(csiapps, name), name


def test_private_helpers_relocated_but_still_reachable():
    # app._seed_token_value and app._signed_in_text moved into auth/chrome. The
    # names stay bound in csiapps.app so nothing that reached for them breaks.
    from csiapps import app, auth, chrome

    assert app._seed_token_value is auth.seed_sandbox_token
    assert app._signed_in_text is chrome.signed_in_text
    assert app._FAVICON == chrome.FAVICON
    assert app._logo_src() == chrome.logo_src()


# ---- import purity -----------------------------------------------------


def test_importing_csiapps_does_not_import_dash_or_flask():
    # The regression that would quietly make Dash a hard dependency.
    loaded = run(
        """
        import sys
        import csiapps
        leaked = sorted(m for m in ('dash', 'dash_auth', 'flask', 'werkzeug')
                        if m in sys.modules)
        print(','.join(leaked))
        """
    )
    assert loaded == ""


@requires_dash
def test_importing_csiapps_dash_does_not_break_the_shiny_wrapper():
    # Both wrappers must coexist in one process: someone will import both while
    # migrating an app.
    out = run(
        """
        import csiapps
        from csiapps.dash import attach, layout_wrapper
        csiapps.set_sandbox_mode(True)
        assert 'csi-navbar' in str(csiapps.ui_wrapper())
        assert 'csi-navbar' in str(layout_wrapper()())
        print('ok')
        """
    )
    assert out.endswith("ok")


def test_csiapps_dash_is_not_auto_imported_by_the_package():
    loaded = run(
        """
        import sys, csiapps
        print('csiapps.dash' in sys.modules)
        """
    )
    assert loaded == "False"


# ---- behaviour without the optional extra ------------------------------


def test_missing_extra_raises_a_guided_import_error():
    # Simulates `pip install csiapps` with no [dash]: the failure must name the
    # fix rather than surfacing a bare ModuleNotFoundError from a submodule.
    out = run(
        """
        import sys
        class Blocker:
            def find_module(self, name, path=None):
                return None
            def find_spec(self, name, path=None, target=None):
                if name.split('.')[0] in ('dash', 'dash_auth'):
                    raise ImportError(f"No module named {name!r}")
                return None
        sys.meta_path.insert(0, Blocker())
        try:
            import csiapps.dash
        except ImportError as e:
            assert "pip install 'csiapps[dash]'" in str(e), str(e)
            print('guided')
        else:
            raise AssertionError('expected ImportError')
        """
    )
    assert out.endswith("guided")


def test_shiny_path_is_unaffected_without_flask():
    # The client's Flask probe must be inert when Flask is absent -- this is the
    # exact configuration of every current csiapps deployment.
    out = run(
        """
        import sys
        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split('.')[0] == 'flask':
                    raise ImportError('No module named flask')
                return None
        sys.meta_path.insert(0, Blocker())

        import os
        os.environ['CSIAPPS_ACCESS_TOKEN'] = 'envtok'
        from csiapps import client
        assert client._get_flask_token() is None
        assert client._flask_accessors is False       # probed once, then cached
        assert client.current_token() == 'envtok'
        assert client.token_ready() is True
        print('ok')
        """
    )
    assert out.endswith("ok")


def test_token_resolution_stays_cheap_without_flask():
    # A failed import is not cached by Python and re-walks sys.path on every
    # attempt, so an uncached probe would tax every fetch_* call in every
    # existing Shiny app. Assert the probe cost is amortised, not per-call.
    out = run(
        """
        import sys, timeit
        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split('.')[0] == 'flask':
                    raise ImportError('No module named flask')
                return None
        sys.meta_path.insert(0, Blocker())

        import os
        os.environ['CSIAPPS_ACCESS_TOKEN'] = 'envtok'
        from csiapps import client
        client.current_token()                      # resolve the probe once
        per_call = timeit.timeit(client.current_token, number=20000) / 20000
        # A repeated failed import measured ~38us here; a cached probe is ~1-2us.
        # 10us leaves ample headroom for a slow machine while still failing loudly
        # if the caching is ever removed.
        assert per_call < 10e-6, f'{per_call*1e6:.1f}us per call'
        print('ok')
        """
    )
    assert out.endswith("ok")
