"""Provisioning contracts use private trees and never install packages on the host."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_shell_coverage_runtime as runtime_tests
from shell_coverage import copy_shell

ROOT = Path(__file__).resolve().parents[1]


class ShellBuildTests(unittest.TestCase):
    run_source = runtime_tests.ShellRuntimeTests.run_source

    def test_package_managers_receive_exact_filtered_lists_and_no_weak_dependencies(
        self,
    ):
        def setup(root):
            packages = root / "support/packages"
            packages.mkdir(parents=True)
            for phase in ("desktop", "wine"):
                (packages / f"{phase}.txt").write_text(
                    "# comment\n\npackage-one\npackage-two\n"
                )

        for distro, phase, expected in (
            (
                "fedora",
                "desktop",
                [
                    "dnf",
                    "install",
                    "-y",
                    "--setopt=install_weak_deps=False",
                    "package-one",
                    "package-two",
                ],
            ),
            (
                "debian",
                "wine",
                [
                    "apt-get",
                    "install",
                    "-y",
                    "--no-install-recommends",
                    "package-one",
                    "package-two",
                ],
            ),
            (
                "ubuntu",
                "desktop",
                [
                    "apt-get",
                    "install",
                    "-y",
                    "--no-install-recommends",
                    "package-one",
                    "package-two",
                ],
            ),
            (
                "arch",
                "desktop",
                [
                    "pacman",
                    "-Syu",
                    "--noconfirm",
                    "--needed",
                    "package-one",
                    "package-two",
                ],
            ),
            (
                "arch",
                "wine",
                [
                    "pacman",
                    "-Syu",
                    "--noconfirm",
                    "--needed",
                    "package-one",
                    "package-two",
                ],
            ),
        ):
            with self.subTest(distro=distro, phase=phase):
                result, commands, _, _ = self.run_source(
                    ROOT / "build/install-packages",
                    arguments=(phase,),
                    setup=lambda root, distro=distro: (
                        setup(root),
                        (root / "os-release").write_text(f"ID={distro}\n"),
                    ),
                    replacements=(
                        ("/etc/os-release", "os-release"),
                        ("/build-support", "support"),
                    ),
                    before=(
                        'dnf() { record dnf "$@"; }\n'
                        'apt-get() { record apt-get "$@"; }\n'
                        'dpkg() { record dpkg "$@"; }\n'
                        'pacman() { record pacman "$@"; }\n'
                        'uv() { record uv "$@"; }\n'
                        'ln() { record ln "$@"; }\n'
                        'rm() { record rm "$@"; }\n'
                        'command() { if [[ $* == "-v wineserver" ]]; then return 0; else builtin command "$@"; fi; }\n'
                    ),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, commands)
                if distro in ("debian", "ubuntu"):
                    self.assertIn(["apt-get", "update"], commands)
                    self.assertEqual(
                        ["dpkg", "--add-architecture", "i386"] in commands,
                        phase == "wine",
                    )
                if distro == "arch":
                    self.assertIn(["pacman", "-Scc", "--noconfirm"], commands)
                    self.assertEqual(
                        any(command[0] == "uv" for command in commands),
                        phase == "desktop",
                    )

    def test_unsupported_distribution_aborts_without_running_any_package_manager(self):
        def setup(root):
            (root / "os-release").write_text("ID=unsupported\n")
            (root / "support/packages").mkdir(parents=True)
            (root / "support/packages/desktop.txt").write_text("unused\n")

        result, commands, _, _ = self.run_source(
            ROOT / "build/install-packages",
            arguments=("desktop",),
            setup=setup,
            replacements=(
                ("/etc/os-release", "os-release"),
                ("/build-support", "support"),
            ),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unsupported distribution", result.stderr)
        self.assertEqual(commands, [])

    def test_missing_wineserver_is_repaired_from_a_private_candidate(self):
        def setup(root):
            (root / "os-release").write_text("ID=ubuntu\n")
            (root / "support/packages").mkdir(parents=True)
            (root / "support/packages/wine.txt").write_text("wine\n")
            candidate = root / "candidate"
            candidate.touch(mode=0o755)

        result, commands, _, _ = self.run_source(
            ROOT / "build/install-packages",
            arguments=("wine",),
            setup=setup,
            replacements=(
                ("/etc/os-release", "os-release"),
                ("/build-support", "support"),
                ("/usr/bin/wineserver-stable", "candidate"),
            ),
            before=(
                'apt-get() { record apt-get "$@"; }\n'
                'dpkg() { record dpkg "$@"; }\n'
                'rm() { record rm "$@"; }\n'
                'ln() { record ln "$@"; touch "$TEST_ROOT/wineserver-found"; }\n'
                'command() { if [[ $* == "-v wineserver" ]]; then [[ -e $TEST_ROOT/wineserver-found ]]; else builtin command "$@"; fi; }\n'
            ),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        links = [command for command in commands if command[0] == "ln"]
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0][2].endswith("/candidate"))
        self.assertEqual(links[0][-1], "/usr/local/bin/wineserver")

    def test_desktop_user_creation_preserves_ubuntu_home_and_rejects_conflicts(self):
        for group, owner in (
            ("", ""),
            ("desktop", ""),
            ("desktop", "ubuntu"),
            ("desktop", "somebody"),
        ):
            with self.subTest(group=group, owner=owner):
                result, commands, _, _ = self.run_source(
                    ROOT / "build/create-user",
                    environment={
                        "TEST_GROUP": group,
                        "TEST_OWNER": owner,
                        "PLASMA_UID": "2000",
                        "PLASMA_GID": "3000",
                    },
                    before=(
                        'getent() { local value; if [[ $1 == group ]]; then value=$TEST_GROUP; else value=$TEST_OWNER; fi; [[ -n $value ]] || return 2; printf "%s:x:2000:3000::/home/test:/bin/bash\\n" "$value"; }\n'
                        'groupadd() { record groupadd "$@"; }\n'
                        'usermod() { record usermod "$@"; }\n'
                        'useradd() { record useradd "$@"; }\n'
                        'chown() { record chown "$@"; }\n'
                        'install() { record install "$@"; }\n'
                    ),
                )
                if owner == "somebody":
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("already belongs to somebody", result.stderr)
                    self.assertFalse(
                        any(
                            command[0] in ("usermod", "useradd", "chown")
                            for command in commands
                        )
                    )
                    continue
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    any(command[0] == "groupadd" for command in commands), not group
                )
                if owner == "ubuntu":
                    self.assertIn(
                        [
                            "usermod",
                            "--login",
                            "plasma",
                            "--home",
                            "/home/plasma",
                            "--move-home",
                            "ubuntu",
                        ],
                        commands,
                    )
                else:
                    created = next(
                        command for command in commands if command[0] == "useradd"
                    )
                    self.assertIn("2000", created)
                    self.assertEqual(
                        created[created.index("--gid") + 1], group or "plasma"
                    )

    def test_runtime_normalization_preserves_existing_alias_and_uses_available_qt_tool(
        self,
    ):
        for available in ("qdbus-qt6", "qdbus6", "/usr/lib64/qt6/bin/qdbus", "none"):
            with self.subTest(available=available):
                result, commands, _, _ = self.run_source(
                    ROOT / "build/normalize-runtime",
                    environment={"TEST_QDBUS": available},
                    before=(
                        'command() { record lookup "$@"; [[ ${2:-} == "$TEST_QDBUS" ]] || return 1; printf "%s\\n" "$TEST_QDBUS"; }\n'
                        'ln() { record ln "$@"; }\n'
                    ),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                links = [command for command in commands if command[0] == "ln"]
                self.assertEqual(bool(links), available not in ("none", "qdbus-qt6"))
                if links:
                    self.assertEqual(links[0][-1], "/usr/local/bin/qdbus-qt6")

    def test_winehq_targets_write_signed_repository_metadata_after_key_verification(
        self,
    ):
        def setup(root, distro, codename):
            (root / "os-release").write_text(
                f"ID={distro}\nVERSION_CODENAME={codename}\n"
            )
            (root / "apt/sources.list.d").mkdir(parents=True)

        for distro, codename, expected_arch in (
            ("debian", "trixie", "amd64 i386"),
            ("ubuntu", "resolute", "amd64"),
            ("debian", "unknown", None),
        ):
            with self.subTest(distro=distro, codename=codename):
                result, commands, _, files = self.run_source(
                    ROOT / "build/install-winehq",
                    setup=lambda root, distro=distro, codename=codename: setup(
                        root, distro, codename
                    ),
                    replacements=(
                        ("/etc/os-release", "os-release"),
                        ("/etc/apt", "apt"),
                    ),
                    before=(
                        'curl() { record curl "$@"; while (($#)); do if [[ $1 == -o ]]; then shift; printf synthetic-key > "$1"; break; fi; shift; done; }\n'
                        'sha256sum() { read -r expected path; record verify "$expected"; return "${TEST_HASH_STATUS:-0}"; }\n'
                    ),
                )
                if expected_arch is None:
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("Unsupported WineHQ target", result.stderr)
                    self.assertEqual(commands, [])
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    metadata = files["apt/sources.list.d/winehq.sources"].decode()
                    self.assertIn(f"Suites: {codename}\n", metadata)
                    self.assertIn(f"Architectures: {expected_arch}\n", metadata)
                    self.assertIn("Signed-By:", metadata)
                    self.assertEqual(commands[1][0], "verify")

    def test_shell_fixture_mapping_rejects_code_or_line_rewrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sh"
            source.write_text("printf /example\\n\n")
            with patch.dict(os.environ, {"MT5_SHELL_COVERAGE_MAP": ""}):
                self.assertEqual(
                    copy_shell(source, root / "copied").read_text(), source.read_text()
                )
                for replacements in (
                    (("printf", "/replacement"),),
                    (("/example", "relative"),),
                    (("/example", "/bad\nline"),),
                    (("/example", "/same-line; false"),),
                ):
                    with self.assertRaises(ValueError):
                        copy_shell(source, root / "invalid", replacements)

    def test_native_artifact_collection_checks_dependencies_and_copies_only_missing_libraries(
        self,
    ):
        def setup(root):
            (root / "viewer").touch(mode=0o755)
            (root / "lib").mkdir()
            (root / "lib/libviewer.so.1").write_text("synthetic-library")
            destination = Path(
                str(root / "baseline") + str(root / "lib/libviewer.so.1")
            )
            destination.parent.mkdir(parents=True)
            destination.touch()

        for phase, missing, baseline, primary, expected in (
            ("input", False, True, True, 0),
            ("vnc", False, True, True, 0),
            ("vnc", False, False, False, 0),
            ("vnc", True, True, True, 1),
            ("invalid", False, True, True, 2),
        ):
            with self.subTest(phase=phase, missing=missing, baseline=baseline):
                before = (
                    "pkg-config() { printf /synthetic/lib; }\n"
                    'install() { record install "$@"; }\n'
                    'command() { if [[ $* == "-v vncviewer" && $TEST_PRIMARY == 1 || $* == "-v xtigervncviewer" ]]; then printf "%s/viewer\\n" "$TEST_ROOT"; else return 1; fi; }\n'
                    'ldd() { if [[ $TEST_MISSING == 1 ]]; then printf "libmissing.so => not found\\n"; else printf "libviewer.so.1 => %s/lib/libviewer.so.1 (0x0)\\n" "$TEST_ROOT"; fi; }\n'
                )
                if not baseline:
                    before += (
                        'rm -- "$TEST_ROOT/baseline$TEST_ROOT/lib/libviewer.so.1"\n'
                    )
                result, commands, _, _ = self.run_source(
                    ROOT / "build/collect-artifacts",
                    arguments=(phase,),
                    setup=setup,
                    before=before,
                    replacements=(("/desktop-baseline", "baseline"),),
                    environment={
                        "TEST_PRIMARY": str(int(primary)),
                        "TEST_MISSING": str(int(missing)),
                    },
                )
                self.assertEqual(result.returncode, expected, result.stderr)
                if phase == "input":
                    self.assertEqual(len(commands), 2)
                    self.assertTrue(commands[0][-1].endswith("/plasma-clipboard"))
                    self.assertTrue(commands[1][-1].endswith("/krfb/events/kwin.so"))
                elif phase == "vnc" and not missing:
                    self.assertEqual(len(commands), 3 if baseline else 4)
                    self.assertTrue(commands[1][-1].endswith("/krfb/framebuffer/pw.so"))
                    if not baseline:
                        self.assertTrue(commands[-1][-1].endswith("/libviewer.so.1"))
                elif missing:
                    self.assertIn("not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
