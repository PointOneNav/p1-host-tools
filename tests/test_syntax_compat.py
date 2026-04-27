"""!
@brief Verify that user scripts compile cleanly on the current Python interpreter.

Note that this only checks that the syntax is valid for the supported Python versions (e.g. match statements, X | Y
union types, walrus operator, etc.). It does not test that the code actually functions as intended.
"""

import glob
import os
import py_compile
import pytest

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))

_SCRIPTS = sorted(
    p for p in (
        glob.glob(os.path.join(_REPO_ROOT, 'bin', '**', '*.py'), recursive=True) +
        glob.glob(os.path.join(_REPO_ROOT, 'p1_runner', '**', '*.py'), recursive=True)
    )
    if '__pycache__' not in p
)

# Parametrize by repo-relative path for readable test IDs.
@pytest.mark.parametrize('path', _SCRIPTS, ids=[os.path.relpath(p, _REPO_ROOT) for p in _SCRIPTS])
def test_compiles(path):
    py_compile.compile(path, doraise=True)
