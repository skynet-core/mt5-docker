"""Small checks for validation's evidence parsers."""

import unittest

from PIL import Image, ImageDraw
from validate import (
    chart_has_content,
    compilation_ok,
    display_profile,
    history_ready,
    kwin_version,
    public_diagnostics,
    report_images,
    report_summary,
    tool_data,
)


class EvidenceTests(unittest.TestCase):
    def test_public_failure_diagnostics_exclude_credentials_and_mcp_responses(self):
        private = "account=123456 password=private-password server=private-broker"
        evidence = {
            "case": "fedora-44-wayland-headless",
            "passed": False,
            "phase": "account_info",
            "failure_type": "RuntimeError",
            "error": private,
            "calls": {"account_info": {"data": private}},
            "password": "private-password",
        }
        self.assertEqual(
            public_diagnostics(evidence),
            {
                "case": "fedora-44-wayland-headless",
                "passed": False,
                "phase": "account_info",
                "failure_type": "RuntimeError",
            },
        )

    def test_running_kwin_version_is_read_from_support_information(self):
        self.assertEqual(
            kwin_version("Version\nKWin version: 6.8.2\nQt Version: 6.8.3"), "6.8.2"
        )
        self.assertEqual(
            kwin_version("\tKWin Version:\t6.5.6\r\nOperation Mode: X11"), "6.5.6"
        )
        self.assertIsNone(kwin_version("Qt Version: 6.8.2"))

    def test_display_profile_keeps_only_public_mode_fields(self):
        self.assertEqual(
            display_profile(
                "# Session\nDISPLAY_MODE='nested'\nDISPLAY_BACKEND=wayland\nHOME=/home/plasma\n"
            ),
            {"DISPLAY_MODE": "nested", "DISPLAY_BACKEND": "wayland"},
        )

    def test_history_requires_bars_inside_the_requested_dates(self):
        self.assertTrue(history_ready([{"time": "10"}, {"time": 20}], 10, 20))
        self.assertFalse(history_ready([], 10, 20))
        self.assertFalse(history_ready(None, 10, 20))
        self.assertFalse(history_ready([{"time": "30"}], 10, 20))

    def test_palette_chart_passes_but_empty_grid_does_not(self):
        image = Image.new("RGB", (160, 90), "black")
        draw = ImageDraw.Draw(image)
        for index in range(8):
            shade = 20 * index
            draw.line((20 * index, 0, 20 * index, 89), fill=(shade, shade, shade))
        self.assertFalse(chart_has_content(image))
        for index in range(8):
            draw.rectangle(
                (20 * index + 3, 10, 20 * index + 8, 70), fill=(0, 100 + 20 * index, 0)
            )
        self.assertTrue(chart_has_content(image))

    def test_compilation_needs_fresh_binary_and_zero_errors(self):
        self.assertTrue(compilation_ok("Result: 0 errors, 1 warnings", 100))
        self.assertFalse(compilation_ok("Result: 10 errors, 0 warnings", 100))
        self.assertFalse(compilation_ok("Result: 0 errors, 0 warnings", 0))

    def test_report_keeps_paired_results(self):
        html = "<table><tr><td>Expert:</td><td><b>AlgoSamurai</b></td></tr><tr><td>Bars:</td><td>1234</td><td>Total Trades:</td><td>7</td></tr></table>"
        self.assertEqual(
            report_summary(html),
            {"Expert": "AlgoSamurai", "Bars": "1234", "Total Trades": "7"},
        )

    def test_tool_rejects_protocol_error(self):
        class Result:
            isError = True
            content = ("error",)

        with self.assertRaises(RuntimeError):
            tool_data(Result())

    def test_report_images_preserve_names_and_reject_traversal(self):
        self.assertEqual(
            report_images('<img src="run.png"><img src="run-hst.png">'),
            ["run.png", "run-hst.png"],
        )
        with self.assertRaises(RuntimeError):
            report_images('<img src="../private.png">')


if __name__ == "__main__":
    unittest.main()
