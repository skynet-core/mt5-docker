"""Enforce 94% executable-line coverage separately for every maintained source."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from coverage import Coverage, CoverageData

ROOT = Path(__file__).resolve().parents[1]
MINIMUM = 94
# GCC emits no executable rows for these declarations; a source edit requires re-audit.
DECLARATION_ONLY = {
    "tests/native/fake-wayland.h": {
        "sha256": "28f86f8b06542402abee1be503553c3e989ad4aea77184b338e36d20f2c86efd",
        "reason": "Declarations only; GCC emits no executable lines; input-unit tests the member defaults",
    }
}


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def inventory(root=ROOT):
    groups = load_module(ROOT / "build/lint.py", "source_inventory").sources(root)
    return {
        name.removeprefix("./"): kind
        for kind, names in groups.items()
        for name in names
    }


def manifest(root=ROOT):
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in inventory(root)
    }


def verify_sources(paths, root=ROOT):
    current = manifest(root)
    if not paths:
        raise ValueError(
            "A source manifest is required; coverage from unknown revisions is rejected"
        )
    for path in paths:
        if json.loads(Path(path).read_text()) != current:
            raise ValueError(f"Source files changed after coverage collection: {path}")


def combine_python(paths, destination):
    merged = CoverageData(basename=str(destination))
    for path in paths:
        incoming = CoverageData(basename=str(path))
        incoming.read()
        merged.update(incoming)
    merged.write()


def python_metrics(root, names, data_file):
    coverage = Coverage(config_file=str(ROOT / ".coveragerc"), data_file=str(data_file))
    coverage.load()
    result = {}
    for name in names:
        _, statements, excluded, missing, _ = coverage.analysis2(str(root / name))
        if excluded:
            raise ValueError(
                f"Owned Python source has excluded executable lines: {name}"
            )
        result[name] = {
            "executable_lines": statements,
            "covered_lines": sorted(set(statements) - set(missing)),
        }
    return result


def native_metrics(paths, root=ROOT, owned=None):
    owned = inventory(root) if owned is None else owned
    result = {}
    for path in paths:
        for source in json.loads(Path(path).read_text())["files"]:
            name = source["file"].removeprefix("/source/").removeprefix("./")
            if name.startswith(str(root) + "/"):
                name = name[len(str(root)) + 1 :]
            if owned.get(name) != "c++":
                continue
            entry = result.setdefault(
                name, {"executable_lines": set(), "covered_lines": set()}
            )
            for line in source["lines"]:
                if line.get("gcovr/excluded"):
                    raise ValueError(f"Native executable line was excluded: {name}")
                number = line["line_number"]
                if (
                    not isinstance(line["count"], int)
                    or isinstance(line["count"], bool)
                    or line["count"] < 0
                ):
                    raise ValueError(f"Invalid native coverage count: {name}")
                entry["executable_lines"].add(number)
                if line["count"] > 0:
                    entry["covered_lines"].add(number)
    return result


def evaluate(owned, metrics, root=ROOT):
    rows = []
    for name, kind in sorted(owned.items()):
        measured = name in metrics
        values = metrics.get(name, {"executable_lines": [], "covered_lines": []})
        if any(
            not isinstance(number, int) or isinstance(number, bool)
            for key in ("executable_lines", "covered_lines")
            for number in values[key]
        ):
            raise ValueError(f"Invalid coverage line positions: {name}")
        executable, covered = (
            set(values["executable_lines"]),
            set(values["covered_lines"]),
        )
        maximum = len((root / name).read_text().splitlines())
        if not covered <= executable or any(
            not isinstance(number, int)
            or isinstance(number, bool)
            or number < 1
            or number > maximum
            for number in executable
        ):
            raise ValueError(f"Invalid coverage line positions: {name}")
        declaration = DECLARATION_ONLY.get(name)
        if declaration and (
            kind != "c++"
            or executable
            or hashlib.sha256((root / name).read_bytes()).hexdigest()
            != declaration["sha256"]
        ):
            raise ValueError(f"Declaration-only source requires re-audit: {name}")
        percent = (
            None
            if declaration
            else (
                100 * len(covered) / len(executable) if measured and executable else 0
            )
        )
        rows.append(
            {
                "file": name,
                "language": kind,
                "measured": measured,
                "executable": len(executable),
                "covered": len(covered),
                "percent": percent,
                "passed": bool(declaration) or (measured and percent >= MINIMUM),
                "declaration_only": declaration["reason"] if declaration else None,
                "missing_lines": sorted(executable - covered),
            }
        )
    return {
        "minimum_per_file": MINIMUM,
        "passed": bool(rows) and all(row["passed"] for row in rows),
        "files": rows,
    }


def write_report(data, output):
    output.mkdir(parents=True, exist_ok=True)
    (output / "coverage.json").write_text(json.dumps(data, indent=2) + "\n")
    lines = [
        "# Per-file source coverage",
        "",
        f"Required: **{MINIMUM}% for every maintained file**. Unmeasured files fail.",
        "",
        "| Source | Language | Covered / executable | Coverage | Result | Missing lines |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in data["files"]:
        missing = row.get("declaration_only") or ", ".join(
            map(str, row["missing_lines"])
        )
        percent = "N/A" if row["percent"] is None else f"{row['percent']:.2f}%"
        lines.append(
            f"| {row['file']} | {row['language']} | {row['covered']}/{row['executable']} | {percent} | {'PASS' if row['passed'] else 'FAIL'} | {missing} |"
        )
    (output / "INDEX.md").write_text("\n".join(lines) + "\n")


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/coverage")
    parser.add_argument("--record-sources", type=Path)
    parser.add_argument("--source-manifest", type=Path, nargs="*", default=[])
    parser.add_argument("--python-data", type=Path, nargs="*", default=[])
    parser.add_argument("--native-report", type=Path, nargs="*", default=[])
    parser.add_argument("--shell-traces", type=Path)
    parser.add_argument("--shell-map", type=Path)
    args = parser.parse_args(arguments)
    if args.record_sources:
        args.record_sources.parent.mkdir(parents=True, exist_ok=True)
        args.record_sources.write_text(json.dumps(manifest(), indent=2) + "\n")
        return 0
    verify_sources(args.source_manifest)
    owned = inventory()
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / ".coverage"
    combine_python(args.python_data, destination)
    metrics = python_metrics(
        ROOT, [name for name, kind in owned.items() if kind == "python"], destination
    )
    metrics.update(native_metrics(args.native_report, owned=owned))
    if args.shell_traces:
        shell = load_module(ROOT / "tests/shell_coverage.py", "shell_report")
        for row in shell.report(ROOT, args.shell_traces, args.shell_map)["files"]:
            metrics[row["file"]] = row
    result = evaluate(owned, metrics)
    write_report(result, args.output)
    for row in result["files"]:
        if not row["passed"]:
            print(f"FAIL {row['file']}: {row['percent']:.2f}% (required {MINIMUM}%)")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
