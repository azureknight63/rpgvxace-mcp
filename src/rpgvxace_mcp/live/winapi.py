"""What `live` needs from Windows, through ctypes and nothing else.

Four jobs: is a process still running, end a game when this server ends,
find a game's windows (its main window, and any dialog it is showing), and
copy a window's pixels. Every function here answers sensibly off Windows --
an empty list, a False, a refusal -- so the rest of `live` and its tests
run anywhere; only the real Game.exe needs this machine.

Handles are declared as pointers before any call. ctypes assumes `int` for
a return value, which truncates a 64-bit handle and fails somewhere else
entirely.
"""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Any


IS_WINDOWS = sys.platform == "win32"


# The window class RGSS3's Game.exe registers for its game window, and the
# class every Win32 message box and RGSS error dialog has.
GAME_WINDOW_CLASS = "RGSS Player"
DIALOG_CLASS = "#32770"


# PrintWindow flags: the client area only, and ask the compositor for the
# rendered content -- without the second, a Direct3D window prints black.
_PW_CLIENTONLY = 0x1
_PW_RENDERFULLCONTENT = 0x2
_SRCCOPY = 0x00CC0020
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_ERROR_ACCESS_DENIED = 5
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
_WM_ACTIVATEAPP = 0x001C
_SW_SHOWNOACTIVATE = 4


if IS_WINDOWS:
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _HANDLE = wintypes.HANDLE
    _HWND = wintypes.HWND
    _HDC = wintypes.HDC
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, _HWND, wintypes.LPARAM)

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class _BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", _BITMAPINFOHEADER),
                    ("bmiColors", wintypes.DWORD * 3)]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation",
                     _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    def _declare(function: Any, restype: Any, *argtypes: Any) -> None:
        function.restype = restype
        function.argtypes = list(argtypes)

    _declare(_kernel32.OpenProcess, _HANDLE, wintypes.DWORD, wintypes.BOOL,
             wintypes.DWORD)
    _declare(_kernel32.GetExitCodeProcess, wintypes.BOOL, _HANDLE,
             ctypes.POINTER(wintypes.DWORD))
    _declare(_kernel32.CloseHandle, wintypes.BOOL, _HANDLE)
    _declare(_kernel32.CreateJobObjectW, _HANDLE, ctypes.c_void_p,
             wintypes.LPCWSTR)
    _declare(_kernel32.SetInformationJobObject, wintypes.BOOL, _HANDLE,
             ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    _declare(_kernel32.AssignProcessToJobObject, wintypes.BOOL, _HANDLE,
             _HANDLE)
    _declare(_user32.EnumWindows, wintypes.BOOL, _WNDENUMPROC,
             wintypes.LPARAM)
    _declare(_user32.EnumChildWindows, wintypes.BOOL, _HWND, _WNDENUMPROC,
             wintypes.LPARAM)
    _declare(_user32.GetWindowThreadProcessId, wintypes.DWORD, _HWND,
             ctypes.POINTER(wintypes.DWORD))
    _declare(_user32.IsWindowVisible, wintypes.BOOL, _HWND)
    _declare(_user32.GetClassNameW, ctypes.c_int, _HWND, wintypes.LPWSTR,
             ctypes.c_int)
    _declare(_user32.GetWindowTextW, ctypes.c_int, _HWND, wintypes.LPWSTR,
             ctypes.c_int)
    _declare(_user32.GetClientRect, wintypes.BOOL, _HWND,
             ctypes.POINTER(wintypes.RECT))
    _declare(_user32.GetDC, _HDC, _HWND)
    _declare(_user32.ReleaseDC, ctypes.c_int, _HWND, _HDC)
    _declare(_user32.PrintWindow, wintypes.BOOL, _HWND, _HDC, wintypes.UINT)
    _declare(_user32.PostMessageW, wintypes.BOOL, _HWND, wintypes.UINT,
             wintypes.WPARAM, wintypes.LPARAM)
    _declare(_user32.IsIconic, wintypes.BOOL, _HWND)
    _declare(_user32.ShowWindow, wintypes.BOOL, _HWND, ctypes.c_int)
    _declare(_gdi32.CreateCompatibleDC, _HDC, _HDC)
    _declare(_gdi32.DeleteDC, wintypes.BOOL, _HDC)
    _declare(_gdi32.CreateDIBSection, wintypes.HBITMAP, _HDC,
             ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
             ctypes.POINTER(ctypes.c_void_p), _HANDLE, wintypes.DWORD)
    _declare(_gdi32.SelectObject, wintypes.HGDIOBJ, _HDC, wintypes.HGDIOBJ)
    _declare(_gdi32.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
    _declare(_gdi32.BitBlt, wintypes.BOOL, _HDC, ctypes.c_int, ctypes.c_int,
             ctypes.c_int, ctypes.c_int, _HDC, ctypes.c_int, ctypes.c_int,
             wintypes.DWORD)
    _SET_DPI = getattr(_user32, "SetThreadDpiAwarenessContext", None)
    if _SET_DPI is not None:
        _declare(_SET_DPI, ctypes.c_void_p, ctypes.c_void_p)


def pid_alive(pid: int) -> bool:
    """Whether a process with this id is still running."""
    if not IS_WINDOWS:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False,
                                   pid)
    if not handle:
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        _kernel32.CloseHandle(handle)


class KillOnClose:
    """A job object that ends every process in it when this server ends.

    Without it, a server killed by its client -- which is how most MCP
    clients stop a server -- would leave the game running on the user's
    desktop with nobody to stop it. `atexit` does not run in that case;
    the kernel closing this handle does. Held for the life of the process,
    deliberately never closed by hand.
    """

    def __init__(self) -> None:
        self.handle = None
        if not IS_WINDOWS:
            return
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            return
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        if _kernel32.SetInformationJobObject(
                handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info), ctypes.sizeof(info)):
            self.handle = handle
        else:
            _kernel32.CloseHandle(handle)

    def adopt(self, pid: int) -> bool:
        """Put a process in the job. False if that was not possible."""
        if not self.handle:
            return False
        process = _kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not process:
            return False
        try:
            return bool(_kernel32.AssignProcessToJobObject(self.handle,
                                                           process))
        finally:
            _kernel32.CloseHandle(process)


