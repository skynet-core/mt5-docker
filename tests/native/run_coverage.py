"""Run native tests and preserve their coverage even when a test fails."""

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path("/source")
BUILD = Path("/native-build")
OUTPUT = Path("/coverage")
POLICY_SPEC = importlib.util.spec_from_file_location(
    "coverage_policy", Path(__file__).resolve().parents[2] / "build/lint.py"
)
POLICY = importlib.util.module_from_spec(POLICY_SPEC)
POLICY_SPEC.loader.exec_module(POLICY)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    # Reusing an output directory must not retain obsolete test-source pages.
    for previous in OUTPUT.glob("native*.html"):
        previous.unlink()
    commands = [
        (
            "ctest",
            [
                "ctest",
                "--test-dir",
                str(BUILD),
                "--output-on-failure",
                "--parallel",
                "2",
            ],
        ),
        (
            "gcovr",
            [
                sys.executable,
                "-m",
                "gcovr",
                "--root",
                str(ROOT),
                "--filter",
                r"(vendor/kde-container|tests/native)/.*",
                "--exclude",
                POLICY.COVERAGE_EXCLUDE,
                "--merge-mode-functions",
                "merge-use-line-min",
                "--json",
                str(OUTPUT / "native.json"),
                "--json-pretty",
                "--xml",
                str(OUTPUT / "native.xml"),
                "--html-details",
                str(OUTPUT / "native.html"),
                "--print-summary",
                str(BUILD),
            ],
        ),
    ]
    failed = False
    for name, command in commands:
        with (OUTPUT / f"{name}.log").open("w") as log:
            try:
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=300,
                )
                failed |= result.returncode != 0
            except (OSError, subprocess.TimeoutExpired) as error:
                log.write(f"{name} failed: {error}\n")
                failed = True
    reports = [OUTPUT / name for name in ("native.json", "native.xml", "native.html")]
    failed |= any(not path.is_file() or path.stat().st_size == 0 for path in reports)
    print(f"Native evidence: {OUTPUT}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
