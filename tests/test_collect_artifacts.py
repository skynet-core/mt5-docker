"""Viewer libraries must preserve SONAMEs without replacing merged-/usr links."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class ArtifactCollectionTests(unittest.TestCase):
    def test_merged_library_directory_keeps_loader_filename(self):
        script = Path(__file__).resolve().parents[1] / "build/collect-artifacts"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            libraries = root / "usr/lib"
            libraries.mkdir(parents=True)
            (root / "lib").symlink_to("usr/lib")
            (libraries / "libviewer.so.1.2").touch()
            (libraries / "libviewer.so.1").symlink_to("libviewer.so.1.2")
            commands = root / "bin"
            commands.mkdir()
            for name, body in {
                "pkg-config": "printf '/usr/lib\\n'",
                "vncviewer": "exit 0",
                "ldd": 'printf "libviewer.so.1 => %s (0x123)\\n" "$TEST_LIBRARY"',
                "install": 'printf "%s\\n" "${@: -1}" >> "$INSTALL_LOG"',
            }.items():
                path = commands / name
                path.write_text("#!/bin/bash\n" + body + "\n")
                path.chmod(0o755)
            log = root / "install.log"
            subprocess.run(
                ["/bin/bash", str(script), "vnc"],
                check=True,
                env={
                    **os.environ,
                    "PATH": str(commands) + os.pathsep + os.environ["PATH"],
                    "TEST_LIBRARY": str(root / "lib/libviewer.so.1"),
                    "INSTALL_LOG": str(log),
                },
            )
            self.assertIn(
                f"/out/root{libraries}/libviewer.so.1", log.read_text().splitlines()
            )
            self.assertNotIn(
                f"/out/root{root}/lib/libviewer.so.1", log.read_text().splitlines()
            )


if __name__ == "__main__":
    unittest.main()
