"""Keep patched KRFB files after package-manager transactions."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = "COPY --from=vnc-build /out/root/ /"


class ImageAssemblyTests(unittest.TestCase):
    def test_patched_krfb_is_installed_after_runtime_and_wine_packages(self):
        dockerfile = (ROOT / "Dockerfile.debian-13").read_text()
        runtime = dockerfile.split("FROM desktop-packages AS runtime\n", 1)[1]
        runtime = runtime.split("FROM input-build AS fixture-build\n", 1)[0]
        self.assertIn(OVERLAY, runtime)
        wine = dockerfile.split("FROM runtime AS wine-packages\n", 1)[1]
        wine = wine.split("FROM wine-packages AS mt5\n", 1)[0]
        self.assertGreater(wine.rfind(OVERLAY), wine.index("install-packages wine"))

    def test_pointer_parent_restores_patched_krfb_after_packages(self):
        fragment = (ROOT / "tests/Dockerfile.pointer-parent.fragment").read_text()
        self.assertGreater(fragment.rfind(OVERLAY), fragment.index("apt-get install"))


if __name__ == "__main__":
    unittest.main()
