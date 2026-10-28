"""Index the exact display matrix and reject missing or incomplete GUI evidence."""

import argparse
import json
import re
import uuid
from pathlib import Path

from PIL import Image

DISTROS = ("fedora-44", "debian-13", "ubuntu-26.04", "archlinux")
LOGS = ("start.log", "services.log", "container.log", "units.log")
CAPTURE_METHODS = {"rfb", "parent-rfb", "kwin-screenshot2", "x11-root"}
OS_RELEASE = {
    "fedora-44": {"ID": "fedora", "VERSION_ID": "44"},
    "debian-13": {"ID": "debian", "VERSION_ID": "13"},
    "ubuntu-26.04": {"ID": "ubuntu", "VERSION_ID": "26.04"},
    "archlinux": {"ID": "arch"},
}


def profiles(backends=("wayland", "x11")):
    for backend in backends:
        for mode, host in (
            ("vnc", None),
            ("web", None),
            ("headless", None),
            ("nested", "wayland"),
            ("nested", "x11"),
        ):
            yield backend, mode, host


def case_path(backend, mode, host):
    path = Path(backend) / mode
    return path / f"on-{host}" if mode == "nested" else path


def read_result(path, errors):
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise TypeError("Result must be a JSON object")
        return value
    except (OSError, ValueError, TypeError) as error:
        errors.append(f"{path.name}: {error}")
        return {}


def check_os_release(actual, distro):
    return isinstance(actual, dict) and all(
        actual.get(key) == value for key, value in OS_RELEASE[distro].items()
    )


