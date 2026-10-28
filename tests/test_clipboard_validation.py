"""The clipboard validator must stay on the supplied private parent display."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SOURCE = Path(__file__).with_name("clipboard.py")
SPEC = importlib.util.spec_from_file_location("clipboard_validation", SOURCE)
CLIPBOARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLIPBOARD)


class ClipboardIsolationTests(unittest.TestCase):
    def test_transfers_use_fresh_readers_and_distinct_payloads(self):
        probes = []
        testcase = self

        class Probe:
            def __init__(self, *arguments):
                self.batch_start = len(probes) // 4 * 4
                self.payload = None
                self.closed = False
                self.read = False
                probes.append(self)

            def set(self, payload):
                self.payload = payload

            def wait(self, expected):
                testcase.assertIsNone(
                    self.payload, "A receiver must never own the clipboard"
                )
                testcase.assertTrue(
                    all(not probe.closed for probe in probes[self.batch_start :])
                )
                self.read = True
                return expected

            def close(self):
                self.closed = True

        parent = {"DISPLAY_MODE": "vnc", "DISPLAY_BACKEND": "wayland"}
        inner = {
            "DISPLAY_MODE": "nested",
            "DISPLAY_BACKEND": "wayland",
            "HOST_DISPLAY_BACKEND": "wayland",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                CLIPBOARD, "configuration", side_effect=[parent, inner, parent, inner]
            ),
            patch.object(CLIPBOARD, "socket_identity", return_value=b"10:123"),
            patch.object(CLIPBOARD, "Probe", Probe),
            patch.object(
                CLIPBOARD.subprocess,
                "run",
                return_value=SimpleNamespace(stdout=b"", stderr=b""),
            ),
            patch("builtins.print"),
        ):
            for case in ("first", "second"):
                CLIPBOARD.validate(
                    SimpleNamespace(
                        runtime="docker",
                        parent="parent",
                        container="child",
                        case=case,
                        output=directory,
                    )
                )
        self.assertEqual(len(probes), 8)
        self.assertTrue(all(probe.closed for probe in probes))
        for start in (0, 4):
            self.assertTrue(all(probe.payload for probe in probes[start : start + 2]))
            self.assertTrue(all(probe.read for probe in probes[start + 2 : start + 4]))
        self.assertNotEqual(
            probes[0].payload["text/plain"], probes[4].payload["text/plain"]
        )

    def test_same_private_parent_socket_is_required(self):
        parent = {"DISPLAY_MODE": "vnc"}
        inner = {"DISPLAY_MODE": "nested"}
        CLIPBOARD.require_isolated_parent(parent, inner, b"10:123", b"10:123")
        for parent_mode, inner_mode, parent_socket, inner_socket in (
            ("nested", "nested", b"10:123", b"10:123"),
            ("vnc", "headless", b"10:123", b"10:123"),
            ("vnc", "nested", b"10:123", b"10:124"),
            ("vnc", "nested", b"", b""),
        ):
            with (
                self.subTest(parent=parent_mode, inner=inner_mode, socket=inner_socket),
                self.assertRaises(ValueError),
            ):
                CLIPBOARD.require_isolated_parent(
                    {"DISPLAY_MODE": parent_mode},
                    {"DISPLAY_MODE": inner_mode},
                    parent_socket,
                    inner_socket,
                )


if __name__ == "__main__":
    unittest.main()
