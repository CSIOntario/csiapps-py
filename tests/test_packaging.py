"""Import-surface guarantees: the core depends on no web framework.

The refactor's whole point is that `import csiapps` pulls in no web framework,
and that each framework wrapper installs and imports only its own
framework. The subprocess tests assert things about a *fresh* interpreter's
sys.modules, which the test session has already polluted.
"""

import importlib.util
import subprocess
import sys
import textwrap

import pytest

import csiapps

PY = sys.executable

requires_shiny = pytest.mark.skipif(
    importlib.util.find_spec("shiny") is None, reason="csiapps[shiny] not installed"
)
requires_dash = pytest.mark.skipif(
    importlib.util.find_spec("dash") is None, reason="csiapps[dash] not installed"
)
requires_streamlit = pytest.mark.skipif(
    importlib.util.find_spec("streamlit") is None,
    reason="csiapps[streamlit] not installed",
)


def run(code):
    proc = subprocess.run(
        [PY, "-c", textwrap.dedent(code)], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    return proc.stdout.strip()


# ---- the public API is framework-independent ---------------------------


def test_public_api():
    # The exact framework-independent __all__. The web-app wrappers are NOT here:
    # they live in explicit framework submodules, imported
    # explicitly, so importing csiapps costs no web framework.
    assert set(csiapps.__all__) == {
        "__version__",
        "browse_sandbox",
        "check_secrets",
        "clear_sandbox",
        "create_profile",
        "create_sport_org",
        "fetch_ams_mapping",
        "fetch_org_options",
        "fetch_profile",
        "fetch_profiles",
        "flatten_profile",
        "is_sandbox_mode",
        "make_request",
        "register_sandbox_schema",
        "set_institute",
        "set_sandbox_mode",
        "token_ready",
    }
    for name in csiapps.__all__:
        assert hasattr(csiapps, name), name


def test_wrappers_are_not_exported_from_the_package():
    # The clean break: the Shiny wrappers no longer hang off the top-level
    # package; consumers migrate to `from csiapps.shiny import ...`.
    assert not hasattr(csiapps, "ui_wrapper")
    assert not hasattr(csiapps, "server_wrapper")
    assert not hasattr(csiapps, "create_app")
    assert not hasattr(csiapps, "page_wrapper")


# ---- import purity -----------------------------------------------------


def test_importing_csiapps_imports_no_web_framework():
    # The regression that would quietly make any framework a hard dependency.
    loaded = run(
        """
        import sys
        import csiapps
        leaked = sorted(m for m in (
            'shiny', 'dash', 'dash_auth', 'flask', 'werkzeug', 'streamlit', 'starlette'
        )
                        if m in sys.modules)
        print(','.join(leaked))
        """
    )
    assert loaded == ""


def test_importing_csiapps_registers_no_token_adapter():
    # A framework adapter is registered only when its submodule is imported, so a
    # bare `import csiapps` leaves the registry empty and current_token() resolves
    # purely from the environment.
    out = run(
        """
        import csiapps
        from csiapps import client
        print(len(client._token_adapters))
        """
    )
    assert out == "0"


@requires_shiny
@requires_dash
@requires_streamlit
def test_the_three_wrappers_coexist_in_one_process():
    # All frameworks must work in one process: someone will import them while
    # migrating an app, and their adapters must not collide.
    out = run(
        """
        import csiapps
        from csiapps.shiny import ui_wrapper
        from csiapps.dash import attach, layout_wrapper
        import csiapps.streamlit
        from csiapps import client
        csiapps.set_sandbox_mode(True)
        assert 'csi-navbar' in str(ui_wrapper())
        assert 'csi-navbar' in str(layout_wrapper()())
        assert len(client._token_adapters) == 3
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


def test_csiapps_shiny_is_not_auto_imported_by_the_package():
    loaded = run(
        """
        import sys, csiapps
        print('csiapps.shiny' in sys.modules)
        """
    )
    assert loaded == "False"


def test_csiapps_streamlit_is_not_auto_imported_by_the_package():
    loaded = run(
        """
        import sys, csiapps
        print('csiapps.streamlit' in sys.modules)
        """
    )
    assert loaded == "False"


# ---- behaviour without the optional extra ------------------------------


def test_missing_dash_extra_raises_a_guided_import_error():
    # Simulates `pip install csiapps` with no [dash]: the failure must name the
    # fix rather than surfacing a bare ModuleNotFoundError from a submodule.
    out = run(
        """
        import sys
        class Blocker:
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


def test_missing_streamlit_extra_raises_a_guided_import_error():
    out = run(
        """
        import sys
        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split('.')[0] in ('streamlit', 'cryptography'):
                    raise ImportError(f"No module named {name!r}")
                return None
        sys.meta_path.insert(0, Blocker())
        try:
            import csiapps.streamlit
        except ImportError as e:
            assert "pip install 'csiapps[streamlit]'" in str(e), str(e)
            print('guided')
        else:
            raise AssertionError('expected ImportError')
        """
    )
    assert out.endswith("guided")


def test_core_works_with_no_web_framework_installed():
    # The pure-ingestion path: block every framework, and csiapps core still
    # imports and resolves the env token. This is the strict improvement over the
    # old hard Shiny dependency.
    out = run(
        """
        import sys
        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split('.')[0] in (
                    'shiny', 'dash', 'dash_auth', 'flask', 'streamlit', 'cryptography'
                ):
                    raise ImportError(f"No module named {name!r}")
                return None
        sys.meta_path.insert(0, Blocker())

        import os
        os.environ['CSIAPPS_ACCESS_TOKEN'] = 'envtok'
        import csiapps
        from csiapps import client
        assert client._token_adapters == []
        assert client.current_token() == 'envtok'
        assert client.token_ready() is True
        print('ok')
        """
    )
    assert out.endswith("ok")
