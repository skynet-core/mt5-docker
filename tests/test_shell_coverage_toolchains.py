"""Build-shell contracts exercise orchestration with synthetic toolchain outputs."""

import unittest
from pathlib import Path

import test_shell_coverage_runtime as runtime_tests
from shell_coverage import copy_shell

ROOT = Path(__file__).resolve().parents[1]

TOOLCHAIN = r"""
curl() { record curl "$@"; local previous=; for item in "$@"; do if [[ $previous == --output ]]; then : > "$item"; fi; previous=$item; done; }
sha256sum() { read -r checksum archive; record checksum "$checksum"; return "${TEST_CHECKSUM_STATUS:-0}"; }
tar() {
    record tar "$@"
    local previous= directory=
    for item in "$@"; do if [[ $previous == -C ]]; then directory=$item; fi; previous=$item; done
    if [[ ${TEST_LICENSES:-1} == 1 ]]; then mkdir -p "$directory/LICENSES"; printf synthetic-license > "$directory/LICENSES/LICENSE"; fi
}
cmake() {
    record cmake "$@"
    if [[ $1 == --install ]]; then
        mkdir -p "$TEST_ROOT/prefix/bin" "$TEST_ROOT/prefix/lib"
        for binary in kwin_wayland kwin_wayland_wrapper kbuildsycoca6 kscreen-doctor; do
            printf '#!/bin/bash\nprintf "KWin 6.8.2\\n"\n' > "$TEST_ROOT/prefix/bin/$binary"
            chmod +x "$TEST_ROOT/prefix/bin/$binary"
        done
        printf '#!/bin/bash\nprintf "${TEST_QT_VERSION:-6.11.2}\\n"\n' > "$TEST_ROOT/prefix/bin/qmake"
        chmod +x "$TEST_ROOT/prefix/bin/qmake"
        : > "$TEST_ROOT/prefix/lib/libKF6Screen.so.6.8.2"
    fi
}
meson() { record meson "$@"; }
getcap() { if [[ ${TEST_CAPABILITY:-0} == 1 && ${TEST_CAP_REMOVED:-0} == 0 ]]; then printf 'synthetic cap_sys_nice=ep\n'; fi; }
setcap() { record setcap "$@"; TEST_CAP_REMOVED=1; }
"""