def _text_of(function: Any, hwnd: Any) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    function(hwnd, buffer, len(buffer))
    return buffer.value


def windows_of(pid: int) -> "list[dict[str, Any]]":
    """Every visible top-level window a process owns, with class and title."""
    if not IS_WINDOWS:
        return []
    found: "list[dict[str, Any]]" = []

    def visit(hwnd: Any, _: Any) -> bool:
        owner = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and _user32.IsWindowVisible(hwnd):
            found.append({"hwnd": hwnd,
                          "class": _text_of(_user32.GetClassNameW, hwnd),
                          "title": _text_of(_user32.GetWindowTextW, hwnd)})
        return True

    _user32.EnumWindows(_WNDENUMPROC(visit), 0)
    return found


def keep_awake(pid: int) -> None:
    """Tell a game's window it is the active application. Takes no focus.

    RGSS3 stops running whenever its window is inactive: Graphics.update
    blocks inside the engine until the window is activated again, so a game
    whose window the user clicked away from -- or minimised -- never
    reaches the bridge. The engine learns it is active from WM_ACTIVATEAPP,
    and a posted one is enough to let it run. Checked on the real engine,
    unfocused and minimised. Only the game's own window is sent anything;
    the foreground window and the keyboard focus are left where they are.
    """
    if not IS_WINDOWS:
        return
    for window in windows_of(pid):
        if window["class"] == GAME_WINDOW_CLASS:
            _user32.PostMessageW(window["hwnd"], _WM_ACTIVATEAPP, 1, 0)


def restore_without_focus(hwnd: Any) -> bool:
    """Un-minimise a window without activating it. True if it was minimised."""
    if not IS_WINDOWS or not _user32.IsIconic(hwnd):
        return False
    _user32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
    return True


def dialog_texts(pid: int) -> "list[dict[str, str]]":
    """What each dialog a process is showing says: its title and its text.

    RGSS shows a Ruby exception it was not given a chance to catch -- one
    raised while the scripts load, above the bridge -- in a modal message
    box. The game then waits for a click that will never come, so this is
    how a hang is told apart from a crash.
    """
    out = []
    for window in windows_of(pid):
        if window["class"] != DIALOG_CLASS:
            continue
        lines: "list[str]" = []

        def visit(hwnd: Any, _: Any) -> bool:
            if _text_of(_user32.GetClassNameW, hwnd) == "Static":
                text = _text_of(_user32.GetWindowTextW, hwnd)
                if text:
                    lines.append(text)
            return True

        _user32.EnumChildWindows(window["hwnd"], _WNDENUMPROC(visit), 0)
        out.append({"title": window["title"], "text": "\n".join(lines)})
    return out


def _has_colour(bgra: bytes) -> bool:
    """Whether any pixel is not black, ignoring the alpha byte."""
    return any(bgra[channel::4].strip(b"\x00") for channel in range(3))


def capture_window(hwnd: Any) -> "tuple[int, int, bytes, str]":
    """A window's client area as (width, height, BGRA bytes, method).

    PrintWindow first, which works whether or not the window is covered;
    if it fails or hands back a frame that is entirely black, BitBlt from
    the window's own device context, which needs it to be on screen.
    The thread is made DPI-aware for the call only, so a scaled desktop
    gives the game's real pixels; the process setting is not touched.
    """
    if not IS_WINDOWS:
        raise OSError("capturing a window needs Windows")
    previous = (_SET_DPI(ctypes.c_void_p(
        _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)) if _SET_DPI else None)
    try:
        rect = wintypes.RECT()
        _user32.GetClientRect(hwnd, ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            raise OSError("the game window has no client area (minimised?)")
        window_dc = _user32.GetDC(hwnd)
        memory_dc = _gdi32.CreateCompatibleDC(window_dc)
        info = _BITMAPINFO()
        header = info.bmiHeader
        header.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        header.biWidth, header.biHeight = width, -height
        header.biPlanes, header.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        bitmap = _gdi32.CreateDIBSection(window_dc, ctypes.byref(info), 0,
                                         ctypes.byref(bits), None, 0)
        old = _gdi32.SelectObject(memory_dc, bitmap)
        try:
            method = "PrintWindow"
            printed = _user32.PrintWindow(
                hwnd, memory_dc, _PW_CLIENTONLY | _PW_RENDERFULLCONTENT)
            data = ctypes.string_at(bits, width * height * 4) if printed else b""
            if not printed or not _has_colour(data):
                method = "BitBlt"
                _gdi32.BitBlt(memory_dc, 0, 0, width, height, window_dc, 0, 0,
                              _SRCCOPY)
                data = ctypes.string_at(bits, width * height * 4)
        finally:
            _gdi32.SelectObject(memory_dc, old)
            _gdi32.DeleteObject(bitmap)
            _gdi32.DeleteDC(memory_dc)
            _user32.ReleaseDC(hwnd, window_dc)
        return width, height, data, method
    finally:
        if _SET_DPI and previous:
            _SET_DPI(ctypes.c_void_p(previous))
