"""Keep shell path-rewrite fixtures attributable to their unchanged source lines."""

import hashlib
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path


def plain_paths(before, after):
    return all(re.fullmatch(r"/[\w./@+=:,%~-]*", value) for value in (before, after))


def copy_shell(source, destination, replacements=()):
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    original = source.read_text()
    content = original
    for before, after in replacements:
        if "\n" in before + after or "\r" in before + after:
            raise ValueError("Shell coverage rewrites must preserve source lines")
        if not plain_paths(before, after):
            raise ValueError("Shell coverage rewrites require plain absolute paths")
        content = content.replace(before, after)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content)
    if mapping := os.environ.get("MT5_SHELL_COVERAGE_MAP"):
        record = {
            "source": str(source),
            "copy": str(destination),
            "source_sha256": hashlib.sha256(original.encode()).hexdigest(),
            "copy_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "replacements": list(replacements),
        }
        with Path(mapping).open("a") as stream:
            stream.write(json.dumps(record) + "\n")
    return destination


def command_locations(content):
    tree = json.loads(
        subprocess.check_output(
            ["shfmt", "-ln", "bash", "--to-json"], input=content.encode()
        )
    )
    lines = set()
    assignment_ends = {}

    def visit(node):
        if isinstance(node, dict):
            if node.get("Type") in {
                "CallExpr",
                "TestClause",
                "ArithmCmd",
                "DeclClause",
                "LetClause",
                "ForClause",
                "CaseClause",
            }:
                lines.add(node["Pos"]["Line"])
            if (
                node.get("Type") == "CallExpr"
                and node.get("Assigns")
                and not node.get("Args")
            ):
                start, end = node["Pos"]["Line"], node["End"]["Line"]
                if end > start:
                    assignment_ends.setdefault(end, set()).add(start)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(tree)
    return lines, assignment_ends


def executable_lines(content):
    return command_locations(content)[0]


def report(root, trace_dir, map_path=None):
    root = Path(root).resolve()
    spec = importlib.util.spec_from_file_location(
        "shell_coverage_inventory",
        Path(__file__).resolve().parents[1] / "build/lint.py",
    )
    inventory = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inventory)
    syntax = {
        (root / name).resolve(): command_locations((root / name).read_text())
        for name in inventory.sources(root)["shell"]
    }
    files = {source: locations[0] for source, locations in syntax.items()}
    aliases = {}
    if map_path and Path(map_path).exists():
        for line in Path(map_path).read_text().splitlines():
            item = json.loads(line)
            source = Path(item["source"]).resolve()
            if source not in files:
                continue
            original = source.read_text()
            if hashlib.sha256(original.encode()).hexdigest() != item["source_sha256"]:
                raise ValueError(
                    "Canonical shell source changed after fixture execution"
                )
            rewritten = original
            for before, after in item["replacements"]:
                if not plain_paths(before, after):
                    raise ValueError("Unverifiable shell fixture rewrite")
                rewritten = rewritten.replace(before, after)
            if hashlib.sha256(rewritten.encode()).hexdigest() != item[
                "copy_sha256"
            ] or len(rewritten.splitlines()) != len(original.splitlines()):
                raise ValueError("Shell fixture hash or source lines do not match")
            copy = Path(item["copy"]).resolve()
            if copy.exists() and copy.read_text() != rewritten:
                raise ValueError(
                    "Executed shell fixture differs from its registered copy"
                )
            if copy in aliases and aliases[copy] != source:
                raise ValueError("Ambiguous shell source-copy mapping")
            aliases[copy] = source
    hits = {source: set() for source in files}
    for path in sorted(Path(trace_dir).glob("*.trace")):
        fields = path.read_bytes().split(b"\0")
        if fields.pop() != b"" or len(fields) % 2:
            raise ValueError("Incomplete shell location trace")
        for name, line in zip(fields[::2], fields[1::2], strict=True):
            source = Path(os.fsdecode(name))
            source = (root / source).resolve()
            source = aliases.get(source, source)
            number = int(line)
            if source in hits:
                if number < 1 or number > len(source.read_text().splitlines()):
                    raise ValueError("Shell location is outside its canonical source")
                # Bash reports the closing line for multiline assignments. Only
                # credit that assignment's start, never adjacent/inner commands.
                hits[source].update(syntax[source][1].get(number, {number}))
    return {
        "files": [
            {
                "file": source.relative_to(root).as_posix(),
                "executable_lines": sorted(lines),
                "covered_lines": sorted(lines & hits[source]),
            }
            for source, lines in sorted(files.items())
        ]
    }
