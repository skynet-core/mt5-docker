"""Coverage publishing keeps reports complete, escaped and free of raw evidence."""

import contextlib
import copy
import importlib.util
import io
import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "build/publish_coverage.py"
SPEC = importlib.util.spec_from_file_location("publisher", SOURCE)
PUBLISH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUBLISH)
REVISION = "a" * 40
REPOSITORY = "owner/repository"
RUN = "https://github.com/owner/repository/actions/runs/123"
PAGES = "https://owner.github.io/repository"


def report():
    rows = []
    for name, language, covered in (
        ("src/<bad>&[file]|.py", "python", 95),
        ("scripts/runtime.sh", "shell", 94),
        ("native/bridge.cpp", "c++", 96),
    ):
        rows.append(
            {
                "file": name,
                "language": language,
                "measured": True,
                "executable": 100,
                "covered": covered,
                "percent": covered,
                "passed": True,
                "declaration_only": None,
                "missing_lines": list(range(covered + 1, 101)),
                "private_environment": "never-publish",
            }
        )
    rows.append(
        {
            "file": "native/declarations.h",
            "language": "c++",
            "measured": False,
            "executable": 0,
            "covered": 0,
            "percent": None,
            "passed": True,
            "declaration_only": "Declarations <only> & audited | no\nexecutable code",
            "missing_lines": [],
        }
    )
    return {
        "minimum_per_file": 94,
        "passed": True,
        "files": rows,
        "private_environment": "never-publish",
    }


def files(root):
    data = root / "report.json"
    data.write_text(json.dumps(report()))
    readme = root / "README.md"
    readme.write_text(
        "before\n" + PUBLISH.START + "\nstale\n" + PUBLISH.END + "\nafter\n"
    )
    args = [
        "--report",
        str(data),
        "--output",
        str(root / "site"),
        "--readme",
        str(readme),
        "--repository",
        REPOSITORY,
        "--revision",
        REVISION,
        "--run-url",
        RUN,
        "--pages-url",
        PAGES,
    ]
    return args, readme


def detailed_reports(root):
    for name, data in (
        ("python/coverage.json", {"files": {"build/lint.py": {}}}),
        (
            "native/native.json",
            {"files": [{"file": "vendor/kde-container/native/clipboard.cpp"}]},
        ),
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))


