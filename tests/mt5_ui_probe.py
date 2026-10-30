"""Read MT5 GUI state in Wine. Input must arrive through the real display transport."""

import ctypes
import json
import sys
from ctypes import wintypes


class GUIThreadInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        *[
            (name, wintypes.HWND)
            for name in (
                "hwndActive",
                "hwndFocus",
                "hwndCapture",
                "hwndMenuOwner",
                "hwndMoveSize",
                "hwndCaret",
            )
        ],
        ("rcCaret", wintypes.RECT),
    ]


def thread_state(thread, user=None):
    user = user or ctypes.windll.user32
    query = user.GetGUIThreadInfo
    query.argtypes = (wintypes.DWORD, ctypes.POINTER(GUIThreadInfo))
    query.restype = wintypes.BOOL
    info = GUIThreadInfo(cbSize=ctypes.sizeof(GUIThreadInfo))
    if query(thread, ctypes.byref(info)):
        return {"focus": info.hwndFocus, "menu_mode": bool(info.flags & 0x4)}
    return {"focus": None, "menu_mode": None}


def physical_desktop(user=None):
    user = user or ctypes.windll.user32
    awareness = user.SetThreadDpiAwarenessContext
    awareness.argtypes = (wintypes.HANDLE,)
    awareness.restype = wintypes.HANDLE
    if not awareness(ctypes.c_void_p(-4)):
        raise RuntimeError("Cannot disable observer DPI virtualization")
    cursor = user.GetPhysicalCursorPos
    cursor.argtypes = (ctypes.POINTER(wintypes.POINT),)
    cursor.restype = wintypes.BOOL
    point = wintypes.POINT()
    if not cursor(ctypes.byref(point)):
        raise RuntimeError("Cannot read physical cursor position")
    return {
        "pointer": [point.x, point.y],
        "desktop": [user.GetSystemMetrics(index) for index in (76, 77, 78, 79)],
    }


def edit_text(window, user=None):
    user = user or ctypes.windll.user32
    send = user.SendMessageTimeoutW
    send.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    )
    send.restype = ctypes.c_size_t
    buffer = ctypes.create_unicode_buffer(4096)
    result = ctypes.c_size_t()
    # WM_GETTEXT reads a foreign edit control; no Win32 input messages are sent.
    if not send(
        window,
        0x000D,
        len(buffer),
        ctypes.cast(buffer, ctypes.c_void_p).value,
        3,
        2000,
        ctypes.byref(result),
    ):
        if not user.IsWindow(window):
            return None
        raise RuntimeError("MT5 edit control did not answer WM_GETTEXT")
    return buffer.value


def window_dpi(window, user=None):
    user = user or ctypes.windll.user32
    query = user.GetDpiForWindow
    query.argtypes = (wintypes.HWND,)
    query.restype = wintypes.UINT
    dpi = query(window)
    if dpi <= 0:
        raise RuntimeError("Cannot read MT5 window DPI")
    return dpi


def file_menu_fallback(window, gui, user=None):
    """Locate MT5's menu when Wine cannot inspect its foreign HMENU."""
    user = user or ctypes.windll.user32
    metric = user.GetSystemMetricsForDpi
    metric.argtypes = (ctypes.c_int, wintypes.UINT)
    metric.restype = ctypes.c_int
    dpi = window_dpi(window, user)
    height = metric(15, dpi)  # SM_CYMENU; the client starts below the menu bar.
    width = round(34 * dpi / 96)
    if height <= 0 or width <= 0:
        raise RuntimeError("MT5 menu dimensions must be positive")
    left, bottom = gui.ClientToScreen(window, (0, 0))
    return [left, bottom - height, left + width, bottom]


def snapshot(expected="", gui=None, process=None, read_text=edit_text):
    physical = {"pointer": None, "desktop": None}
    read_focus = gui is None
    if gui is None:
        import win32gui as gui
        import win32process as process

        physical = physical_desktop()

    windows = []
    gui.EnumWindows(
        lambda window, _: (
            windows.append(window) if gui.IsWindowVisible(window) else None
        ),
        None,
    )
    main = next(
        (
            window
            for window in windows
            if gui.GetClassName(window) == "MetaQuotes::MetaTrader::5.00"
        ),
        None,
    )
    result = {
        "main": main,
        "menus": [],
        "dialogs": [],
        "controls": [],
        **physical,
    }
    if main is None:
        return result
    pid = process.GetWindowThreadProcessId(main)[1]
    result.update(
        pid=pid,
        main_dpi=window_dpi(main) if read_focus else None,
        main_rect=gui.GetWindowRect(main),
        main_enabled=bool(gui.IsWindowEnabled(main)),
        foreground=gui.GetForegroundWindow(),
        **(
            thread_state(process.GetWindowThreadProcessId(main)[0])
            if read_focus
            else {"focus": None, "menu_mode": None}
        ),
    )
    try:
        valid, rectangle = gui.GetMenuItemRect(main, gui.GetMenu(main), 0)
    except Exception:
        valid, rectangle = False, None
    result["file_rect"] = rectangle if valid else file_menu_fallback(main, gui)
    for window in windows:
        if process.GetWindowThreadProcessId(window)[1] != pid:
            continue
        name = gui.GetClassName(window)
        if name == "#32768":
            result["menus"].append(window)
            continue
        if window != main:
            result["dialogs"].append(
                {"hwnd": window, "class": name, "rect": gui.GetWindowRect(window)}
            )
        children = []
        gui.EnumChildWindows(
            window, lambda child, _, children=children: children.append(child), None
        )
        for child in children:
            if not gui.IsWindowVisible(child) or not gui.IsWindowEnabled(child):
                continue
            kind = gui.GetClassName(child)
            control = {
                "hwnd": child,
                "root": window,
                "class": kind,
                "id": gui.GetDlgCtrlID(child),
                "rect": gui.GetWindowRect(child),
            }
            if (
                kind == "Button"
                and gui.GetWindowText(child).replace("&", "").casefold() == "cancel"
            ):
                control["cancel"] = True
            elif kind == "Edit" and not gui.GetWindowLong(child, -16) & 0x20:
                text = read_text(child)
                if text is None:
                    continue
                control.update(
                    edit=True,
                    matches=bool(expected) and text == expected,
                    text=text if text == expected else None,
                )
            else:
                continue
            result["controls"].append(control)
    return result


def serve(source=sys.stdin, destination=sys.stdout):
    for line in source:
        request = json.loads(line)
        destination.write(json.dumps(snapshot(request.get("expected", ""))) + "\n")
        destination.flush()


if __name__ == "__main__":
    serve()
