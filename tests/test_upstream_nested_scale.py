"""Run the unchanged upstream FFI tests against this checkout's vendored helper."""

import importlib.util
import os
from pathlib import Path
from unittest.mock import patch


def load_tests(loader, _tests, _pattern):
    root = Path(__file__).resolve().parents[1]
    source = root / "vendor/kde-container/test/test_nested_scale.py"
    spec = importlib.util.spec_from_file_location("upstream_nested_scale_tests", source)
    module = importlib.util.module_from_spec(spec)
    # Candidate overrides used upstream must not redirect Root coverage.
    with patch.dict(
        os.environ,
        {"QA_SCALE_SOURCE": str(source.parents[1] / "libexec/plasma-remote-output")},
    ):
        spec.loader.exec_module(module)
    return loader.loadTestsFromModule(module)
