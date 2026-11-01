"""Observe Market painting and activate headless Wine with real SendInput events."""

import base64
import json
import sys
import time
import urllib.request
from pathlib import Path


def page_ready(result):
    dom = result.get("dom", {})
    paints = {item.get("name") for item in dom.get("paint", [])}
    return (
        dom.get("url") == "file://terminal64.exe/market.html"
        and dom.get("title") == "Market"
        and dom.get("ready") == "complete"
        and dom.get("visible") == "visible"
        and all(
            text in dom.get("text", "") for text in ("All", "Most rated", "Price Down")
        )
        and {"first-paint", "first-contentful-paint"} <= paints
        and result.get("frames", {}).get("count", 0) > 0
    )


def native_input(action, *arguments):
    import ctypes
    from ctypes import wintypes

    sys.path.insert(0, str(Path(__file__).parent))
    from mt5_ui_probe import physical_desktop

    user = ctypes.WinDLL("user32", use_last_error=True)
    desktop = physical_desktop(user)["desktop"]

    class Mouse(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class Keyboard(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class Data(ctypes.Union):
        _fields_ = [("mi", Mouse), ("ki", Keyboard)]

    class Input(ctypes.Structure):
        _anonymous_ = ("data",)
        _fields_ = [("type", wintypes.DWORD), ("data", Data)]

    user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    user.SendInput.restype = wintypes.UINT

    def send(event):
        if user.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
            raise RuntimeError("Win32 SendInput did not accept the input event")

    def move(x, y):
        left, top, width, height = desktop
        send(
            Input(
                type=0,
                mi=Mouse(
                    round((x - left) * 65535 / (width - 1)),
                    round((y - top) * 65535 / (height - 1)),
                    0,
                    0x8001 | 0x4000,
                    0,
                    0,
                ),
            )
        )

    def key(keysym, down):
        vk = {0xFF0D: 13, 0xFFE1: 0xA0, 0xFFE3: 0xA2, 0xFFC1: 0x73}.get(keysym)
        if vk is None:
            vk = user.VkKeyScanW(keysym) & 255
        if vk == 255:
            raise RuntimeError("Unknown keyboard keysym")
        send(Input(type=1, ki=Keyboard(vk, 0, 0 if down else 2, 0, 0)))

    if action == "keys":
        for keysym, down in json.loads(arguments[0]):
            key(keysym, down)
            time.sleep(0.02)
    elif action == "key":
        key(*map(int, arguments))
    elif action in ("click", "drag"):
        values = list(map(int, arguments))
        x, y = values[:2]
        move(x, y)
        send(Input(type=0, mi=Mouse(0, 0, 0, 2, 0, 0)))
        time.sleep(0.08)
        if action == "drag":
            dx, dy = values[2:]
            for step in range(1, 11):
                move(x + round(dx * step / 10), y + round(dy * step / 10))
                time.sleep(0.04)
        send(Input(type=0, mi=Mouse(0, 0, 0, 4, 0, 0)))
    else:
        raise RuntimeError("Unknown native input action")
    return {"method": "Win32 SendInput", "passed": True}


def native(capture=False):
    import win32gui as gui

    sys.path.insert(0, str(Path(__file__).parent))
    from mt5_ui_probe import physical_desktop

    state = physical_desktop()
    windows = []
    gui.EnumWindows(lambda hwnd, _: windows.append(hwnd), None)
    main = next(
        hwnd
        for hwnd in windows
        if gui.IsWindowVisible(hwnd)
        and gui.GetClassName(hwnd) == "MetaQuotes::MetaTrader::5.00"
    )
    children = []
    gui.EnumChildWindows(main, lambda hwnd, _: children.append(hwnd), None)
    trees = [
        hwnd
        for hwnd in children
        if gui.IsWindowVisible(hwnd)
        and gui.GetClassName(hwnd) == "SysTreeView32"
        and gui.GetWindowText(gui.GetParent(hwnd)) == "Navigator"
    ]
    browsers = [
        hwnd
        for hwnd in children
        if gui.IsWindowVisible(hwnd)
        and gui.GetClassName(hwnd) == "Chrome_RenderWidgetHostHWND"
    ]
    state.update(
        main=main, foreground=gui.GetForegroundWindow(), title=gui.GetWindowText(main)
    )
    if len(trees) != 1:
        raise RuntimeError("MT5 has no unique visible Navigator tree")
    state["navigator"] = gui.GetWindowRect(trees[0])
    state["navigator_hwnd"] = trees[0]
    if len(browsers) == 1:
        state["browser"] = gui.GetWindowRect(browsers[0])
    markets = [
        hwnd
        for hwnd in children
        if gui.IsWindowVisible(hwnd)
        and gui.GetWindowText(hwnd) == "Market"
        and gui.GetClassName(hwnd).startswith("Afx:")
    ]
    if len(markets) == 1:
        state["market"] = gui.GetWindowRect(markets[0])
        client = gui.GetClientRect(markets[0])
        state["market_client"] = [
            *gui.ClientToScreen(markets[0], client[:2]),
            *gui.ClientToScreen(markets[0], client[2:]),
        ]
    if capture:
        from PIL import ImageGrab

        ImageGrab.grab(window=main).save(r"C:\MT5\market-window.png")
        if len(browsers) == 1:
            ImageGrab.grab(window=browsers[0]).save(r"C:\MT5\market-native.png")
    return state


def debug_policy():
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_LOCAL_MACHINE,
        r"Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments",
        0,
        winreg.KEY_READ | winreg.KEY_WRITE,
    ) as key:
        try:
            before, _kind = winreg.QueryValueEx(key, "terminal64.exe")
        except FileNotFoundError:
            before = ""
        arguments = before + " --remote-debugging-port=9222 --enable-automation"
        winreg.SetValueEx(key, "terminal64.exe", 0, winreg.REG_SZ, arguments.strip())
    return {"before": before, "diagnostic_arguments": arguments.strip()}


def observe(timeout=60):
    from websockets.sync.client import connect

    sequence = 0

    def read(path):
        with urllib.request.urlopen(
            "http://127.0.0.1:9222" + path, timeout=5
        ) as stream:
            return json.load(stream)

    def rpc(socket, method, params=None):
        nonlocal sequence
        sequence += 1
        socket.send(
            json.dumps({"id": sequence, "method": method, "params": params or {}})
        )
        while True:
            reply = json.loads(socket.recv(timeout=10))
            if reply.get("id") == sequence:
                if "error" in reply:
                    raise RuntimeError(f"CDP {method} failed: {reply['error']}")
                return reply["result"]

    expression = """({url:location.href,title:document.title,ready:document.readyState,
visible:document.visibilityState,text:document.body?.innerText,
paint:performance.getEntriesByType('paint').map(p=>({name:p.name,startTime:p.startTime})),
size:[innerWidth,innerHeight],dpr:devicePixelRatio})"""
    frames = """new Promise(resolve=>{let count=0;const end=performance.now()+500;
requestAnimationFrame(function tick(now){count++;if(now<end)requestAnimationFrame(tick)});
setTimeout(()=>resolve({count,visibility:document.visibilityState}),600)})"""
    deadline = time.monotonic() + timeout
    result = {}
    with Path(r"C:\MT5\market-cdp.jsonl").open("w") as log:
        while time.monotonic() < deadline:
            try:
                targets = [
                    item
                    for item in read("/json/list")
                    if item.get("url") == "file://terminal64.exe/market.html"
                ]
                if len(targets) != 1:
                    raise RuntimeError("Market has no unique CDP page")
                with connect(
                    targets[0]["webSocketDebuggerUrl"], open_timeout=5, close_timeout=2
                ) as page:
                    result = {
                        "target": targets[0],
                        "dom": rpc(
                            page,
                            "Runtime.evaluate",
                            {"expression": expression, "returnByValue": True},
                        )["result"]["value"],
                        "frames": rpc(
                            page,
                            "Runtime.evaluate",
                            {
                                "expression": frames,
                                "awaitPromise": True,
                                "returnByValue": True,
                            },
                        )["result"]["value"],
                    }
                    if page_ready(result):
                        screenshot = rpc(
                            page,
                            "Page.captureScreenshot",
                            {"format": "png", "captureBeyondViewport": False},
                        )
                        Path(r"C:\MT5\market-cdp.png").write_bytes(
                            base64.b64decode(screenshot["data"])
                        )
                        result["native"] = native(capture=True)
                        version = read("/json/version")
                        with connect(
                            version["webSocketDebuggerUrl"],
                            open_timeout=5,
                            close_timeout=2,
                        ) as browser:
                            result["browser_arguments"] = rpc(
                                browser, "Browser.getBrowserCommandLine"
                            )
                        return result
            except (OSError, RuntimeError) as error:
                result = {"not_ready": str(error)}
            log.write(json.dumps(result) + "\n")
            log.flush()
            time.sleep(0.5)
    native(capture=True)
    raise RuntimeError("Market never produced visible DOM, paint and animation frames")


if __name__ == "__main__":
    action = sys.argv[1]
    print(
        json.dumps(
            debug_policy()
            if action == "policy"
            else native_input(*sys.argv[2:])
            if action == "input"
            else native()
            if action == "native"
            else observe()
        )
    )
