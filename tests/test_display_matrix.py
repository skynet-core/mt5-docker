"""The display index must reject missing, mismatched and false GUI evidence."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from display_matrix import case_path, index, profiles
from PIL import Image, ImageDraw

IMAGE_ID = "sha256:" + "1" * 64


class DisplayMatrixTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.distro = self.root / "fedora-44"
        self.distro.mkdir()
        (self.distro / "results.json").write_text(
            json.dumps({"passed": True, "image_id": IMAGE_ID})
        )
        for backend, mode, host in profiles():
            path = case_path(backend, mode, host)
            directory = self.distro / path
            directory.mkdir(parents=True)
            report = {
                "passed": True,
                "image_id": IMAGE_ID,
                "os_release": {"ID": "fedora", "VERSION_ID": "44"},
                "backend": backend,
                "mode": mode,
                "host_backend": host or "none",
                "evidence": path.as_posix(),
                "screenshot": "screenshot.png",
                "gui_rendered": True,
                "capture_method": (
                    "kwin-screenshot2" if backend == "wayland" else "x11-root"
                )
                if mode == "headless"
                else "rfb",
                "rfb_authenticated": True,
            }
            if mode == "headless":
                report["headless_private"] = True
                report["headless_services"] = {
                    "plasma-vnc.service": "inactive",
                    "plasma-web.service": "inactive",
                }
            if mode != "headless":
                report["gui_input"] = {
                    "passed": True,
                    "method": "rfb",
                    "expected": "expected.txt",
                    "saved": "typed.txt",
                    "screenshot": "screenshot.png",
                }
                (directory / "expected.txt").write_text(path.as_posix())
                (directory / "typed.txt").write_text(path.as_posix())
            (directory / "results.json").write_text(json.dumps(report))
            for name in ("start.log", "services.log", "container.log", "units.log"):
                (directory / name).write_text("retained\n")
            image = Image.new("RGB", (800, 600), "#202020")
            draw = ImageDraw.Draw(image)
            draw.rectangle((12, 12, 787, 55), fill=(18, 210, 178))
            draw.rectangle((12, 65, 787, 550), fill="white")
            image.save(directory / "screenshot.png")

    def collect(self):
        return index(self.root, ("fedora-44",))

    def test_complete_expected_matrix_and_nested_layout(self):
        self.assertEqual(len(list(profiles())), 10)
        self.assertEqual(
            case_path("wayland", "nested", "x11"), Path("wayland/nested/on-x11")
        )
        result = self.collect()
        self.assertTrue(result["passed"])
        self.assertEqual(result["expected_cases"], 10)
        self.assertEqual(result["passed_cases"], 10)
        for distro, os_release in (
            ("debian-13", {"ID": "debian", "VERSION_ID": "13"}),
            ("ubuntu-26.04", {"ID": "ubuntu", "VERSION_ID": "26.04"}),
            ("archlinux", {"ID": "arch"}),
        ):
            destination = self.root / distro
            shutil.copytree(self.distro, destination)
            for result_file in destination.rglob("results.json"):
                report = json.loads(result_file.read_text())
                report["os_release"] = os_release
                result_file.write_text(json.dumps(report))
        complete = index(self.root)
        self.assertTrue(complete["passed"])
        self.assertEqual(complete["expected_cases"], 40)
        self.assertEqual(complete["passed_cases"], 40)

    def test_same_count_with_wrong_case_cannot_pass(self):
        (self.distro / "x11/web").rename(self.distro / "x11/wrong-mode")
        result = self.collect()
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["cases"]), 10)
        self.assertTrue((self.root / "INDEX.md").is_file())

    def test_arch_build_version_is_preserved(self):
        destination = self.root / "archlinux"
        shutil.copytree(self.distro, destination)
        os_release = {"ID": "arch", "VERSION_ID": "20261018.0.612668"}
        for result_file in destination.rglob("results.json"):
            report = json.loads(result_file.read_text())
            report["os_release"] = os_release
            result_file.write_text(json.dumps(report))
        result = index(self.root, ("archlinux",))
        self.assertTrue(result["passed"])
        self.assertEqual(result["passed_cases"], 10)
        self.assertTrue(
            all(case["os_release"] == os_release for case in result["cases"])
        )

    def test_alias_to_the_wrong_distribution_cannot_count_as_another_image(self):
        shutil.copytree(self.distro, self.root / "ubuntu-26.04")
        result = index(self.root, ("ubuntu-26.04",))
        self.assertFalse(result["passed"])
        self.assertEqual(result["passed_cases"], 0)
        self.assertTrue(
            all(
                any("os_release" in error for error in case["errors"])
                for case in result["cases"]
            )
        )

    def test_case_cannot_claim_a_different_image_or_profile(self):
        path = self.distro / "wayland/vnc/results.json"
        report = json.loads(path.read_text())
        report.update(image_id="sha256:" + "2" * 64, backend="x11")
        path.write_text(json.dumps(report))
        result = self.collect()
        self.assertFalse(result["passed"])
        failed = next(case for case in result["cases"] if case["errors"])
        self.assertTrue(any("image_id" in error for error in failed["errors"]))
        self.assertTrue(any("backend" in error for error in failed["errors"]))

    def test_headless_requires_a_real_nonblank_marker_screenshot(self):
        path = self.distro / "wayland/headless/screenshot.png"
        for image in (
            Image.new("RGB", (800, 600), "black"),
            Image.new("RGB", (800, 600), "white"),
            Image.new("RGBA", (800, 600), (18, 210, 178, 0)),
            Image.new("RGB", (20, 20), (18, 210, 178)),
        ):
            image.save(path)
            self.assertFalse(self.collect()["passed"])
        path.write_bytes(b"not a PNG")
        self.assertFalse(self.collect()["passed"])
        path.unlink()
        self.assertFalse(self.collect()["passed"])

    def test_success_boolean_cannot_replace_saved_gui_input(self):
        (self.distro / "x11/vnc/typed.txt").write_text("stale text")
        result = self.collect()
        self.assertFalse(result["passed"])
        self.assertTrue(
            any(
                "Saved GUI input" in error
                for case in result["cases"]
                for error in case["errors"]
            )
        )

    def test_headless_cannot_pass_with_a_remote_service_or_remote_capture(self):
        path = self.distro / "wayland/headless/results.json"
        report = json.loads(path.read_text())
        report["headless_services"]["plasma-vnc.service"] = "active"
        path.write_text(json.dumps(report))
        self.assertFalse(self.collect()["passed"])
        report["headless_services"]["plasma-vnc.service"] = "inactive"
        report["capture_method"] = "rfb"
        path.write_text(json.dumps(report))
        self.assertFalse(self.collect()["passed"])

    def test_missing_logs_or_malformed_results_remain_indexed_failures(self):
        (self.distro / "x11/web/services.log").unlink()
        (self.distro / "wayland/vnc/results.json").write_text("invalid json")
        result = self.collect()
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["cases"]), 10)
        self.assertEqual(result["passed_cases"], 8)


if __name__ == "__main__":
    unittest.main()
