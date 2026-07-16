"""Phase 1 smoke test: the package imports and reports a version.

Real behaviour tests are ported from the R package's testthat suite as each
module lands (see PORTING_PLAN.md).
"""

import csiapps


def test_package_imports_with_version():
    assert isinstance(csiapps.__version__, str)
    assert csiapps.__version__.count(".") >= 2