class ShellToolchainTests(unittest.TestCase):
    run_source = runtime_tests.ShellRuntimeTests.run_source

    def kwin_setup(self, root):
        support = root / "support"
        support.mkdir()
        manifest = "".join(
            (ROOT / name).read_text()
            for name in (
                "build/kwin-6.8.2-sources.tsv",
                "build/kwin-client-sources.tsv",
                "build/kwin-vulkan-sources.tsv",
            )
        )
        (support / "kwin-6.8.2-sources.tsv").write_text(manifest)
        for phase in (ROOT / "build/kwin-phases").glob("*.sh"):
            replacements = [
                ("/work", str(root / "work")),
                ("/out/root", str(root / "out/root")),
            ]
            if phase.stem == "collect":
                replacements += [
                    (str(root / "out/root/opt"), str(root / "out/root") + str(root)),
                    (
                        "/usr/lib/x86_64-linux-gnu/libdisplay-info.so.2",
                        str(root / "display-info.so.2"),
                    ),
                ]
            copy_shell(phase, support / f"kwin-phase-{phase.stem}.sh", replacements)
        (root / "display-info.so.2").write_text("synthetic-ABI-payload")
        (root / "prefix/include").mkdir(parents=True)
        (root / "prefix/include/development.h").write_text("do not ship")
        (root / "prefix/lib").mkdir()
        (root / "prefix/lib/development.a").write_text("do not ship")

    def test_every_private_stack_phase_keeps_its_install_prefix_and_verifies_versions(
        self,
    ):
        for phase in (
            "platform",
            "qtbase",
            "qtdeclarative",
            "qtmodules",
            "frameworks",
            "kwin",
            "collect",
        ):
            with self.subTest(phase=phase):
                result, commands, _, files = self.run_source(
                    ROOT / "build/kwin-6.8.2",
                    arguments=(phase,),
                    setup=self.kwin_setup,
                    replacements=(
                        ("/opt/kwin-6.8.2", "prefix"),
                        ("/build-support", "support"),
                        ("/work", "work"),
                    ),
                    before=TOOLCHAIN,
                    environment={"TEST_CAPABILITY": "1" if phase == "kwin" else "0"},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(any(command[0] == "checksum" for command in commands))
                self.assertTrue(any(command[0] == "tar" for command in commands))
                if phase == "platform":
                    self.assertTrue(
                        any(command[:2] == ["meson", "compile"] for command in commands)
                    )
                else:
                    configure = [
                        command
                        for command in commands
                        if command[:2] == ["cmake", "-S"]
                    ]
                    self.assertTrue(configure)
                    self.assertTrue(
                        all(
                            any(
                                value.startswith("-DCMAKE_INSTALL_PREFIX=")
                                and value.endswith("/prefix")
                                for value in command
                            )
                            for command in configure
                        )
                    )
                if phase == "kwin":
                    self.assertTrue(
                        any(command[:2] == ["setcap", "-r"] for command in commands)
                    )
                    self.assertIn("KWin 6.8.2", result.stdout)
                if phase == "collect":
                    payloads = [name for name in files if name.startswith("out/root/")]
                    self.assertTrue(
                        any(name.endswith("/bin/kwin_wayland") for name in payloads)
                    )
                    self.assertTrue(
                        any(
                            name.endswith("/lib/libdisplay-info.so.2")
                            for name in payloads
                        )
                    )
                    self.assertFalse(
                        any(name.endswith((".a", "development.h")) for name in payloads)
                    )

    def test_source_hash_failure_stops_before_extracting_or_building(self):
        result, commands, _, _ = self.run_source(
            ROOT / "build/kwin-6.8.2",
            arguments=("qtbase",),
            setup=self.kwin_setup,
            replacements=(
                ("/opt/kwin-6.8.2", "prefix"),
                ("/build-support", "support"),
                ("/work", "work"),
            ),
            before=TOOLCHAIN,
            environment={"TEST_CHECKSUM_STATUS": "1"},
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse(any(command[0] in ("tar", "cmake") for command in commands))

    def test_wrong_qt_version_and_unknown_phase_are_rejected(self):
        for phase, version, expected in (
            ("qtbase", "0.0.0", 1),
            ("not-a-phase", "6.11.2", 2),
        ):
            with self.subTest(phase=phase):
                result, _, _, _ = self.run_source(
                    ROOT / "build/kwin-6.8.2",
                    arguments=(phase,),
                    setup=self.kwin_setup,
                    replacements=(
                        ("/opt/kwin-6.8.2", "prefix"),
                        ("/build-support", "support"),
                        ("/work", "work"),
                    ),
                    before=TOOLCHAIN,
                    environment={"TEST_QT_VERSION": version, "TEST_LICENSES": "0"},
                )
                self.assertEqual(result.returncode, expected)

    def test_arch_x11_build_checks_the_isolated_namespace_and_prunes_development_files(
        self,
    ):
        stub = r"""
curl() { record curl "$@"; }
sha256sum() { read -r checksum archive; record checksum "$checksum"; }
tar() { record tar "$@"; }
cmake() {
    record cmake "$@"
    if [[ $1 == --install ]]; then
        mkdir -p "$TEST_ROOT/out/root/usr/bin" "$TEST_ROOT/out/root/usr/lib" "$TEST_ROOT/out/root/usr/include"
        printf '#!/bin/bash\n' > "$TEST_ROOT/out/root/usr/bin/kwin_x11"
        chmod +x "$TEST_ROOT/out/root/usr/bin/kwin_x11"
        : > "$TEST_ROOT/out/root/usr/lib/libkwin-x11.so.6"
        : > "$TEST_ROOT/out/root/usr/include/development.h"
    fi
}
"""
        result, commands, _, files = self.run_source(
            ROOT / "build/arch-kwin-x11",
            replacements=(
                ("/tmp/kwin-x11.tar.gz", "archive"),
                ("/kwin-x11-source", "source-tree"),
                ("/kwin-x11-build", "build-tree"),
                ("/out/root", "out/root"),
            ),
            before=stub,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands[1][0], "checksum")
        self.assertIn("out/root/usr/bin/kwin_x11", files)
        self.assertIn("out/root/usr/lib/libkwin-x11.so.6", files)
        self.assertNotIn("out/root/usr/include/development.h", files)
        self.assertNotIn("out/root/usr/bin/kwin_wayland", files)


if __name__ == "__main__":
    unittest.main()
