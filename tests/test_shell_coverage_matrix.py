"""Matrix orchestration tests preserve failures/evidence using a fake runtime only."""

import unittest
from pathlib import Path

import test_shell_coverage_runtime as runtime_tests

ROOT = Path(__file__).resolve().parents[1]

DOCKER = r"""record docker "$@"
case $1 in
    create) printf 'fixture\n'; exit "${TEST_CREATE_STATUS:-0}";;
    cp) [[ ${TEST_COPY_STATUS:-0} == 0 ]] || exit "$TEST_COPY_STATUS"; touch "$3";;
    stop) touch "$TEST_ROOT/stopped";;
    inspect)
        if [[ ${2:-} == --format ]]; then
            if [[ ${TEST_HEALTH_DELAY:-0} == 1 && ! -e $TEST_ROOT/health-probed ]]; then touch "$TEST_ROOT/health-probed"; printf 'starting\n'; else printf 'healthy\n'; fi
        elif [[ -e $TEST_ROOT/stopped ]]; then [[ ${TEST_CLEANUP_STUCK:-0} == 1 ]];
        else exit 0; fi;;
    port) printf '127.0.0.1:12345\n';;
esac
"""

UV = r"""record uv "$@"
if [[ $4 == tests/runtime.py ]]; then
    printf 'synthetic display runner\n'
    exit "${TEST_RUNTIME_STATUS:-0}"
elif [[ $4 == tests/display_matrix.py ]]; then
    exit "${TEST_INDEX_STATUS:-0}"
elif [[ $4 == tests/validate.py ]]; then
    previous=; case_name=
    for argument in "$@"; do if [[ $previous == --case ]]; then case_name=$argument; fi; previous=$argument; done
    mkdir -p "$OUTPUT_DIR/$case_name"
    printf '{"passed":false,"reason":"synthetic validation"}\n' > "$OUTPUT_DIR/$case_name/diagnostics.json"
    exit "${TEST_VALIDATE_STATUS:-0}"
elif [[ $4 == tests/capture-vnc.py ]]; then
    exit "${TEST_CAPTURE_STATUS:-0}"
fi
"""


class ShellMatrixTests(unittest.TestCase):
    run_source = runtime_tests.ShellRuntimeTests.run_source

    def run_matrix(
        self, script, *, arguments=(), extra="", environment=None, collision=False
    ):
        def setup(root):
            (root / "tools").mkdir()
            (root / "bin").mkdir()
            for name, body in (("docker", DOCKER), ("uv", UV)):
                path = root / "tools" / name
                path.write_text("#!/bin/bash\n" + body)
                path.chmod(0o755)
            launcher = root / "bin/mt5-run"
            launcher.write_text(
                '#!/bin/bash\nrecord launch "$DISPLAY_BACKEND" "$DISPLAY_MODE"\n'
                'rm -f -- "$TEST_ROOT/stopped" "$TEST_ROOT/health-probed"\n'
                'exit "${TEST_START_STATUS:-0}"\n'
            )
            launcher.chmod(0o755)
            if collision:
                (root / "output/fedora-44").mkdir(parents=True)

        return self.run_source(
            ROOT / "tests" / script,
            relative_name="tests/" + script,
            setup=setup,
            arguments=arguments,
            before=(
                'export PATH="$TEST_ROOT/tools:$PATH"\nexport OUTPUT_DIR="$TEST_ROOT/output"\n'
                "id() { printf 1000; }\n"
                'sleep() { record sleep "$@"; SECONDS=$((SECONDS + ${TEST_SLEEP_ADVANCE:-1})); }\n'
                + extra
            ),
            environment={"MT5_ENV_FILE": "/dev/null", **(environment or {})},
        )

    def test_broker_defaults_cover_every_image_backend_mode_and_version_requirement(
        self,
    ):
        result, commands, _, _ = self.run_matrix("matrix.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        starts = [command for command in commands if command[0] == "launch"]
        self.assertEqual(len(starts), 32)
        self.assertEqual(
            {tuple(command[1:]) for command in starts},
            {
                (backend, mode)
                for backend in ("wayland", "x11")
                for mode in ("headless", "nested", "vnc", "web")
            },
        )
        self.assertTrue(any("--kwin-version" in command for command in commands))
        self.assertTrue(any("--web" in command for command in commands))

    def test_broker_failures_are_not_hidden_and_cleanup_still_runs(self):
        for values, message in (
            ({"TEST_START_STATUS": "1"}, "Broker container failed to start"),
            ({"TEST_VALIDATE_STATUS": "1"}, "synthetic validation"),
            ({"TEST_CAPTURE_STATUS": "1"}, ""),
            (
                {"TEST_CLEANUP_STUCK": "1", "TEST_SLEEP_ADVANCE": "31"},
                "Container cleanup timed out",
            ),
        ):
            with self.subTest(values=values):
                result, commands, _, _ = self.run_matrix(
                    "matrix.sh", arguments=("fedora-44",), environment=values
                )
                self.assertEqual(result.returncode, 1)
                if message:
                    self.assertIn(message, result.stderr)
                self.assertTrue(
                    any(
                        command[:3] == ["docker", "volume", "rm"]
                        for command in commands
                    )
                )

    def test_broker_waits_for_health_before_validation(self):
        result, commands, _, _ = self.run_matrix(
            "matrix.sh",
            arguments=("debian-13",),
            environment={"TEST_HEALTH_DELAY": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(["sleep", "3"], commands)

    def test_display_defaults_run_each_matching_fixture_and_index(self):
        result, commands, _, files = self.run_matrix("display-matrix.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            sum(command[:2] == ["docker", "create"] for command in commands), 4
        )
        self.assertTrue(
            any(
                command[:5]
                == ["uv", "run", "--locked", "python", "tests/display_matrix.py"]
                for command in commands
            )
        )
        for distro in ("fedora-44", "debian-13", "ubuntu-26.04", "archlinux"):
            self.assertIn(f"output/{distro}/runner.log", files)

    def test_display_failure_paths_retain_logs_and_propagate_exit_status(self):
        for values, message in (
            ({"TEST_CREATE_STATUS": "1"}, "Could not export"),
            ({"TEST_COPY_STATUS": "1"}, "Could not export"),
            ({"TEST_RUNTIME_STATUS": "1"}, "synthetic display runner"),
            ({"TEST_INDEX_STATUS": "1"}, ""),
        ):
            with self.subTest(values=values):
                result, _, _, files = self.run_matrix(
                    "display-matrix.sh", arguments=("fedora-44",), environment=values
                )
                self.assertEqual(result.returncode, 1)
                if message:
                    self.assertIn(message, result.stderr)
                self.assertIn("output/fedora-44/runner.log", files)

    def test_display_rejects_unknown_images_ambiguous_overrides_and_existing_evidence(
        self,
    ):
        for arguments, values, collision, message in (
            (("unknown",), {}, False, "Unknown distribution"),
            (
                ("fedora-44", "debian-13"),
                {"IMAGE_TAG": "override"},
                False,
                "Select one distribution",
            ),
            (("fedora-44",), {}, True, "Evidence already exists"),
        ):
            with self.subTest(message=message):
                result, commands, _, _ = self.run_matrix(
                    "display-matrix.sh",
                    arguments=arguments,
                    environment=values,
                    collision=collision,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)
                self.assertEqual(commands, [])


if __name__ == "__main__":
    unittest.main()
