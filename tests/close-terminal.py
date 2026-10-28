"""Send WM_CLOSE to MT5 and wait; executed with Windows Python inside Wine."""

import ctypes
from ctypes import wintypes

user = ctypes.windll.user32
kernel = ctypes.windll.kernel32
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
)
kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user.PostMessageW.argtypes = (
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
handles = []


@callback_type
def close(window, unused):
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(window, ctypes.byref(pid))
    handle = kernel.OpenProcess(0x101000, False, pid.value)
    if handle:
        name = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(name))
        if kernel.QueryFullProcessImageNameW(
            handle, 0, name, ctypes.byref(size)
        ) and name.value.lower().endswith("\\terminal64.exe"):
            user.PostMessageW(window, 0x0010, 0, 0)
            handles.append(handle)
        else:
            kernel.CloseHandle(handle)
    return True


user.EnumWindows(close, 0)
for handle in handles:
    status = kernel.WaitForSingleObject(handle, 30000)
    kernel.CloseHandle(handle)
    if status != 0:
        raise RuntimeError("MT5 did not close gracefully within 30 seconds")