class ReportValidationTests(unittest.TestCase):
    def test_test_sources_cannot_be_published_from_older_reports(self):
        for name in (
            "tests/test_runtime.py",
            "vendor/kde-container/test/egl-fence-test.cpp",
            "vendor/kde-container/native/ui-fixture.cpp",
            "vendor/kde-container/native/vnc-benchmark.cpp",
        ):
            with self.subTest(name=name):
                data = report()
                data["files"][0]["file"] = name
                with self.assertRaisesRegex(ValueError, "Test sources"):
                    PUBLISH.validate_report(data)

    def test_valid_metadata_is_reconstructed_without_private_extra_fields(self):
        data = PUBLISH.validate_report(report())
        self.assertNotIn("never-publish", json.dumps(data))
        self.assertEqual(
            [row["file"] for row in data["files"]],
            sorted(row["file"] for row in report()["files"]),
        )
        self.assertEqual(
            PUBLISH.summaries(data),
            [
                ("Python", 1, "95.00%", 0),
                ("Bash", 1, "94.00%", 0),
                ("C++", 2, "96.00%", 1),
            ],
        )

    def test_failed_empty_or_invalid_threshold_reports_are_rejected(self):
        for data in (
            [],
            {},
            {**report(), "passed": False},
            {**report(), "files": []},
            {**report(), "files": [None]},
            *(
                {**report(), "minimum_per_file": minimum}
                for minimum in (True, "94", 93, 101, float("nan"), float("inf"))
            ),
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                PUBLISH.validate_report(data)

    def test_invalid_counts_percentages_positions_or_file_states_are_rejected(self):
        variants = [
            {"language": "unknown"},
            {"passed": False},
            {"measured": 1},
            {"measured": False},
            {"covered": True},
            {"covered": -1},
            {"covered": 101},
            {"executable": "100"},
            {"executable": 0, "covered": 0, "missing_lines": [], "percent": 100},
            *(
                {"percent": percent}
                for percent in (True, "95", float("nan"), float("inf"), -1, 93, 94, 101)
            ),
            {"missing_lines": None},
            {"missing_lines": [0, 97, 98, 99, 100]},
            {"missing_lines": [True, 97, 98, 99, 100]},
            {"missing_lines": [96, 96, 98, 99, 100]},
            {"missing_lines": [97, 96, 98, 99, 100]},
            {"missing_lines": []},
            {"declaration_only": "not a declaration"},
        ]
        for change in variants:
            with self.subTest(change=change):
                data = report()
                data["files"][0].update(change)
                with self.assertRaises(ValueError):
                    PUBLISH.validate_report(data)
        data = report()
        data["files"].append(copy.deepcopy(data["files"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            PUBLISH.validate_report(data)

    def test_na_requires_an_actual_zero_line_declaration_audit(self):
        for change in (
            {"declaration_only": None},
            {"declaration_only": " "},
            {"language": "python"},
            {"covered": 1},
            {"executable": 1, "missing_lines": [1]},
        ):
            with self.subTest(change=change):
                data = report()
                data["files"][-1].update(change)
                with self.assertRaises(ValueError):
                    PUBLISH.validate_report(data)
        data = report()
        data["files"] = [data["files"][-1]]
        self.assertEqual(
            PUBLISH.summaries(PUBLISH.validate_report(data)), [("C++", 1, "N/A", 1)]
        )

    def test_source_paths_cannot_escape_or_inject_line_breaks(self):
        for name in (
            None,
            "",
            ".",
            "../private.py",
            "/absolute.py",
            "a/../private.py",
            "a/./file.py",
            "a//file.py",
            "a\\file.py",
            "https://example.test/file.py",
            "a\nfile.py",
            "a\x7ffile.py",
        ):
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(ValueError, "source path"),
            ):
                PUBLISH.source_path(name)


class RenderingTests(unittest.TestCase):
    def test_only_missing_local_native_index_breadcrumbs_fall_back_to_main_table(self):
        root = Path("/private/native")
        source = root / "nested/native.source.html"
        content = '<p>94.44% coverage &amp; source stays unchanged</p><a class="nav index" href="missing.html">up</a><a class="nav index" href="../native.html">valid</a><a class="other" href="missing.html">source</a><a class="nav index" href="https://example.test/">external</a><a class="nav index" href="#anchor">anchor</a><a class="nav index">no target</a>'
        expected = content.replace('href="missing.html">up', 'href="../native.html">up')
        rendered = PUBLISH.native_navigation(
            content, source, root, {root / "native.html"}
        )
        self.assertEqual(rendered, expected)
        for href in (
            "../../private.html",
            "/private.html",
            r"..\private.html",
            "%00.html",
            "file:///private.html",
            "javascript:alert(1)",
        ):
            with (
                self.subTest(href=href),
                self.assertRaisesRegex(ValueError, "native|Native"),
            ):
                PUBLISH.native_navigation(
                    f'<a class="nav index" href="{href}">up</a>', source, root, set()
                )

    def test_html_and_readme_escape_all_rows_and_link_missing_lines_at_tested_sha(self):
        data = PUBLISH.validate_report(report())
        document = PUBLISH.render_html(data, REPOSITORY, REVISION, RUN, [])
        block = PUBLISH.readme_block(data, REPOSITORY, REVISION, RUN, PAGES)
        self.assertNotIn("<bad>", document)
        self.assertNotIn("<only>", document)
        self.assertIn("&lt;bad&gt;&amp;[file]|.py", document)
        self.assertIn('class="audit"', document)
        self.assertIn("Declarations &lt;only&gt; &amp; audited", document)
        self.assertIn("%3Cbad%3E%26%5Bfile%5D%7C.py#L96", document)
        self.assertIn(f"/blob/{REVISION}/scripts/runtime.sh#L95", document)
        self.assertIn("All 4 source files", block)
        self.assertIn("<details>", block)
        self.assertIn("&lt;bad&gt;&amp;\\[file\\]\\|.py", block)
        self.assertIn(
            "Declarations &lt;only&gt; &amp; audited \\| no executable code", block
        )
        self.assertNotIn("Python annotated", document)

    def test_marker_replacement_preserves_every_byte_outside_the_existing_pair(self):
        content = "prefix\n" + PUBLISH.START + "\nold\n" + PUBLISH.END + "\nsuffix\n"
        expected = "prefix\n" + PUBLISH.START + "\nnew\n" + PUBLISH.END + "\nsuffix\n"
        self.assertEqual(PUBLISH.replace_readme(content, "new"), expected)
        self.assertEqual(PUBLISH.replace_readme(expected, "new"), expected)
        for malformed in (
            "no markers",
            PUBLISH.START,
            PUBLISH.END + PUBLISH.START,
            content + PUBLISH.START,
            content.replace(PUBLISH.START, "<!--coverage-report:start-->"),
            content + "<!-- coverage-report: broken",
        ):
            with (
                self.subTest(malformed=malformed),
                self.assertRaisesRegex(ValueError, "marker"),
            ):
                PUBLISH.replace_readme(malformed, "new")

    def test_public_urls_reject_credentials_and_encode_markdown_metacharacters(self):
        self.assertEqual(
            PUBLISH.public_url("https://example.test/a(b)/"),
            "https://example.test/a%28b%29",
        )
        for url in (
            "http://example.test",
            "javascript:alert(1)",
            "https://user:password@example.test",
            "https://example.test?token=private",
            "https://example.test/#secret",
            "https://example.test/%2e%2e/file",
            "https://example.test/a b",
            "https://example.test/%0a",
            "https://<bad>.test",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                PUBLISH.public_url(url)


class PublishingTests(unittest.TestCase):
    def test_cli_copies_only_public_static_assets_and_embeds_the_complete_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, readme = files(root)
            artifact = root / "artifact"
            source = {
                "python/html/index.html": "python index",
                "python/html/style.css": "python CSS",
                "python/html/sub/source.html": "python source",
                "python/html/code.js": "python JS",
                "python/html/status.json": "private-payload",
                "python/html/.coverage": "private-payload",
                "python/html/debug.log": "private-payload",
                "native/native.html": "native index",
                "native/native.css": "native CSS",
                "native/native.functions.html": "native source",
                "native/native.source.html": '<a class="nav index" href="missing.html">up</a><p>covered</p>',
                "native/native.png": "native icon",
                "native/ctest.log": "private-payload",
                "native/foreign.html": "private-payload",
                "shell/maps.jsonl": "private-payload",
                ".env": "private-payload",
            }
            for name, value in source.items():
                path = artifact / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value)
            detailed_reports(artifact)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    PUBLISH.main(args + ["--artifact-root", str(artifact)]), 0
                )
            site = root / "site"
            copied = {
                path.relative_to(site).as_posix()
                for path in site.rglob("*")
                if path.is_file()
            }
            self.assertEqual(
                copied,
                {
                    "index.html",
                    "coverage.json",
                    "python/html/index.html",
                    "python/html/style.css",
                    "python/html/sub/source.html",
                    "python/html/code.js",
                    "native/native.html",
                    "native/native.css",
                    "native/native.functions.html",
                    "native/native.source.html",
                    "native/native.png",
                },
            )
            self.assertTrue(
                all(
                    "private-payload" not in path.read_text()
                    for path in site.rglob("*")
                    if path.is_file()
                )
            )
            self.assertNotIn("never-publish", (site / "coverage.json").read_text())
            self.assertIn(
                "Python annotated source and branches",
                (site / "index.html").read_text(),
            )
            metadata = json.loads((site / "coverage.json").read_text())
            self.assertEqual(
                (site / "native/native.source.html").read_text(),
                '<a class="nav index" href="native.html">up</a><p>covered</p>',
            )
            self.assertEqual(metadata["revision"], REVISION)
            self.assertEqual(len(metadata["files"]), 4)
            self.assertTrue(readme.read_text().startswith("before\n" + PUBLISH.START))
            self.assertTrue(readme.read_text().endswith(PUBLISH.END + "\nafter\n"))
            self.assertIn(f"[CI run]({RUN})", readme.read_text())

    def test_optional_assets_and_an_existing_empty_output_directory_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, readme = files(root)
            readme.write_bytes(readme.read_bytes().replace(b"\n", b"\r\n"))
            (root / "site").mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(PUBLISH.main(args), 0)
            self.assertEqual(
                {path.name for path in (root / "site").iterdir()},
                {"index.html", "coverage.json"},
            )
            self.assertTrue(
                readme.read_bytes().startswith(b"before\r\n" + PUBLISH.START.encode())
            )
            self.assertTrue(
                readme.read_bytes().endswith(PUBLISH.END.encode() + b"\r\nafter\r\n")
            )

    def test_missing_or_symlinked_assets_fail_before_readme_or_site_changes(self):
        for failure in ("missing", "symlink-file", "symlink-directory"):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                args, readme = files(root)
                before = readme.read_bytes()
                artifact = root / "artifact"
                detailed_reports(artifact)
                (artifact / "python/html").mkdir(parents=True)
                (artifact / "python/html/index.html").write_text("python")
                (artifact / "native").mkdir(exist_ok=True)
                if failure != "missing":
                    (artifact / "native/native.html").write_text("native")
                    if failure == "symlink-file":
                        (artifact / "native/native.css").symlink_to(
                            root / "report.json"
                        )
                    else:
                        (artifact / "python/html/external").symlink_to(
                            root, target_is_directory=True
                        )
                with self.assertRaises(ValueError):
                    PUBLISH.main(args + ["--artifact-root", str(artifact)])
                self.assertEqual(readme.read_bytes(), before)
                self.assertFalse((root / "site").exists())

    def test_detailed_metadata_rejects_test_sources_even_with_a_clean_main_table(self):
        for name, data in (
            ("python/coverage.json", {"files": {"tests/test_runtime.py": {}}}),
            (
                "native/native.json",
                {"files": [{"file": "/source/tests/native/input-unit.cpp"}]},
            ),
            (
                "native/native.json",
                {
                    "files": [
                        {
                            "file": str(
                                PUBLISH.ROOT
                                / "vendor/kde-container/native/ui-fixture.cpp"
                            )
                        }
                    ]
                },
            ),
            (
                "native/native.json",
                {"files": [{"file": "./vendor/kde-container/test/egl-fence-test.cpp"}]},
            ),
            (
                "native/native.json",
                {"files": [{"file": "vendor/kde-container/native/vnc-benchmark.cpp"}]},
            ),
        ):
            with (
                self.subTest(name=name, data=data),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                args, readme = files(root)
                before = readme.read_bytes()
                artifact = root / "artifact"
                detailed_reports(artifact)
                (artifact / name).write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, "Test sources"):
                    PUBLISH.main(args + ["--artifact-root", str(artifact)])
                self.assertEqual(readme.read_bytes(), before)
                self.assertFalse((root / "site").exists())

    def test_missing_invalid_or_symlinked_detailed_metadata_is_rejected_before_writes(
        self,
    ):
        for failure in (
            "missing",
            "symlink",
            "not-object",
            "python-files",
            "native-files",
            "native-row",
            "native-path",
        ):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                args, readme = files(root)
                before = readme.read_bytes()
                artifact = root / "artifact"
                detailed_reports(artifact)
                path = artifact / "python/coverage.json"
                if failure == "missing":
                    path.unlink()
                elif failure == "symlink":
                    path.unlink()
                    path.symlink_to(root / "report.json")
                elif failure == "not-object":
                    path.write_text("[]")
                elif failure == "python-files":
                    path.write_text('{"files": []}')
                else:
                    values = {
                        "native-files": {},
                        "native-row": [None],
                        "native-path": [{"file": None}],
                    }
                    (artifact / "native/native.json").write_text(
                        json.dumps({"files": values[failure]})
                    )
                with self.assertRaises(ValueError):
                    PUBLISH.main(args + ["--artifact-root", str(artifact)])
                self.assertEqual(readme.read_bytes(), before)
                self.assertFalse((root / "site").exists())

    def test_untrusted_cli_links_or_stale_outputs_are_rejected_without_writes(self):
        for option, value in (
            ("--repository", "../repository"),
            ("--revision", "short"),
            ("--run-url", "https://github.com/other/project/actions/runs/123"),
        ):
            with (
                self.subTest(option=option),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                args, readme = files(root)
                before = readme.read_bytes()
                args[args.index(option) + 1] = value
                with self.assertRaises(ValueError):
                    PUBLISH.main(args)
                self.assertEqual(readme.read_bytes(), before)
                self.assertFalse((root / "site").exists())
        for output in ("nonempty", "symlink"):
            with (
                self.subTest(output=output),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                args, _ = files(root)
                if output == "nonempty":
                    (root / "site").mkdir()
                    (root / "site/.env").write_text("private")
                else:
                    (root / "empty").mkdir()
                    (root / "site").symlink_to(root / "empty", target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "empty"):
                    PUBLISH.main(args)

    def test_entrypoint_help_exposes_the_ci_cli(self):
        with (
            patch.object(sys, "argv", ["publisher", "--help"]),
            contextlib.redirect_stdout(io.StringIO()) as output,
            self.assertRaises(SystemExit) as status,
        ):
            runpy.run_path(str(SOURCE), run_name="__main__")
        self.assertEqual(status.exception.code, 0)
        self.assertIn("--artifact-root", output.getvalue())


if __name__ == "__main__":
    unittest.main()
