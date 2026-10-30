"""Publish passing coverage metadata and static reports without raw CI evidence."""

import argparse
import html
import importlib.util
import json
import math
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit, urlunsplit

START = "<!-- coverage-report:start -->"
END = "<!-- coverage-report:end -->"
LANGUAGES = {"python": "Python", "shell": "Bash", "c++": "C++"}
ASSETS = {".html", ".css", ".js", ".png", ".svg", ".ico", ".woff", ".woff2"}
ROOT = Path(__file__).resolve().parents[1]
POLICY_SPEC = importlib.util.spec_from_file_location(
    "coverage_policy", ROOT / "build/lint.py"
)
POLICY = importlib.util.module_from_spec(POLICY_SPEC)
POLICY_SPEC.loader.exec_module(POLICY)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value):
    return type(value) is int and value >= 0


def source_path(value):
    require(
        isinstance(value, str) and value and "\\" not in value, "Invalid source path"
    )
    path = PurePosixPath(value)
    require(
        bool(path.parts)
        and not path.is_absolute()
        and ".." not in path.parts
        and path.as_posix() == value
        and not any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ),
        "Invalid source path",
    )
    return value


def validate_report(data):
    require(isinstance(data, dict), "Coverage report must be a JSON object")
    minimum = data.get("minimum_per_file")
    require(
        type(minimum) in (int, float)
        and math.isfinite(minimum)
        and 94 <= minimum <= 100,
        "Coverage threshold must be at least 94 percent",
    )
    require(
        data.get("passed") is True
        and isinstance(data.get("files"), list)
        and data["files"],
        "Coverage report must pass and contain files",
    )
    rows, names = [], set()
    for original in data["files"]:
        require(isinstance(original, dict), "File result must be a JSON object")
        name = source_path(original.get("file"))
        require(
            POLICY.coverage_source(name),
            "Test sources cannot appear in public coverage",
        )
        require(name not in names, "Duplicate source file")
        names.add(name)
        kind = original.get("language")
        require(
            kind in LANGUAGES
            and original.get("passed") is True
            and type(original.get("measured")) is bool,
            "Invalid language or file result",
        )
        executable, covered = original.get("executable"), original.get("covered")
        require(
            integer(executable) and integer(covered) and covered <= executable,
            "Invalid executable or covered count",
        )
        missing = original.get("missing_lines")
        require(
            isinstance(missing, list)
            and all(integer(line) and line > 0 for line in missing)
            and missing == sorted(set(missing))
            and len(missing) == executable - covered,
            "Invalid missing-line positions",
        )
        percent, audit = original.get("percent"), original.get("declaration_only")
        if percent is None:
            require(
                kind == "c++"
                and executable == covered == 0
                and isinstance(audit, str)
                and audit.strip(),
                "N/A requires an audited declaration-only source",
            )
        else:
            require(
                original["measured"]
                and executable > 0
                and audit is None
                and type(percent) in (int, float)
                and math.isfinite(percent)
                and minimum <= percent <= 100
                and math.isclose(percent, 100 * covered / executable, abs_tol=1e-8),
                "Invalid or insufficient per-file percentage",
            )
        rows.append(
            {
                "file": name,
                "language": kind,
                "measured": original["measured"],
                "executable": executable,
                "covered": covered,
                "percent": percent,
                "passed": True,
                "declaration_only": audit,
                "missing_lines": missing,
            }
        )
    return {
        "minimum_per_file": minimum,
        "passed": True,
        "files": sorted(rows, key=lambda row: row["file"]),
    }


def public_url(value):
    url = urlsplit(value)
    require(
        url.scheme == "https"
        and url.hostname
        and not url.username
        and not url.password
        and not url.query
        and not url.fragment
        and ".." not in PurePosixPath(unquote(url.path)).parts
        and not any(character.isspace() for character in value),
        "Expected a public HTTPS URL without credentials or query parameters",
    )
    path = unquote(url.path)
    require(
        re.fullmatch(r"[A-Za-z0-9.-]+(?::443)?", url.netloc)
        and not any(ord(character) < 32 for character in path),
        "Invalid public URL host or path",
    )
    return urlunsplit(("https", url.netloc, quote(path, safe="/-._~"), "", "")).rstrip(
        "/"
    )


def summaries(data):
    result = []
    for kind, label in LANGUAGES.items():
        rows = [row for row in data["files"] if row["language"] == kind]
        if rows:
            percentages = [row["percent"] for row in rows if row["percent"] is not None]
            result.append(
                (
                    label,
                    len(rows),
                    f"{min(percentages):.2f}%" if percentages else "N/A",
                    sum(row["percent"] is None for row in rows),
                )
            )
    return result


