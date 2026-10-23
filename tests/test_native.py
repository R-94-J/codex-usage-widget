"""Attachment tests: policy is pure; Win32 mutations target our own Tk windows only.

Run with D:/python3.12/python.exe -B -m unittest tests.test_native -v.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import dataclasses
import gc
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock


class NativeTestCase(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            importlib.util.find_spec("codex_meter.native"),
            "The native attachment module has not been implemented yet",
        )
        self.native = importlib.import_module("codex_meter.native")
        self.Rect = self.native.Rect
        self.WindowInfo = self.native.WindowInfo


class RectTests(NativeTestCase):
    def test_rect_is_a_dataclass_with_the_contract_fields(self):
        self.assertTrue(dataclasses.is_dataclass(self.Rect))
        self.assertEqual(
            [field.name for field in dataclasses.fields(self.Rect)],
            ["left", "top", "right", "bottom"],
        )

    def test_rect_dimensions_preserve_negative_monitor_coordinates(self):
        rect = self.Rect(-1920, -300, -200, 800)
        self.assertEqual((rect.width, rect.height), (1720, 1100))
        self.assertEqual(self.Rect(10, 20, 10, 20).width, 0)

    def test_window_info_is_a_snapshot_with_the_contract_fields(self):
        rect = self.Rect(10, 20, 1210, 820)
        host = self.WindowInfo(123, 456, "ChatGPT", rect, True, False)
        self.assertTrue(dataclasses.is_dataclass(host))
        self.assertEqual(dataclasses.astuple(host),
                         (123, 456, "ChatGPT", (10, 20, 1210, 820), True, False))


class GeometryTests(NativeTestCase):
    def setUp(self):
        super().setUp()
        self.host = self.Rect(100, 50, 1300, 850)
        self.area = self.Rect(0, 0, 1920, 1040)

    def test_default_insets_are_right_16_and_top_64(self):
        self.assertEqual(
            self.native.anchor_position(self.host, (300, 48), work_area=self.area),
            (984, 114),
        )

    def test_explicit_insets_use_the_host_right_edge(self):
        self.assertEqual(
            self.native.anchor_position(self.host, (300, 48), (24, 100), self.area),
            (976, 150),
        )

    def test_clamps_left_and_top_for_offscreen_host(self):
        host = self.Rect(-2000, -500, -1000, 500)
        self.assertEqual(
            self.native.anchor_position(host, (300, 48), (16, 64), self.area),
            (0, 0),
        )

    def test_clamps_right_and_bottom_excluding_taskbar(self):
        host = self.Rect(1800, 1000, 3000, 1800)
        self.assertEqual(
            self.native.anchor_position(host, (300, 48), (16, 64), self.area),
            (1620, 992),
        )

    def test_negative_monitor_coordinates_are_not_clamped_to_zero(self):
        area = self.Rect(-1920, -1080, 0, -40)
        host = self.Rect(-1800, -1000, -100, -100)
        self.assertEqual(
            self.native.anchor_position(host, (320, 48), (16, 64), area),
            (-436, -936),
        )

    def test_taskbars_on_left_and_top_are_respected(self):
        area = self.Rect(-1860, -1030, 0, 0)
        host = self.Rect(-2400, -1400, -2000, -600)
        self.assertEqual(
            self.native.anchor_position(host, (320, 48), (16, 64), area),
            (-1860, -1030),
        )

    def test_oversized_overlay_pins_to_work_area_origin(self):
        area = self.Rect(-1000, 50, -600, 350)
        self.assertEqual(
            self.native.anchor_position(self.host, (900, 500), (16, 64), area),
            (-1000, 50),
        )

    def test_negative_drag_offsets_are_allowed_but_still_clamped(self):
        self.assertEqual(
            self.native.anchor_position(self.host, (300, 48), (-2000, -500), self.area),
            (1620, 0),
        )

    def test_overlay_exactly_fitting_work_area_has_one_anchor(self):
        self.assertEqual(
            self.native.anchor_position(self.host, (1920, 1040), (16, 64), self.area),
            (0, 0),
        )

    def test_invalid_sizes_and_work_areas_are_rejected(self):
        for size in ((-1, 48), (300, -1)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                self.native.anchor_position(self.host, size, (16, 64), self.area)
        for area in (self.Rect(0, 0, 0, 100), self.Rect(0, 50, 100, 40)):
            with self.subTest(area=area), self.assertRaises(ValueError):
                self.native.anchor_position(self.host, (300, 48), (16, 64), area)
        with self.assertRaises(TypeError):
            self.native.anchor_position(self.host, (300, 48))


class VisibilityTests(NativeTestCase):
    def setUp(self):
        super().setUp()
        self.host = self.WindowInfo(123, 456, "anything", self.Rect(0, 0, 1200, 800),
                                    True, False)

    def test_visible_host_or_explicit_own_dialog_allows_showing(self):
        for foreground in (123, 789):
            with self.subTest(foreground=foreground):
                self.assertTrue(self.native.should_show(self.host, foreground, {789}))

    def test_unrelated_foreground_and_null_foreground_hide_overlay(self):
        for foreground in (999, 0):
            with self.subTest(foreground=foreground):
                self.assertFalse(self.native.should_show(self.host, foreground, {0, 789}))

    def test_missing_hidden_or_minimized_host_hides_even_for_own_dialog(self):
        for host in (None, dataclasses.replace(self.host, visible=False),
                     dataclasses.replace(self.host, minimized=True)):
            for foreground in (123, 789):
                with self.subTest(host=host, foreground=foreground):
                    self.assertFalse(self.native.should_show(host, foreground, {789}))

    def test_allowed_roots_set_is_not_modified(self):
        roots = {789}
        self.native.should_show(self.host, 123, roots)
        self.assertEqual(roots, {789})


class PortabilityTests(NativeTestCase):
    def test_non_windows_import_does_not_load_dlls_and_dpi_is_a_noop(self):
        self.assertTrue(hasattr(self.native, "NativeWindows"), "NativeWindows is missing")
        script = """