def replace_index(path, content):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    try:
        temporary.write_text(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def check_screenshot(path):
    with Image.open(path) as image:
        if image.format != "PNG" or image.width < 320 or image.height < 200:
            raise ValueError("Screenshot must be a PNG of at least 320x200")
        image.verify()
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        image = Image.alpha_composite(
            Image.new("RGBA", rgba.size, (0, 0, 0, 255)), rgba
        ).convert("RGB")
        image.thumbnail((160, 100))
        colors = image.getcolors(image.width * image.height) or []
        visible = sum(count for count, pixel in colors if max(pixel) > 32)
        if len(colors) < 3 or visible < image.width * image.height * 0.05:
            raise ValueError("Screenshot is blank")
        marker = sum(
            count
            for count, pixel in colors
            if max(abs(a - b) for a, b in zip(pixel, (18, 210, 178), strict=True)) < 15
        )
        if marker <= 50:
            raise ValueError("Screenshot does not contain the GUI fixture marker")
        return marker


def inspect_case(output, distro, backend, mode, host, image_id, run_errors):
    relative = Path(distro) / case_path(backend, mode, host)
    directory = output / relative
    errors = list(run_errors)
    report = read_result(directory / "results.json", errors)
    expected = {
        "image_id": image_id,
        "backend": backend,
        "mode": mode,
        "host_backend": host or "none",
        "evidence": case_path(backend, mode, host).as_posix(),
        "screenshot": "screenshot.png",
    }
    if report.get("passed") is not True:
        errors.append(
            "Case did not pass: " + str(report.get("error", "no success result"))
        )
    for key, value in expected.items():
        if report.get(key) != value:
            errors.append(f"{key} does not match {value!r}")
    if not check_os_release(report.get("os_release"), distro):
        errors.append(f"os_release does not match {OS_RELEASE[distro]!r}")
    if report.get("gui_rendered") is not True:
        errors.append("GUI rendering was not verified")
    capture_method = report.get("capture_method")
    if not isinstance(capture_method, str) or capture_method not in CAPTURE_METHODS:
        errors.append("Capture method does not identify real display output")
    if mode == "headless":
        if report.get("headless_private") is not True or report.get(
            "headless_services"
        ) != {
            "plasma-vnc.service": "inactive",
            "plasma-web.service": "inactive",
        }:
            errors.append("Headless VNC/web services were not verified inactive")
        native_capture = "kwin-screenshot2" if backend == "wayland" else "x11-root"
        if backend == "wayland" and capture_method == "x11-root":
            session = read_result(directory / "session.json", errors)
            backing = report.get("headless_backing_display")
            if (
                not isinstance(backing, str)
                or not re.fullmatch(r":11[01]", backing)
                or backing != session.get("PLASMA_SOFTWARE_DISPLAY")
                or any(
                    session.get(key) != value
                    for key, value in {
                        "DISPLAY_BACKEND": "wayland",
                        "DISPLAY_MODE": "headless",
                        "HOST_DISPLAY_BACKEND": "none",
                        "PLASMA_SOFTWARE_CAPTURE": "1",
                        "XAUTHORITY": "/etc/plasma/Xauthority",
                    }.items()
                )
            ):
                errors.append(
                    "Headless X11 capture did not identify its private backing output"
                )
            native_capture = "x11-root"
        if capture_method != native_capture:
            errors.append(
                "Headless screenshot did not use its native compositor capture"
            )
    if mode in ("vnc", "web") and report.get("rfb_authenticated") is not True:
        errors.append("Remote display authentication was not verified")
    for name in LOGS:
        if not (directory / name).is_file():
            errors.append(f"Missing {name}")
    marker = None
    try:
        marker = check_screenshot(directory / "screenshot.png")
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
        errors.append(f"screenshot.png: {error}")
    if mode != "headless":
        gui = report.get("gui_input")
        if not isinstance(gui, dict) or gui.get("passed") is not True:
            errors.append("GUI input did not pass")
        else:
            for key, value in (
                ("expected", "expected.txt"),
                ("saved", "typed.txt"),
                ("screenshot", "screenshot.png"),
            ):
                if gui.get(key) != value:
                    errors.append(f"GUI input {key} does not match {value!r}")
            if gui.get("method") != "rfb":
                errors.append("GUI input transport was not verified")
            try:
                token = (directory / "expected.txt").read_bytes()
                if not token or (directory / "typed.txt").read_bytes() != token:
                    errors.append("Saved GUI input does not match the expected text")
            except OSError as error:
                errors.append(f"GUI input evidence: {error}")
    return {
        "distro": distro,
        **expected,
        "image_id": report.get("image_id"),
        "capture_method": report.get("capture_method"),
        "os_release": report.get("os_release"),
        "headless_private": report.get("headless_private")
        if mode == "headless"
        else None,
        "marker_pixels": marker,
        "passed": not errors,
        "path": relative.as_posix(),
        "errors": errors,
    }


def index(output, distros=DISTROS):
    output = Path(output).resolve()
    cases = []
    run_errors = []
    for distro in distros:
        errors = []
        report = read_result(output / distro / "results.json", errors)
        image_id = report.get("image_id")
        if not isinstance(image_id, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", image_id
        ):
            errors.append("Distro result has no valid immutable image ID")
        if report.get("passed") is False:
            run_errors.append(f"{distro}: Distro run did not pass")
        elif report.get("passed") is not True:
            errors.append("Distro result has no valid pass/fail status")
        if "os_release" in report and not check_os_release(
            report["os_release"], distro
        ):
            errors.append("Distro OS metadata does not match the requested image")
        cases.extend(
            inspect_case(output, distro, backend, mode, host, image_id, errors)
            for backend, mode, host in profiles()
        )
    summary = {
        "expected_cases": len(distros) * 10,
        "passed_cases": sum(case["passed"] for case in cases),
        "passed": bool(cases)
        and not run_errors
        and all(case["passed"] for case in cases),
        "run_errors": run_errors,
        "cases": cases,
    }
    output.mkdir(parents=True, exist_ok=True)
    replace_index(output / "index.json", json.dumps(summary, indent=2) + "\n")
    lines = [
        "# Display matrix evidence",
        "",
        f"Passed {summary['passed_cases']} of {summary['expected_cases']} required cases.",
        "",
        "| Case | Result | Screenshot | Input proof | Logs |",
        "| --- | --- | --- | --- | --- |",
    ]
    failures = [f"- {error}" for error in run_errors]
    for case in cases:
        path = case["path"]
        directory = output / path
        screenshot = (
            f"[PNG]({path}/screenshot.png)"
            if (directory / "screenshot.png").is_file()
            else "missing"
        )
        logs = ", ".join(
            f"[{name}]({path}/{name})"
            if (directory / name).is_file()
            else f"missing {name}"
            for name in LOGS
        )
        inputs = (
            "—"
            if case["mode"] == "headless"
            else ", ".join(
                f"[{name}]({path}/{name})"
                if (directory / name).is_file()
                else f"missing {name}"
                for name in ("expected.txt", "typed.txt")
            )
        )
        verdict = "PASS" if case["passed"] else "FAIL"
        lines.append(
            f"| [{path}]({path}/results.json) | {verdict} | {screenshot} | {inputs} | {logs} |"
        )
        if case["errors"]:
            failures.append(f"- `{path}`: " + "; ".join(case["errors"]))
    if failures:
        lines.extend(["", "Failures:", "", *failures])
    replace_index(output / "INDEX.md", "\n".join(lines) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--distros", nargs="+", choices=DISTROS, default=DISTROS)
    options = parser.parse_args()
    if len(options.distros) != len(set(options.distros)):
        parser.error("Select each distribution once")
    result = index(options.output, options.distros)
    print(f"Display matrix: {result['passed_cases']}/{result['expected_cases']} passed")
    for error in result["run_errors"]:
        print(f"FAIL run: {error}")
    for case in result["cases"]:
        if not case["passed"]:
            print(f"FAIL {case['path']}: {'; '.join(case['errors'])}")
    print(f"Display evidence: {options.output.resolve() / 'INDEX.md'}")
    raise SystemExit(0 if result["passed"] else 1)
