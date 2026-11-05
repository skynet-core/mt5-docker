"""Keep patched KRFB files after package-manager transactions."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = "COPY --from=vnc-build /out/root/ /"


class ImageAssemblyTests(unittest.TestCase):
    def test_patched_krfb_is_installed_after_runtime_and_wine_packages(self):
        for distro in ("fedora-44", "debian-13", "ubuntu-26.04", "archlinux"):
            with self.subTest(distro=distro):
                dockerfile = (ROOT / f"Dockerfile.{distro}").read_text()
                runtime = dockerfile.split("FROM desktop-packages AS runtime\n", 1)[1]
                runtime = runtime.split("FROM input-build AS fixture-build\n", 1)[0]
                self.assertGreater(runtime.rfind(OVERLAY), runtime.rfind("install-packages x11-runtime"))
                wine = dockerfile.split("FROM runtime AS wine-packages\n", 1)[1]
                wine = wine.split("FROM wine-packages AS mt5\n", 1)[0]
                self.assertGreater(wine.rfind(OVERLAY), wine.index("install-packages wine"))

    def test_pointer_parent_restores_patched_krfb_after_pacman(self):
        fragment = (ROOT / "tests/Dockerfile.pointer-parent.fragment").read_text()
        self.assertGreater(fragment.rfind(OVERLAY), fragment.index("pacman -Syu"))


if __name__ == "__main__":
    unittest.main()