import ctypes, sys
sys.platform = 'linux'
def forbidden(*args, **kwargs):
    raise AssertionError('Windows APIs must not be loaded on import')
ctypes.WinDLL = forbidden
ctypes.WINFUNCTYPE = forbidden
from codex_meter.native import Rect, NativeWindows, configure_dpi_awareness
assert Rect(-100, 0, 100, 50).width == 200
assert configure_dpi_awareness() is None
try:
    NativeWindows()
except OSError:
    pass
else:
    raise AssertionError('NativeWindows must explicitly reject non-Windows')
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class FakeWin32TestCase(NativeTestCase):
    """Only DLL calls are faked, never the policy or discovery implementation."""

    def setUp(self):
        super().setUp()
        self.assertTrue(hasattr(self.native, "NativeWindows"), "NativeWindows is missing")
        names = (
            "EnumWindows GetForegroundWindow GetAncestor GetWindow GetWindowRect "
            "IsWindow IsWindowVisible IsIconic GetWindowThreadProcessId "
            "GetWindowTextLengthW GetWindowTextW GetWindowLongW SetWindowLongW "
            "GetWindowLongPtrW SetWindowLongPtrW SetWindowPos ShowWindow "
            "MonitorFromWindow GetMonitorInfoW SystemParametersInfoW "
            "CallWindowProcW SetProcessDpiAwarenessContext SetProcessDPIAware"
        )
        self.user32 = SimpleNamespace(**{name: mock.Mock(return_value=0)
                                        for name in names.split()})
        self.kernel32 = SimpleNamespace(**{name: mock.Mock(return_value=0) for name in (
            "OpenProcess", "QueryFullProcessImageNameW", "CloseHandle", "GetCurrentThreadId"
        )})
        self.dwmapi = SimpleNamespace(DwmSetWindowAttribute=mock.Mock(return_value=0))
        self.shcore = SimpleNamespace(SetProcessDpiAwareness=mock.Mock(return_value=0))
        self.dlls = {"user32": self.user32, "kernel32": self.kernel32,
                     "dwmapi": self.dwmapi, "shcore": self.shcore}
        self.loader = self.enterContext(mock.patch.object(
            ctypes, "WinDLL", side_effect=lambda name, **kw: self.dlls[name.removesuffix('.dll')]
        ))
        ctypes.set_last_error(0)
        self.windows = {}
        self.addCleanup(self.clear_fake_subclasses)
        self.paths = {}
        self.process_handles = {}
        self.user32.IsWindow.side_effect = lambda hwnd: hwnd in self.windows
        self.user32.IsWindowVisible.side_effect = lambda hwnd: self.windows[hwnd]["visible"]
        self.user32.IsIconic.side_effect = lambda hwnd: self.windows.get(hwnd, {}).get("minimized", False)
        self.user32.GetWindow.side_effect = lambda hwnd, which: self.windows[hwnd]["owner"]
        self.user32.GetAncestor.side_effect = lambda hwnd, which: self.windows.get(hwnd, {}).get("root", 0)
        self.user32.GetWindowRect.side_effect = self.get_rect
        self.user32.GetWindowThreadProcessId.side_effect = self.get_pid
        self.user32.GetWindowTextLengthW.side_effect = lambda hwnd: len(self.windows[hwnd]["title"])
        self.user32.GetWindowTextW.side_effect = self.get_title
        for name in ("GetWindowLongW", "GetWindowLongPtrW"):
            getattr(self.user32, name).side_effect = self.get_long
        for name in ("SetWindowLongW", "SetWindowLongPtrW"):
            getattr(self.user32, name).side_effect = self.set_long
        self.user32.SetWindowPos.return_value = 1
        self.user32.MonitorFromWindow.return_value = 0x100000088
        self.user32.GetMonitorInfoW.side_effect = self.get_monitor_info
        self.kernel32.OpenProcess.side_effect = self.open_process
        self.kernel32.QueryFullProcessImageNameW.side_effect = self.query_path
        self.kernel32.CloseHandle.return_value = 1
        self.kernel32.GetCurrentThreadId.return_value = 222
        self.user32.EnumWindows.side_effect = self.enumerate_windows

    def clear_fake_subclasses(self):
        # These HWNDs exist only in this DLL fake; no native window owns them.
        registry = getattr(self.native, "_subclass_procs", {})
        for hwnd in self.windows:
            registry.pop(hwnd, None)

    def add_window(self, hwnd=101, pid=500, path=r"C:\Apps\ChatGPT.exe", **changes):
        record = dict(pid=pid, title="ChatGPT", visible=True, minimized=False,
                      owner=0, root=hwnd, rect=(0, 0, 1200, 800), style=0,
                      exstyle=0, wndproc=0x123456789)
        record.update(changes)
        self.windows[hwnd] = record
        self.paths[pid] = path
        return hwnd

    def enumerate_windows(self, callback, data):
        for hwnd in self.windows:
            if not callback(hwnd, data):
                return 0
        return 1

    def get_rect(self, hwnd, out):
        value = self.windows.get(hwnd, {}).get("rect")
        if value is None:
            return 0
        out._obj.left, out._obj.top, out._obj.right, out._obj.bottom = value
        return 1

    def get_pid(self, hwnd, out):
        out._obj.value = self.windows.get(hwnd, {}).get("pid", 0)
        return 222 if hwnd in self.windows else 0

    def get_title(self, hwnd, buffer, capacity):
        buffer.value = self.windows[hwnd]["title"][:capacity - 1]
        return len(buffer.value)

    def get_long(self, hwnd, index):
        return self.windows[hwnd][{-16: "style", -20: "exstyle", -4: "wndproc"}[index]]

    def set_long(self, hwnd, index, value):
        key = {-16: "style", -20: "exstyle", -4: "wndproc"}[index]
        previous = self.windows[hwnd][key]
        self.windows[hwnd][key] = value
        return previous

    def open_process(self, rights, inherit, pid):
        self.assertEqual(rights, 0x1000)  # PROCESS_QUERY_LIMITED_INFORMATION only
        self.assertFalse(inherit)
        if self.paths.get(pid) is None:
            return 0
        handle = 0x100000000 + pid
        self.process_handles[handle] = pid
        return handle

    def query_path(self, handle, flags, buffer, length):
        self.assertEqual(flags, 0)  # Win32 path, not device path
        value = self.paths[self.process_handles[handle]]
        self.assertGreaterEqual(length._obj.value, len(value) + 1)
        buffer.value = value
        length._obj.value = len(value)
        return 1

    def get_monitor_info(self, monitor, out):
        self.assertEqual(out._obj.cbSize, ctypes.sizeof(out._obj))
        out._obj.rcWork = wintypes.RECT(-1920, -100, 0, 1000)
        return 1


