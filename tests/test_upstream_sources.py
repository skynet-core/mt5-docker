"""Keep upstream-owned runtime and build copies tied to their reviewed commit."""

import hashlib
import json
import re
import stat
import unittest
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


class UpstreamSourcesTests(unittest.TestCase):
    def test_upstream_sources_match_pinned_provenance(self):
        ledger = ROOT / "build/upstream-sources.json"
        self.assertTrue(ledger.is_file(), "Missing build/upstream-sources.json")
        metadata = json.loads(ledger.read_text())
        self.assertEqual(metadata["repository"], "skynet-core/kde-container")
        self.assertIsNotNone(re.fullmatch(r"[0-9a-f]{40}", metadata["commit"]))
        request = urlsplit(metadata["pull_request"])
        self.assertEqual(request.scheme, "https")
        self.assertTrue(request.netloc)
        vendor = ROOT / "vendor/kde-container"
        self.assertEqual(
            re.findall(
                r"^Commit: ([0-9a-f]{40})$",
                (vendor / "UPSTREAM").read_text(),
                re.MULTILINE,
            ),
            [metadata["commit"]],
        )
        self.assertIsInstance(metadata["files"], list)
        seen = {"path": set(), "upstream_path": set()}
        for entry in metadata["files"]:
            with self.subTest(path=entry["path"]):
                for field in seen:
                    self.assertIsInstance(entry[field], str)
                    path = PurePosixPath(entry[field])
                    self.assertTrue(path.parts)
                    self.assertFalse(path.is_absolute())
                    self.assertNotIn("..", path.parts)
                    self.assertNotIn(path, seen[field])
                    seen[field].add(path)
                path = ROOT / entry["path"]
                self.assertTrue(path.is_file() and not path.is_symlink())
                self.assertIsNotNone(re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]))
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"]
                )
                self.assertIn(entry["mode"], {"100644", "100755"})
                mode = "100755" if path.stat().st_mode & stat.S_IXUSR else "100644"
                self.assertEqual(mode, entry["mode"])
        self.assertEqual(
            {
                path
                for path in seen["path"]
                if path.is_relative_to("vendor/kde-container")
            },
            {
                PurePosixPath(path.relative_to(ROOT).as_posix())
                for path in vendor.rglob("*")
                if path.is_file()
                and path != vendor / "UPSTREAM"
                # Unit imports generate bytecode; it is not vendored source.
                and "__pycache__" not in path.parts
            },
        )


if __name__ == "__main__":
    unittest.main()
