"""Shared broker test volumes must keep one identity across all display modes."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class BrokerMatrixTests(unittest.TestCase):
    def test_every_mode_keeps_the_same_wine_volume_owner(self):
        source = Path(__file__).with_name("matrix.sh")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "bin").mkdir()
            commands = root / "commands"
            commands.mkdir()
            shutil.copyfile(source, root / "tests/matrix.sh")
            for path, body in {
                root / "bin/mt5-run": (
                    'printf "%s:%s:%s:%s\\n" "$DISPLAY_BACKEND" "$DISPLAY_MODE" '
                    '"$PLASMA_UID" "$PLASMA_GID" >> "$IDENTITY_LOG"\n'
                ),
                commands / "docker": (
                    'case "$1" in\n'
                    "inspect) if [[ ${2:-} == --format ]]; then echo healthy; else exit 1; fi ;;\n"
                    "port) echo 127.0.0.1:12345 ;;\n"
                    "esac\n"
                ),
                commands / "uv": "exit 0\n",
                commands / "id": (
                    'if [[ $1 == -u ]]; then echo "$TEST_UID"; else echo "$TEST_GID"; fi\n'
                ),
            }.items():
                path.write_text("#!/bin/bash\nset -eu\n" + body)
                path.chmod(0o755)
            for uid, gid, overrides, expected in (
                ("1001", "1002", {}, ("1001", "1002")),
                ("0", "0", {}, ("1000", "1000")),
                (
                    "1001",
                    "1002",
                    {"PLASMA_UID": "1500", "PLASMA_GID": "1600"},
                    ("1500", "1600"),
                ),
            ):
                with self.subTest(uid=uid, gid=gid, overrides=overrides):
                    log = root / "identity.log"
                    log.unlink(missing_ok=True)
                    environment = {
                        key: value
                        for key, value in os.environ.items()
                        if key not in ("PLASMA_UID", "PLASMA_GID")
                    }
                    subprocess.run(
                        ["/bin/bash", str(root / "tests/matrix.sh"), "fedora-44"],
                        env={
                            **environment,
                            **overrides,
                            "PATH": str(commands) + os.pathsep + os.environ["PATH"],
                            "TEST_UID": uid,
                            "TEST_GID": gid,
                            "IDENTITY_LOG": str(log),
                            "MT5_ENV_FILE": "/dev/null",
                            "OUTPUT_DIR": str(root / "output"),
                        },
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    self.assertEqual(
                        [line.split(":") for line in log.read_text().splitlines()],
                        [
                            [backend, mode, *expected]
                            for backend in ("wayland", "x11")
                            for mode in ("headless", "nested", "vnc", "web")
                        ],
                    )


if __name__ == "__main__":
    unittest.main()