@unittest.skipUnless(sys.platform == "win32", "Win32 ABI boundary tests")
class Win32BoundaryTests(FakeWin32TestCase):
    def test_exact_executable_basename_is_used_not_title(self):
        self.add_window(101, 500, r"C:\Apps\CHATGPT.EXE", title="Unrelated localized title")
        self.add_window(102, 501, r"C:\Apps\Codex.exe", title="")
        self.add_window(103, 502, r"C:\Apps\python.exe", title="ChatGPT")
        self.add_window(104, 503, r"C:\Apps\NotCodex.exe", title="Codex")
        self.add_window(105, 504, r"C:\Apps\ChatGPT.exe.bak", title="ChatGPT")
        self.add_window(106, 505, r"C:\Codex.exe\editor.exe", title="Codex")
        hosts = self.native.NativeWindows().list_hosts()
        self.assertEqual([host.hwnd for host in hosts], [101, 102])
        self.assertEqual(hosts[0].pid, 500)
        self.assertEqual(hosts[0].rect, self.Rect(0, 0, 1200, 800))
        self.assertEqual(hosts[0].title, "Unrelated localized title")

    def test_hidden_owned_child_tool_and_tiny_windows_are_excluded(self):
        for hwnd, change in enumerate((dict(visible=False), dict(owner=900),
                                      dict(root=900), dict(style=0x40000000),
                                      dict(exstyle=0x80), dict(rect=(0, 0, 319, 800)),
                                      dict(rect=(0, 0, 1200, 199)), dict(rect=None)), 101):
            self.add_window(hwnd=hwnd, **change)
        self.assertEqual(self.native.NativeWindows().list_hosts(), [])
        self.kernel32.OpenProcess.assert_not_called()

    def test_inaccessible_process_is_skipped_without_closing_null_handle(self):
        self.add_window(path=None)
        self.assertEqual(self.native.NativeWindows().list_hosts(), [])
        self.kernel32.CloseHandle.assert_not_called()

    def test_failed_path_query_closes_its_read_only_process_handle(self):
        self.add_window()
        self.kernel32.QueryFullProcessImageNameW.side_effect = None
        self.kernel32.QueryFullProcessImageNameW.return_value = 0
        self.assertEqual(self.native.NativeWindows().list_hosts(), [])
        self.kernel32.CloseHandle.assert_called_once_with(0x100000000 + 500)

    def test_process_handle_is_closed_even_if_query_raises(self):
        self.add_window()
        self.kernel32.QueryFullProcessImageNameW.side_effect = OSError("process exited")
        self.assertEqual(self.native.NativeWindows().list_hosts(), [])
        self.kernel32.CloseHandle.assert_called_once_with(0x100000000 + 500)

    def test_process_names_are_cached_only_within_one_enumeration(self):
        self.add_window(101, 500)
        self.add_window(102, 500)
        api = self.native.NativeWindows()
        self.assertEqual(len(api.list_hosts()), 2)
        self.assertEqual(self.kernel32.OpenProcess.call_count, 1)
        self.paths[500] = r"C:\Apps\editor.exe"
        self.assertEqual(api.list_hosts(), [])
        self.assertEqual(self.kernel32.OpenProcess.call_count, 2)
        self.assertEqual(self.kernel32.CloseHandle.call_count, 2)

    def test_minimized_state_is_preserved_in_the_snapshot(self):
        self.add_window(minimized=True)
        hosts = self.native.NativeWindows().list_hosts()
        self.assertEqual(len(hosts), 1)
        self.assertTrue(hosts[0].visible)
        self.assertTrue(hosts[0].minimized)

    def test_enumeration_failure_is_reported(self):
        def failed(*args):
            ctypes.set_last_error(5)
            return 0
        self.user32.EnumWindows.side_effect = failed
        with self.assertRaises(OSError):
            self.native.NativeWindows().list_hosts()

    def test_pointer_sized_handles_and_callback_arguments_are_preserved(self):
        hwnd = self.add_window(hwnd=0x100000123)
        self.user32.GetForegroundWindow.return_value = hwnd
        api = self.native.NativeWindows()
        self.assertEqual(api.foreground_window(), hwnd)
        self.assertEqual(api.root_window(hwnd), hwnd)
        self.assertEqual(api.list_hosts()[0].hwnd, hwnd)
        self.assertIs(self.user32.GetForegroundWindow.restype, wintypes.HWND)
        self.assertIs(self.kernel32.OpenProcess.restype, wintypes.HANDLE)
        self.assertIs(self.user32.GetAncestor.restype, wintypes.HWND)
        self.assertEqual(self.user32.SetWindowPos.argtypes[:2], [wintypes.HWND, wintypes.HWND])
        enum_type = self.user32.EnumWindows.argtypes[0]
        self.assertEqual(enum_type._argtypes_, (wintypes.HWND, wintypes.LPARAM))
        self.assertEqual(ctypes.sizeof(enum_type._argtypes_[1]), ctypes.sizeof(ctypes.c_void_p))
        self.assertIs(self.user32.GetWindowLongPtrW.restype, ctypes.c_ssize_t)
        self.assertIs(self.user32.SetWindowLongPtrW.argtypes[2], ctypes.c_ssize_t)
        self.assertTrue(all(call.kwargs.get("use_last_error") for call in self.loader.call_args_list))

    def test_32_bit_binding_uses_get_and_set_window_long_without_ptr_exports(self):
        del self.user32.GetWindowLongPtrW
        del self.user32.SetWindowLongPtrW
        original_sizeof = ctypes.sizeof
        with mock.patch.object(ctypes, "sizeof", side_effect=lambda value:
                               4 if value is ctypes.c_void_p else original_sizeof(value)):
            self.native.NativeWindows()
        self.assertIs(self.user32.GetWindowLongW.restype, wintypes.LONG)
        self.assertIs(self.user32.SetWindowLongW.argtypes[2], wintypes.LONG)

    def test_overlay_styles_preserve_layered_and_remove_appwindow(self):
        hwnd = self.add_window(pid=os.getpid(), exstyle=0x80000 | 0x40000)
        api = self.native.NativeWindows()
        api.configure_overlay(hwnd)
        self.assertEqual(self.windows[hwnd]["exstyle"], 0x80000 | 0x08000000 | 0x80)
        self.user32.SetWindowPos.assert_called_with(hwnd, 0, 0, 0, 0, 0,
                                                   0x01 | 0x02 | 0x04 | 0x10 | 0x20)
        self.user32.ShowWindow.assert_not_called()

    def test_zero_previous_style_is_success_and_stale_last_error_is_cleared(self):
        hwnd = self.add_window(pid=os.getpid(), exstyle=0)
        ctypes.set_last_error(5)
        self.native.NativeWindows().configure_overlay(hwnd)
        self.assertTrue(self.windows[hwnd]["exstyle"] & 0x08000000)

    def test_style_write_failure_is_reported(self):
        hwnd = self.add_window(pid=os.getpid())
        def failed(*args):
            ctypes.set_last_error(5)
            return 0
        self.user32.SetWindowLongPtrW.side_effect = failed
        with self.assertRaises(OSError):
            self.native.NativeWindows().configure_overlay(hwnd)

    def test_show_and_hide_never_request_activation(self):
        hwnd = self.add_window(pid=os.getpid())
        api = self.native.NativeWindows()
        api.show_overlay(hwnd, -300, -100, 320, 48)
        self.user32.SetWindowPos.assert_called_once_with(hwnd, -1, -300, -100, 320, 48, 0x10 | 0x40)
        self.user32.ShowWindow.assert_called_with(hwnd, 4)
        api.hide(hwnd)
        self.user32.ShowWindow.assert_called_with(hwnd, 0)

    def test_invalid_handles_are_harmless_and_return_empty_query_results(self):
        api = self.native.NativeWindows()
        self.assertEqual(api.foreground_window(), 0)
        for hwnd in (0, 999):
            self.assertFalse(api.is_window(hwnd))
            self.assertFalse(api.is_minimized(hwnd))
            self.assertIsNone(api.rect(hwnd))
            self.assertEqual(api.root_window(hwnd), 0)
            api.configure_overlay(hwnd)
            api.show_overlay(hwnd, 0, 0, 320, 48)
            api.hide(hwnd)
        self.user32.SetWindowPos.assert_not_called()
        self.user32.ShowWindow.assert_not_called()

    def test_work_area_uses_nearest_monitor_and_virtual_screen_coordinates(self):
        api = self.native.NativeWindows()
        self.assertEqual(api.work_area(101), self.Rect(-1920, -100, 0, 1000))
        self.user32.MonitorFromWindow.assert_called_once_with(101, 2)
        self.user32.SystemParametersInfoW.assert_not_called()

    def test_work_area_falls_back_to_primary_work_area(self):
        self.user32.GetMonitorInfoW.side_effect = None
        self.user32.GetMonitorInfoW.return_value = 0
        def spi(action, param, out, flags):
            self.assertEqual((action, param, flags), (0x30, 0, 0))
            out._obj.left, out._obj.top = 40, 20
            out._obj.right, out._obj.bottom = 1920, 1040
            return 1
        self.user32.SystemParametersInfoW.side_effect = spi
        self.assertEqual(self.native.NativeWindows().work_area(0), self.Rect(40, 20, 1920, 1040))

    def test_work_area_failure_is_not_silently_reported_as_real_bounds(self):
        self.user32.GetMonitorInfoW.side_effect = None
        self.user32.GetMonitorInfoW.return_value = 0
        with self.assertRaises(OSError):
            self.native.NativeWindows().work_area(0)

    def test_rounded_corners_are_optional(self):
        hwnd = self.add_window(pid=os.getpid())
        self.dwmapi.DwmSetWindowAttribute.return_value = -2147024809
        self.native.NativeWindows().configure_overlay(hwnd)
        self.assertTrue(self.windows[hwnd]["exstyle"] & 0x08000000)


