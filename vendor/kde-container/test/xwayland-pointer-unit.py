"""Build the actual callback TU using an existing Xwayland Meson SDK."""

import argparse
import hashlib
import json
import shlex
import subprocess
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--unit", choices=("pointer", "rootful-ready"), default="pointer"
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    options = parser.parse_args()
    source, build, output = (
        options.source.resolve(),
        options.build.resolve(),
        options.output.resolve(),
    )
    output.mkdir(parents=True, exist_ok=False)
    units = {
        "pointer": (
            "xwayland-pointer-unit.c",
            "xwayland-input.c",
            ("QueuePointerEvents",),
        ),
        "rootful-ready": (
            "xwayland-rootful-ready-unit.c",
            "xwayland-window.c",
            (
                "MakeAtom",
                "dixChangeWindowProperty",
                "xwl_randr_add_modes_fixed",
                "wl_proxy_get_version",
                "wl_proxy_marshal_flags",
                "libdecor_configuration_get_content_size",
                "libdecor_state_new",
                "libdecor_frame_commit",
                "libdecor_state_free",
            ),
        ),
    }
    fixture_name, source_name, wrap_symbols = units[options.unit]
    fixture = Path(__file__).with_name(fixture_name)
    actual = source / "hw/xwayland" / source_name
    database = build / "compile_commands.json"
    entries = [
        entry
        for entry in json.loads(database.read_text())
        if entry["file"].endswith("/" + source_name)
    ]
    assert len(entries) == 1, "Expected exactly one production unit compile command"
    entry = entries[0]
    directory = Path(entry["directory"])
    compile_command = entry.get("arguments") or shlex.split(entry["command"])
    assert (directory / entry["file"]).resolve() == actual
    command = list(compile_command)
    inputs = [
        index for index, word in enumerate(command) if word.endswith("/" + source_name)
    ]
    assert len(inputs) == 1
    command[inputs[0]] = str(fixture)
    command[command.index("-o") + 1] = str(output / "pointer-unit.o")
    for flag, replacement in (("-MF", "pointer-unit.d"), ("-MT", "pointer-unit.o")):
        if flag in command:
            command[command.index(flag) + 1] = str(output / replacement)
    assert not (fixture.parent / actual.name).exists(), (
        "Fixture must not shadow the SDK input source"
    )
    command.extend(["-iquote", str(actual.parent)])
    commands = subprocess.check_output(
        ["ninja", "-C", str(build), "-t", "commands", "hw/xwayland/Xwayland"], text=True
    )
    link = shlex.split(commands.splitlines()[-1])
    assert "-o" in link and link[link.index("-o") + 1] == "hw/xwayland/Xwayland"
    assert not any(word.startswith("@") for word in link), (
        "Inspect a response-file SDK before adapting the draft"
    )
    objects = [
        index for index, word in enumerate(link) if word.endswith(source_name + ".o")
    ]
    assert len(objects) == 1
    link[objects[0]] = str(output / "pointer-unit.o")
    main_libraries = [
        word for word in link if Path(word).name.endswith("libxserver_main.a")
    ]
    assert len(main_libraries) == 1
    link.remove(main_libraries[0])
    link[link.index("-o") + 1] = str(output / "pointer-unit")
    link.extend("-Wl,--wrap=" + symbol for symbol in wrap_symbols)
    receipt = {
        "complete": False,
        "source": str(actual),
        "source_sha256": digest(actual),
        "fixture_sha256": digest(fixture),
        "compile_database_sha256": digest(database),
        "production_compile": compile_command,
        "unit_compile": command,
        "unit_link": link,
        "unit": options.unit,
        "wrapped_boundaries": wrap_symbols,
        "boundary": "Actual complete Xwayland translation unit and original production libraries; only the explicitly recorded event/property/library boundaries are wrapped.",
    }
    (output / "COMMANDS.json").write_text(json.dumps(receipt, indent=2) + "\n")
    for name, argv, cwd in (
        ("compile", command, directory),
        ("link", link, build),
        ("run", [str(output / "pointer-unit")], build),
    ):
        result = subprocess.run(
            argv,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        (output / f"{name}.log").write_text(result.stdout)
        receipt[name + "_exit"] = result.returncode
        (output / "RESULT.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if result.returncode:
            print(result.stdout, end="")
            return 1
    receipt["complete"] = True
    (output / "RESULT.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print((output / "run.log").read_text(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