def markdown(value):
    value = html.escape(str(value)).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\|\[\]*_`])", r"\\\1", value)


def source_url(repository, revision, name):
    return f"https://github.com/{repository}/blob/{revision}/{quote(name, safe='/')}"


def readme_block(data, repository, revision, run_url, pages_url):
    lines = [
        f"[Full HTML coverage report]({pages_url}/) · [tested commit `{revision[:12]}`](https://github.com/{repository}/commit/{revision}) · [CI run]({run_url})",
        "",
        f"Required: **{data['minimum_per_file']:g}% for every executable source file**. Branch coverage is available in the detailed reports.",
        "",
        "| Language | Files | Minimum line coverage | Required | Passed | Audited N/A |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, count, minimum, audits in summaries(data):
        lines.append(
            f"| {label} | {count} | {minimum} | {data['minimum_per_file']:g}% | {count}/{count} | {audits} |"
        )
    lines += [
        "",
        "<details>",
        f"<summary>All {len(data['files'])} source files</summary>",
        "",
        "| Source | Language | Covered / executable | Line coverage | Missing lines or audit |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for row in data["files"]:
        url = source_url(repository, revision, row["file"])
        percent = "N/A" if row["percent"] is None else f"{row['percent']:.2f}%"
        gaps = (
            markdown(row["declaration_only"])
            if row["percent"] is None
            else ", ".join(f"[{line}]({url}#L{line})" for line in row["missing_lines"])
        )
        lines.append(
            f"| [{markdown(row['file'])}]({url}) | {LANGUAGES[row['language']]} | {row['covered']}/{row['executable']} | {percent} | {gaps} |"
        )
    return "\n".join(lines + ["", "</details>"])


def replace_readme(content, block):
    openings = re.findall(r"<!--\s*coverage-report\b", content)
    markers = re.findall(r"<!--\s*coverage-report\b.*?-->", content, re.DOTALL)
    require(
        len(openings) == 2 and markers == [START, END],
        "README must contain exactly one valid coverage marker pair",
    )
    start, end = content.index(START) + len(START), content.index(END)
    return content[:start] + "\n" + block + "\n" + content[end:]


def asset_files(root):
    if root is None:
        return []
    for name in ("python/coverage.json", "native/native.json"):
        report = root / name
        require(
            report.is_file()
            and not report.is_symlink()
            and report.resolve().is_relative_to(root.resolve()),
            f"Missing or unsafe detailed coverage metadata: {name}",
        )
        data = json.loads(report.read_text())
        require(isinstance(data, dict), "Invalid detailed coverage metadata")
        files = data.get("files")
        if name.startswith("python/"):
            require(isinstance(files, dict), "Invalid Python coverage sources")
            names = files
        else:
            require(
                isinstance(files, list) and all(isinstance(row, dict) for row in files),
                "Invalid native coverage sources",
            )
            names = [row.get("file") for row in files]
        for value in names:
            require(isinstance(value, str), "Invalid detailed coverage source")
            source = source_path(
                value.removeprefix("/source/")
                .removeprefix(str(ROOT) + "/")
                .removeprefix("./")
            )
            require(
                POLICY.coverage_source(source),
                "Test sources cannot appear in detailed coverage",
            )
    result = []
    for folder, index in (("python/html", "index.html"), ("native", "native.html")):
        source = root / folder
        require(
            source.is_dir() and not source.is_symlink() and (source / index).is_file(),
            f"Missing static coverage report: {folder}/{index}",
        )
        for path in sorted(source.rglob("*")):
            require(
                not path.is_symlink() and path.resolve().is_relative_to(root.resolve()),
                "Coverage assets must not contain symlinks or escape their root",
            )
            if (
                path.is_file()
                and path.suffix in ASSETS
                and (folder == "python/html" or path.name.startswith("native"))
            ):
                result.append((path, Path(folder) / path.relative_to(source)))
    return result


class NativeNavigation(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "a" and {"nav", "index"} <= set((values.get("class") or "").split()):
            self.links.append((self.get_starttag_text(), values.get("href")))


def native_navigation(content, source, root, available):
    parser = NativeNavigation()
    parser.feed(content)
    for tag, href in parser.links:
        if href is None:
            continue
        url = urlsplit(href)
        require(
            url.scheme in ("", "http", "https"),
            "Unsafe native report navigation scheme",
        )
        if url.scheme or url.netloc or not url.path:
            continue
        path = unquote(url.path)
        require(
            "\\" not in path
            and not PurePosixPath(path).is_absolute()
            and not any(ord(character) < 32 for character in path),
            "Unsafe native report navigation",
        )
        target = (source.parent / path).resolve()
        require(
            target.is_relative_to(root.resolve()),
            "Native navigation escapes its report directory",
        )
        if target not in available:
            fallback = (
                "../" * len(source.parent.relative_to(root).parts) + "native.html"
            )
            replacement = re.sub(
                r"(?<![\w:-])href\s*=\s*([\"']).*?\1",
                lambda _match, fallback=fallback: f'href="{fallback}"',
                tag,
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )
            content = content.replace(tag, replacement, 1)
    return content


def render_html(data, repository, revision, run_url, assets):
    escape = html.escape
    parts = [
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Per-file source coverage</title><style>body{font:16px system-ui,sans-serif;max-width:1300px;margin:2rem auto;padding:0 1rem;color:#172033;background:#f7f9fc}a{color:#0755a6}table{border-collapse:collapse;width:100%;background:white;margin:1rem 0}th,td{padding:.6rem;text-align:left;border:1px solid #d8e0eb;vertical-align:top}th{background:#e9eff7}h1{margin-bottom:.3rem}.scroll{overflow:auto}.audit{background:#fff7dc}.source{overflow-wrap:anywhere}</style><h1>Per-file source coverage</h1>',
        f'<p>All executable source files pass the {data["minimum_per_file"]:g}% minimum. Branches are reported separately.</p><p>Tested commit <a href="https://github.com/{repository}/commit/{revision}">{revision}</a> · <a href="{escape(run_url, quote=True)}">CI run</a> · <a href="coverage.json">Coverage metadata</a></p>',
    ]
    if assets:
        parts.append(
            '<p><a href="python/html/index.html">Python annotated source and branches</a> · <a href="native/native.html">C++ annotated source and branches</a>. Bash missing-line links below point to the tested source.</p>'
        )
    parts.append(
        "<table><thead><tr><th>Language</th><th>Files</th><th>Minimum line coverage</th><th>Passed</th><th>Audited N/A</th></tr></thead><tbody>"
    )
    for label, count, minimum, audits in summaries(data):
        parts.append(
            f"<tr><td>{label}</td><td>{count}</td><td>{minimum}</td><td>{count}/{count}</td><td>{audits}</td></tr>"
        )
    parts.append(
        '</tbody></table><h2>Every maintained source file</h2><div class="scroll"><table><thead><tr><th>Source</th><th>Language</th><th>Covered / executable</th><th>Line coverage</th><th>Missing lines or audit</th></tr></thead><tbody>'
    )
    for row in data["files"]:
        url = escape(source_url(repository, revision, row["file"]), quote=True)
        audit = row["percent"] is None
        percent = "N/A" if audit else f"{row['percent']:.2f}%"
        gaps = (
            escape(row["declaration_only"])
            if audit
            else ", ".join(
                f'<a href="{url}#L{line}">{line}</a>' for line in row["missing_lines"]
            )
        )
        row_class = ' class="audit"' if audit else ""
        parts.append(
            f'<tr{row_class}><td class="source"><a href="{url}">{escape(row["file"])}</a></td><td>{LANGUAGES[row["language"]]}</td><td>{row["covered"]}/{row["executable"]}</td><td>{percent}</td><td>{gaps}</td></tr>'
        )
    return "\n".join(parts + ["</tbody></table></div></html>"]) + "\n"


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--readme", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--pages-url", required=True)
    parser.add_argument("--artifact-root", type=Path)
    args = parser.parse_args(arguments)
    require(
        re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", args.repository
        ),
        "Invalid GitHub repository",
    )
    require(
        re.fullmatch(r"[0-9a-f]{40}", args.revision),
        "Expected a full tested commit SHA",
    )
    run_url, pages_url = public_url(args.run_url), public_url(args.pages_url)
    require(
        re.fullmatch(
            re.escape(f"https://github.com/{args.repository}/actions/runs/")
            + r"[0-9]+",
            run_url,
        ),
        "CI run URL must belong to the repository",
    )
    data = validate_report(json.loads(args.report.read_text()))
    readme = replace_readme(
        args.readme.read_bytes().decode("utf-8"),
        readme_block(data, args.repository, args.revision, run_url, pages_url),
    )
    assets = asset_files(args.artifact_root)
    available = {source.resolve() for source, _relative in assets}
    native_html = {
        source: native_navigation(
            source.read_bytes().decode("utf-8"),
            source,
            args.artifact_root / "native",
            available,
        )
        for source, relative in assets
        if relative.parts[0] == "native" and source.suffix == ".html"
    }
    require(
        not args.output.is_symlink()
        and (
            not args.output.exists()
            or args.output.is_dir()
            and not any(args.output.iterdir())
        ),
        "Output directory must be empty",
    )
    args.output.mkdir(parents=True, exist_ok=True)
    for source, relative in assets:
        target = args.output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source in native_html:
            target.write_bytes(native_html[source].encode("utf-8"))
        else:
            shutil.copyfile(source, target)
    (args.output / "index.html").write_text(
        render_html(data, args.repository, args.revision, run_url, assets)
    )
    (args.output / "coverage.json").write_text(
        json.dumps(
            {
                **data,
                "repository": args.repository,
                "revision": args.revision,
                "run_url": run_url,
            },
            indent=2,
        )
        + "\n"
    )
    args.readme.write_bytes(readme.encode("utf-8"))
    print(f"Published {len(data['files'])} coverage rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