@unittest.skipUnless(sys.platform == "win32", "DPI API boundary tests")
class DpiTests(FakeWin32TestCase):
    def test_per_monitor_v2_uses_a_pointer_sized_context(self):
        self.user32.SetProcessDpiAwarenessContext.return_value = 1
        self.native.configure_dpi_awareness()
        self.user32.SetProcessDpiAwarenessContext.assert_called_once_with(-4)
        self.assertEqual(self.user32.SetProcessDpiAwarenessContext.argtypes, [ctypes.c_void_p])
        self.shcore.SetProcessDpiAwareness.assert_not_called()

    def test_missing_v2_api_falls_back_to_per_monitor_v1(self):
        del self.user32.SetProcessDpiAwarenessContext
        self.native.configure_dpi_awareness()
        self.shcore.SetProcessDpiAwareness.assert_called_once_with(2)
        self.user32.SetProcessDPIAware.assert_not_called()

    def test_old_system_falls_back_to_system_dpi_awareness(self):
        del self.user32.SetProcessDpiAwarenessContext
        del self.shcore.SetProcessDpiAwareness
        self.native.configure_dpi_awareness()
        self.user32.SetProcessDPIAware.assert_called_once_with()

    def test_existing_process_awareness_is_not_overridden(self):
        def denied(context):
            ctypes.set_last_error(5)
            return 0
        self.user32.SetProcessDpiAwarenessContext.side_effect = denied
        self.native.configure_dpi_awareness()
        self.shcore.SetProcessDpiAwareness.assert_not_called()
        self.user32.SetProcessDPIAware.assert_not_called()

    def test_existing_shcore_awareness_does_not_fall_back(self):
        del self.user32.SetProcessDpiAwarenessContext
        self.shcore.SetProcessDpiAwareness.return_value = -2147024891
        self.native.configure_dpi_awareness()
        self.user32.SetProcessDPIAware.assert_not_called()


