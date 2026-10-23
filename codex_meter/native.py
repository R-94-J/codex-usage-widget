"""Read-only host discovery and no-activation Win32 overlay attachment.

The geometry and visibility policy can be imported without Windows or Tk.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import ntpath
import os
import sys


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    rect: Rect
    visible: bool
    minimized: bool


def anchor_position(
    host_rect: Rect,
    size: tuple[int, int],
    offset: tuple[int, int] = (16, 64),
    work_area: Rect | None = None,
) -> tuple[int, int]:
    """Inset from the host's top-right, then clamp to a monitor's work area.

    Supply work_area positionally with offset, or by keyword to use the default
    offset. An overlay larger than the work area is pinned to its top-left;
    fitting such an overlay completely on that monitor is not possible.
    """
    if work_area is None:
        raise TypeError("work_area is required")
    width, height = size
    if width < 0 or height < 0:
        raise ValueError("overlay dimensions must be nonnegative")
    if work_area.width <= 0 or work_area.height <= 0:
        raise ValueError("work_area must have positive dimensions")
    right_inset, top_inset = offset
    x = host_rect.right - width - right_inset
    y = host_rect.top + top_inset
    return (
        max(work_area.left, min(x, work_area.right - width)),
        max(work_area.top, min(y, work_area.bottom - height)),
    )


def should_show(
    host: WindowInfo | None, foreground_root: int, own_roots: set[int]
) -> bool:
    """Allow only a visible host or an explicitly allowed widget dialog root."""
    return bool(
        host is not None
        and host.visible
        and not host.minimized
        and foreground_root
        and (foreground_root == host.hwnd or foreground_root in own_roots)
    )


# All handles, LPARAM/WPARAM and LONG_PTR values retain the process pointer width.
GWLP_WNDPROC = -4
WM_MOUSEACTIVATE = 0x0021
WM_NCDESTROY = 0x0082
MA_NOACTIVATE = 3
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CHILD = 0x40000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
GA_ROOT = 2
GW_OWNER = 4
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MONITOR_DEFAULTTONEAREST = 2
SPI_GETWORKAREA = 0x0030
_HOST_EXECUTABLES = frozenset({"chatgpt.exe", "codex.exe"})
_MIN_HOST_SIZE = (320, 200)


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


@dataclass
class _Subclass:
    previous: int
    callback: object = None


# A native WNDPROC pointer is NOT a Python reference. Retain each thunk until
# WM_NCDESTROY even if a NativeWindows facade is garbage-collected beforehand.
_subclass_procs: dict[int, _Subclass] = {}


def _bind(library, name, result, arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


def _check(result, operation):
    if not result:
        error = ctypes.get_last_error()
        if error:
            raise ctypes.WinError(error)
        raise OSError(f"{operation} failed")
    return result


def _rect(value) -> Rect:
    return Rect(value.left, value.top, value.right, value.bottom)


def configure_dpi_awareness() -> None:
    """Call before creating Tk; leave manifest/already-selected awareness intact.

    Per-monitor v2 falls back to per-monitor v1, then system awareness on older
    Windows. This changes only the current process, never persistent settings.
    """
    if sys.platform != "win32":
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        set_context = _bind(user32, "SetProcessDpiAwarenessContext", wintypes.BOOL,
                            [ctypes.c_void_p])
        ctypes.set_last_error(0)
        if set_context(-4) or ctypes.get_last_error() == 5:
            return
    except (AttributeError, OSError):
        pass
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        set_awareness = _bind(shcore, "SetProcessDpiAwareness", wintypes.LONG, [ctypes.c_int])
        result = set_awareness(2)
        if result == 0 or result & 0xFFFFFFFF == 0x80070005:
            return
    except (AttributeError, OSError):
        pass
    try:
        _bind(user32, "SetProcessDPIAware", wintypes.BOOL, [])()
    except (AttributeError, OSError):
        pass


class NativeWindows:
    """Small synchronous adapter; the Tk UI owns polling and overlay lifetime.

    Discovery is read-only and trusts exact executable basenames, not titles.
    Construct on Windows only. Pass root_window(tk_window.winfo_id()) to overlay
    operations: Tk's winfo_id can identify its child rather than its wrapper.
    """

    def __init__(self):
        if sys.platform != "win32":
            raise OSError("NativeWindows requires Windows")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self._wndproc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        )
        user_functions = (
            ("CallWindowProcW", ctypes.c_ssize_t,
             [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]),
            ("EnumWindows", wintypes.BOOL, [self._enum_proc, wintypes.LPARAM]),
            ("GetForegroundWindow", wintypes.HWND, []),
            ("GetAncestor", wintypes.HWND, [wintypes.HWND, wintypes.UINT]),
            ("GetWindow", wintypes.HWND, [wintypes.HWND, wintypes.UINT]),
            ("GetWindowRect", wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]),
            ("IsWindow", wintypes.BOOL, [wintypes.HWND]),
            ("IsWindowVisible", wintypes.BOOL, [wintypes.HWND]),
            ("IsIconic", wintypes.BOOL, [wintypes.HWND]),
            ("GetWindowThreadProcessId", wintypes.DWORD,
             [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]),
            ("GetWindowTextLengthW", ctypes.c_int, [wintypes.HWND]),
            ("GetWindowTextW", ctypes.c_int, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]),
            ("SetWindowPos", wintypes.BOOL,
             [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
              ctypes.c_int, ctypes.c_int, wintypes.UINT]),
            ("ShowWindow", wintypes.BOOL, [wintypes.HWND, ctypes.c_int]),
            ("MonitorFromWindow", wintypes.HMONITOR, [wintypes.HWND, wintypes.DWORD]),
            ("GetMonitorInfoW", wintypes.BOOL,
             [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]),
            ("SystemParametersInfoW", wintypes.BOOL,
             [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]),
        )
        for name, result, arguments in user_functions:
            _bind(self._user32, name, result, arguments)
        # Ptr names are C macros, not exported functions, in a 32-bit user32.dll.
        wide = ctypes.sizeof(ctypes.c_void_p) == 8
        suffix, long_type = ("PtrW", ctypes.c_ssize_t) if wide else ("W", wintypes.LONG)
        self._get_window_long = _bind(self._user32, "GetWindowLong" + suffix, long_type,
                                      [wintypes.HWND, ctypes.c_int])
        self._set_window_long = _bind(self._user32, "SetWindowLong" + suffix, long_type,
                                      [wintypes.HWND, ctypes.c_int, long_type])
        _bind(self._kernel32, "OpenProcess", wintypes.HANDLE,
              [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD])
        _bind(self._kernel32, "QueryFullProcessImageNameW", wintypes.BOOL,
              [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)])
        _bind(self._kernel32, "CloseHandle", wintypes.BOOL, [wintypes.HANDLE])
        _bind(self._kernel32, "GetCurrentThreadId", wintypes.DWORD, [])
        self._set_dwm_attribute = None
        try:
            dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
            self._set_dwm_attribute = _bind(
                dwmapi, "DwmSetWindowAttribute", wintypes.LONG,
                [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD],
            )
        except (OSError, AttributeError):
            pass

    def _get_long(self, hwnd: int, index: int) -> int:
        ctypes.set_last_error(0)
        result = self._get_window_long(hwnd, index)
        if result == 0 and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        return int(result)

    def _set_long(self, hwnd: int, index: int, value: int) -> int:
        ctypes.set_last_error(0)
        previous = self._set_window_long(hwnd, index, value)
        if previous == 0 and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        return int(previous)

    def _process_basename(self, pid: int) -> str:
        process = self._kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process:
            return ""
        try:
            # Maximum extended Windows path, in WCHARs. No VM_READ or injection.
            capacity = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(capacity.value)
            if not self._kernel32.QueryFullProcessImageNameW(
                process, 0, buffer, ctypes.byref(capacity)
            ):
                return ""
            return ntpath.basename(buffer.value).casefold()
        finally:
            self._kernel32.CloseHandle(process)

    def list_hosts(self) -> list[WindowInfo]:
        """Enumerate visible unowned app windows at least 320 by 200 pixels.

        No title matching, host mutation, global hooks, or persistent PID cache.
        Access-denied/exited processes and windows disappearing mid-enumeration
        are skipped. The caller should recheck foreground/minimized every tick.
        """
        hosts: list[WindowInfo] = []
        names: dict[int, str] = {}

        @self._enum_proc
        def visit(hwnd, _data):
            try:
                if (not self._user32.IsWindowVisible(hwnd)
                        or self._user32.GetWindow(hwnd, GW_OWNER)
                        or self.root_window(hwnd) != hwnd):
                    return True
                bounds = self.rect(hwnd)
                if (bounds is None or bounds.width < _MIN_HOST_SIZE[0]
                        or bounds.height < _MIN_HOST_SIZE[1]):
                    return True
                if (self._get_long(hwnd, GWL_STYLE) & WS_CHILD
                        or self._get_long(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW):
                    return True
                pid = wintypes.DWORD()
                if not self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
                    return True
                if pid.value not in names:
                    names[pid.value] = self._process_basename(pid.value)
                if names[pid.value] not in _HOST_EXECUTABLES:
                    return True
                length = min(32767, max(0, self._user32.GetWindowTextLengthW(hwnd)))
                title = ctypes.create_unicode_buffer(length + 1)
                self._user32.GetWindowTextW(hwnd, title, len(title))
                hosts.append(WindowInfo(int(hwnd), pid.value, title.value, bounds,
                                        True, self.is_minimized(hwnd)))
            except OSError:
                # ctypes callbacks cannot propagate Python exceptions to EnumWindows.
                pass
            return True

        ctypes.set_last_error(0)
        _check(self._user32.EnumWindows(visit, 0), "EnumWindows")
        return hosts

    def foreground_window(self) -> int:
        return int(self._user32.GetForegroundWindow() or 0)

    def root_window(self, hwnd: int) -> int:
        return int(self._user32.GetAncestor(hwnd, GA_ROOT) or 0) if hwnd else 0

    def rect(self, hwnd: int) -> Rect | None:
        value = wintypes.RECT()
        if hwnd and self._user32.GetWindowRect(hwnd, ctypes.byref(value)):
            return _rect(value)
        return None

    def is_minimized(self, hwnd: int) -> bool:
        return bool(hwnd and self._user32.IsIconic(hwnd))

    def is_window(self, hwnd: int) -> bool:
        return bool(hwnd and self._user32.IsWindow(hwnd))

    def _own_window_thread(self, hwnd: int) -> int:
        if not self.is_window(hwnd):
            return 0
        pid = wintypes.DWORD()
        thread = self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not thread:
            return 0  # The window disappeared after IsWindow.
        if pid.value != os.getpid():
            raise ValueError("Overlay operations require a window owned by this process")
        return int(thread)

    def _install_noactivate(self, hwnd: int) -> None:
        if hwnd in _subclass_procs:
            return
        previous = self._get_long(hwnd, GWLP_WNDPROC)
        _check(previous, "GetWindowLongPtrW(GWLP_WNDPROC)")
        entry = _Subclass(previous)

        @self._wndproc_type
        def procedure(window, message, wparam, lparam):
            if message == WM_MOUSEACTIVATE:
                # Do not eat the click: ordinary Tk bindings still implement drag.
                return MA_NOACTIVATE
            if message == WM_NCDESTROY:
                try:
                    current = self._get_window_long(window, GWLP_WNDPROC)
                    if ctypes.c_void_p(current).value == address:
                        # Do not overwrite a newer procedure in a subclass chain.
                        self._set_window_long(window, GWLP_WNDPROC, entry.previous)
                    return self._user32.CallWindowProcW(
                        entry.previous, window, message, wparam, lparam,
                    )
                finally:
                    _subclass_procs.pop(window, None)
            return self._user32.CallWindowProcW(entry.previous, window, message, wparam, lparam)

        entry.callback = procedure
        address = ctypes.cast(procedure, ctypes.c_void_p).value
        _subclass_procs[hwnd] = entry
        try:
            entry.previous = self._set_long(hwnd, GWLP_WNDPROC, address)
        except OSError:
            _subclass_procs.pop(hwnd, None)
            raise

    def configure_overlay(self, hwnd: int) -> None:
        """Configure our overlay on its Tk thread, never the discovered host.

        The retained subclass overrides Tk's activating WM_MOUSEACTIVATE reply.
        Other messages go through CallWindowProcW; WM_NCDESTROY releases it.
        No generic tray callback API or global/native input hook is installed.
        """
        thread = self._own_window_thread(hwnd)
        if not thread:
            return
        if thread != self._kernel32.GetCurrentThreadId():
            raise RuntimeError("Configure the overlay on the thread that created its Tk window")
        self._install_noactivate(hwnd)
        style = self._get_long(hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        self._set_long(hwnd, GWL_EXSTYLE, style)
        _check(self._user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        ), "SetWindowPos")
        if self._set_dwm_attribute is not None:
            preference = ctypes.c_int(2)  # DWMWCP_ROUND; Windows 11+, best effort.
            self._set_dwm_attribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))

    def show_overlay(self, hwnd: int, x: int, y: int, width: int, height: int) -> None:
        """Move/show without activating; do not pair with Tk deiconify per tick."""
        if not self._own_window_thread(hwnd):
            return
        _check(self._user32.SetWindowPos(
            hwnd, HWND_TOPMOST, x, y, width, height, SWP_NOACTIVATE | SWP_SHOWWINDOW,
        ), "SetWindowPos")
        # Return value is previous visibility, NOT a success/failure BOOL.
        self._user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)

    def hide(self, hwnd: int) -> None:
        if self._own_window_thread(hwnd):
            self._user32.ShowWindow(hwnd, SW_HIDE)

    def work_area(self, hwnd: int) -> Rect:
        """Return the nearest monitor's work area, with a primary-monitor fallback."""
        monitor = self._user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if monitor and self._user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return _rect(info.rcWork)
        bounds = wintypes.RECT()
        ctypes.set_last_error(0)
        _check(self._user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(bounds), 0),
               "SystemParametersInfoW(SPI_GETWORKAREA)")
        return _rect(bounds)
