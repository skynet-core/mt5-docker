"""Lint all repository sources, including vendored and extensionless scripts."""

import os
import re
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def language(path):
    if path.suffix in {".sh", ".bash", ".ksh"}:
        return "shell"
    if path.suffix in {".py", ".pyi"}:
        return "python"
    if path.suffix in {".cpp", ".cc", ".cxx", ".C", ".h", ".hpp", ".hh", ".hxx"}:
        return "c++"
    with path.open("rb") as stream:
        first_line = stream.readline().decode("utf-8", errors="replace").strip()
    if first_line == "# /// script":
        return "python"
    if first_line.startswith("#!"):
        interpreters = [Path(word).name for word in shlex.split(first_line[2:])]
        if any(word in {"sh", "bash", "dash", "ksh"} for word in interpreters):
            return "shell"
        if any(re.fullmatch(r"python[\d.]*", word) for word in interpreters):
            return "python"
    return None


def sources(root=ROOT):
    files = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
    )
    groups = {"shell": [], "python": [], "c++": []}
    for name in sorted(set(os.fsdecode(files).split("\0")) - {""}):
        path = root / name
        if path.is_file() and (kind := language(path)):
            groups[kind].append(f"./{name}")
    return groups


def main():
    groups = sources()
    checks = {
        "shell": ["shellcheck"],
        "python": ["ruff", "check"],
        "c++": [
            "cppcheck",
            "--enable=warning,performance,portability",
            "--error-exitcode=1",
            "--library=qt",
            "--std=c++17",
            "--language=c++",
        ],
    }
    failed = False
    for kind, command in checks.items():
        print(f"\n{kind}: {len(groups[kind])} source files", flush=True)
        try:
            subprocess.run([command[0], "--version"], cwd=ROOT, check=True)
            print(shlex.join(command + groups[kind]), flush=True)
            result = subprocess.run(command + groups[kind], cwd=ROOT, check=False)
            failed |= result.returncode != 0
        except (OSError, subprocess.CalledProcessError) as error:
            print(f"{command[0]} failed: {error}; run uv sync --locked", flush=True)
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