@unittest.skipUnless(sys.platform == "win32", "Win32 subclass ABI tests")
class SubclassTests(FakeWin32TestCase):
    def test_mouse_activate_returns_noactivate_without_eating_clicks(self):
        hwnd = self.add_window(pid=os.getpid())
        api = self.native.NativeWindows()
        previous = self.windows[hwnd]["wndproc"]
        api.configure_overlay(hwnd)
        self.assertNotEqual(self.windows[hwnd]["wndproc"], previous,
                            "Tk must have a retained WM_MOUSEACTIVATE override")
        entry = self.native._subclass_procs[hwnd]
        self.assertEqual(entry.callback(hwnd, 0x21, hwnd, (0x201 << 16) | 1), 3)
        self.user32.CallWindowProcW.assert_not_called()

    def test_other_messages_forward_pointer_sized_arguments_and_result(self):
        hwnd = self.add_window(hwnd=0x100000321, pid=os.getpid())
        api = self.native.NativeWindows()
        previous = self.windows[hwnd]["wndproc"]
        api.configure_overlay(hwnd)
        self.assertNotEqual(self.windows[hwnd]["wndproc"], previous)
        self.user32.CallWindowProcW.return_value = 0x100000007
        callback = self.native._subclass_procs[hwnd].callback
        self.assertEqual(callback(hwnd, 0x455, 0x100000123, -0x100000456), 0x100000007)
        self.user32.CallWindowProcW.assert_called_once_with(
            previous, hwnd, 0x455, 0x100000123, -0x100000456)
        self.assertIs(callback.restype, ctypes.c_ssize_t)
        self.assertEqual(callback.argtypes, (wintypes.HWND, wintypes.UINT,
                                             wintypes.WPARAM, wintypes.LPARAM))
        self.assertIs(self.user32.CallWindowProcW.restype, ctypes.c_ssize_t)
        self.assertIs(self.user32.CallWindowProcW.argtypes[0], ctypes.c_void_p)

    def test_reconfiguring_does_not_chain_duplicate_callbacks(self):
        hwnd = self.add_window(pid=os.getpid())
        api = self.native.NativeWindows()
        previous = self.windows[hwnd]["wndproc"]
        api.configure_overlay(hwnd)
        callback_pointer = self.windows[hwnd]["wndproc"]
        self.assertNotEqual(callback_pointer, previous)
        api.configure_overlay(hwnd)
        self.native.NativeWindows().configure_overlay(hwnd)
        self.assertEqual(self.windows[hwnd]["wndproc"], callback_pointer)
        installs = [call for call in self.user32.SetWindowLongPtrW.call_args_list if call.args[1] == -4]
        self.assertEqual(len(installs), 1)

    def test_destroy_restores_original_and_releases_retained_callback(self):
        hwnd = self.add_window(pid=os.getpid())
        api = self.native.NativeWindows()
        previous = self.windows[hwnd]["wndproc"]
        api.configure_overlay(hwnd)
        self.assertNotEqual(self.windows[hwnd]["wndproc"], previous)
        callback = self.native._subclass_procs[hwnd].callback
        callback(hwnd, 0x82, 0, 0)  # WM_NCDESTROY
        self.assertEqual(self.windows[hwnd]["wndproc"], previous)
        self.user32.CallWindowProcW.assert_called_once_with(previous, hwnd, 0x82, 0, 0)
        self.assertNotIn(hwnd, self.native._subclass_procs)

    def test_destroy_does_not_overwrite_a_newer_subclass(self):
        hwnd = self.add_window(pid=os.getpid())
        api = self.native.NativeWindows()
        previous = self.windows[hwnd]["wndproc"]
        api.configure_overlay(hwnd)
        self.assertNotEqual(self.windows[hwnd]["wndproc"], previous)
        callback = self.native._subclass_procs[hwnd].callback
        self.windows[hwnd]["wndproc"] = 0x987654321
        callback(hwnd, 0x82, 0, 0)
        self.assertEqual(self.windows[hwnd]["wndproc"], 0x987654321)
        self.assertNotIn(hwnd, self.native._subclass_procs)

    def test_foreign_windows_are_rejected_before_any_mutation(self):
        hwnd = self.add_window(pid=os.getpid() + 1)
        api = self.native.NativeWindows()
        for operation in (lambda: api.configure_overlay(hwnd),
                          lambda: api.show_overlay(hwnd, 0, 0, 320, 48),
                          lambda: api.hide(hwnd)):
            with self.assertRaises(ValueError):
                operation()
        self.user32.SetWindowLongPtrW.assert_not_called()
        self.user32.SetWindowPos.assert_not_called()
        self.user32.ShowWindow.assert_not_called()

    def test_subclass_must_be_installed_on_the_owning_tk_thread(self):
        hwnd = self.add_window(pid=os.getpid())
        self.kernel32.GetCurrentThreadId.return_value = 333
        with self.assertRaises(RuntimeError):
            self.native.NativeWindows().configure_overlay(hwnd)
        self.user32.SetWindowLongPtrW.assert_not_called()

    def test_failed_subclass_install_does_not_retain_a_dead_callback(self):
        hwnd = self.add_window(pid=os.getpid())
        original = self.windows[hwnd]["wndproc"]
        def set_long(hwnd, index, value):
            if index == -4:
                ctypes.set_last_error(5)
                return 0
            return self.set_long(hwnd, index, value)
        self.user32.SetWindowLongPtrW.side_effect = set_long
        with self.assertRaises(OSError):
            self.native.NativeWindows().configure_overlay(hwnd)
        self.assertEqual(self.windows[hwnd]["wndproc"], original)
        self.assertNotIn(hwnd, self.native._subclass_procs)


@unittest.skipUnless(sys.platform == "win32", "Needs Windows and an interactive Tk desktop")
class RealTkTests(NativeTestCase):
    """No real Codex/ChatGPT HWND is queried, moved, subclassed or focused here."""

    def setUp(self):
        super().setUp()
        import tkinter as tk
        self.native.configure_dpi_awareness()
        self.api = self.native.NativeWindows()
        try:
            self.host = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk desktop unavailable: {error}")
        self.addCleanup(self.host.destroy)
        self.host.title("Native attachment TEST host (disposable)")
        self.host.geometry("640x360+60+60")
        self.overlay = tk.Toplevel(self.host)
        self.overlay.withdraw()
        self.overlay.overrideredirect(True)
        self.overlay.geometry("320x64+80+80")
        self.label = tk.Label(self.overlay, text="Native attachment TEST overlay", bg="#222222", fg="white")
        self.label.pack(fill="both", expand=True)
        self.host.update_idletasks()
        self.host.update()
        self.host_hwnd = self.api.root_window(self.host.winfo_id())
        self.hwnd = self.api.root_window(self.overlay.winfo_id())
        self.assertNotEqual(self.hwnd, self.host_hwnd)
        self.assertTrue(self.api.is_window(self.hwnd))
        for hwnd in (self.hwnd, self.host_hwnd):
            pid = wintypes.DWORD()
            self.api._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            self.assertEqual(pid.value, os.getpid(), "Test may mutate only its own HWNDs")
        # Deliberately focus only our disposable host, never a real application.
        self.host.focus_force()
        self.host.update()
        self.assertEqual(self.api.foreground_window(), self.host_hwnd)
        self.send_message = self.api._user32.SendMessageW
        self.send_message.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.send_message.restype = ctypes.c_ssize_t

    def show_own_overlay(self):
        area = self.api.work_area(self.host_hwnd)
        x, y = self.native.anchor_position(self.api.rect(self.host_hwnd), (320, 64), work_area=area)
        self.api.show_overlay(self.hwnd, x, y, 320, 64)
        self.host.update()
        self.assertTrue(self.api._user32.IsWindowVisible(self.hwnd))
        return x, y

    def test_real_tk_show_move_hide_preserves_own_host_foreground(self):
        self.api.configure_overlay(self.hwnd)
        x, y = self.show_own_overlay()
        self.assertEqual(self.api.rect(self.hwnd), self.Rect(x, y, x + 320, y + 64))
        for step in range(10):
            self.api.show_overlay(self.hwnd, x - step, y + step, 320, 64)
            self.host.update()
            self.assertEqual(self.api.foreground_window(), self.host_hwnd)
            self.api.hide(self.hwnd)
            self.host.update()
            self.assertFalse(self.api._user32.IsWindowVisible(self.hwnd))
            self.assertEqual(self.api.foreground_window(), self.host_hwnd)
        self.show_own_overlay()
        self.assertEqual(self.api.foreground_window(), self.host_hwnd)

    def test_real_tk_mouseactivate_returns_3_and_button_binding_still_runs(self):
        self.api.configure_overlay(self.hwnd)
        self.show_own_overlay()
        for target in (self.hwnd, self.overlay.winfo_id(), self.label.winfo_id()):
            result = self.send_message(target, 0x21, self.hwnd, (0x201 << 16) | 1)
            self.assertEqual(result, 3, f"WM_MOUSEACTIVATE on our HWND {target}")
            self.assertEqual(self.api.foreground_window(), self.host_hwnd)
        clicks = []
        self.label.bind("<Button-1>", lambda event: clicks.append((event.x, event.y)))
        # Tk 8.6 derives button state from GetKeyState, not WM_*'s wParam.
        # Adjust ONLY this test thread's input table, restoring it in finally;
        # no SendInput, hardware cursor movement, global hooks, or foreign HWND.
        user32 = self.api._user32
        for name in ("GetKeyboardState", "SetKeyboardState"):
            function = getattr(user32, name)
            function.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
            function.restype = wintypes.BOOL
        original = (ctypes.c_ubyte * 256)()
        self.assertTrue(user32.GetKeyboardState(original))
        state = (ctypes.c_ubyte * 256).from_buffer_copy(original)
        try:
            state[1] = 0x80  # VK_LBUTTON down in this thread, not global input.
            self.assertTrue(user32.SetKeyboardState(state))
            self.send_message(self.label.winfo_id(), 0x201, 1, (5 << 16) | 5)
            self.host.update()
            state[1] = 0
            self.assertTrue(user32.SetKeyboardState(state))
            self.send_message(self.label.winfo_id(), 0x202, 0, (5 << 16) | 5)
            self.host.update()
        finally:
            self.assertTrue(user32.SetKeyboardState(original))
        self.assertEqual(clicks, [(5, 5)])
        self.assertEqual(self.api.foreground_window(), self.host_hwnd)

    def test_real_tk_subclass_is_retained_idempotent_and_removed_on_destroy(self):
        original = self.api._get_long(self.hwnd, -4)
        facade = self.native.NativeWindows()
        facade.configure_overlay(self.hwnd)
        installed = self.api._get_long(self.hwnd, -4)
        self.assertNotEqual(installed, original)
        self.api.configure_overlay(self.hwnd)
        self.assertEqual(self.api._get_long(self.hwnd, -4), installed)
        del facade
        gc.collect()
        self.assertEqual(self.send_message(self.hwnd, 0x21, self.hwnd, (0x201 << 16) | 1), 3)
        self.overlay.title("Native test Unicode \u6587\u4ef6")
        buffer = ctypes.create_unicode_buffer(128)
        self.api._user32.GetWindowTextW(self.hwnd, buffer, len(buffer))
        self.assertEqual(buffer.value, self.overlay.title())
        self.overlay.destroy()
        self.host.update()
        self.assertFalse(self.api.is_window(self.hwnd))
        self.assertIsNone(self.api.rect(self.hwnd))
        self.assertNotIn(self.hwnd, self.native._subclass_procs)
        self.api.hide(self.hwnd)
        self.api.show_overlay(self.hwnd, 0, 0, 320, 64)

    def test_real_tk_minimize_and_restore_drives_visibility_policy(self):
        bounds = self.api.rect(self.host_hwnd)
        self.assertIsNotNone(bounds)
        self.host.iconify()
        self.host.update()
        self.assertTrue(self.api.is_minimized(self.host_hwnd))
        host = self.WindowInfo(self.host_hwnd, os.getpid(), "TEST", bounds, True, True)
        self.assertFalse(self.native.should_show(host, self.host_hwnd, {self.hwnd}))
        self.host.deiconify()
        self.host.focus_force()
        self.host.update()
        self.assertFalse(self.api.is_minimized(self.host_hwnd))
        host = dataclasses.replace(host, minimized=False)
        self.assertTrue(self.native.should_show(host, self.api.foreground_window(), {self.hwnd}))

    def test_real_own_process_query_uses_full_executable_path(self):
        self.assertEqual(self.api._process_basename(os.getpid()), Path(sys.executable).name.casefold())


if __name__ == "__main__":
    unittest.main()
