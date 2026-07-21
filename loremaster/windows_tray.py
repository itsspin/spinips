"""Dependency-free Windows notification-area control for Loremaster.

The native callback lives on a small daemon message-loop thread.  It never
touches Tk: commands cross the thread boundary through ``TrayCommandQueue``
and are applied by Loremaster's normal UI polling loop.
"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
from typing import Callable


TRAY_SHOW = "show"
TRAY_HIDE = "hide"
TRAY_EXIT = "exit"
TRAY_COMMANDS = frozenset((TRAY_SHOW, TRAY_HIDE, TRAY_EXIT))


def overlay_should_be_visible(*, hidden_to_tray: bool, wait_for_eq: bool,
                              eq_running: bool, manual_show: bool) -> bool:
    """Resolve runtime-only visibility without querying fragile Tk state."""
    return (not hidden_to_tray
            and (not wait_for_eq or eq_running or manual_show))


class TrayCommandQueue:
    """Thread-safe, bounded-at-drain bridge from Win32 to Tk."""

    def __init__(self) -> None:
        self._queue: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._exit_queued = False

    def emit(self, command: str) -> bool:
        if command not in TRAY_COMMANDS:
            return False
        with self._lock:
            if self._exit_queued:
                return False
            if command == TRAY_EXIT:
                self._exit_queued = True
            self._queue.put(command)
        return True

    def drain(self, limit: int = 16) -> list[str]:
        found: list[str] = []
        for _index in range(max(0, int(limit))):
            try:
                found.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return found


def build_brand_icon_planes(size: int = 32) -> tuple[bytes, bytes, int]:
    """Return top-down BGRA pixels and a 1-bit transparency mask.

    The low-detail hex/ember mark remains legible at notification-area sizes.
    This is generated once in memory, so source runs need no Pillow or asset
    lookup and a PyInstaller one-file build needs no data-file extraction.
    """
    size = int(size)
    if not 16 <= size <= 64:
        raise ValueError("tray icon size must be between 16 and 64 pixels")
    scale = size / 32.0
    cx = cy = (size - 1) / 2.0
    vertices = (
        (cx, cy - 14.0 * scale),
        (cx + 12.2 * scale, cy - 7.0 * scale),
        (cx + 12.2 * scale, cy + 7.0 * scale),
        (cx, cy + 14.0 * scale),
        (cx - 12.2 * scale, cy + 7.0 * scale),
        (cx - 12.2 * scale, cy - 7.0 * scale),
    )

    def inside_polygon(px: float, py: float) -> bool:
        inside = False
        previous = len(vertices) - 1
        for current, (x1, y1) in enumerate(vertices):
            x0, y0 = vertices[previous]
            crosses = ((y1 > py) != (y0 > py))
            if crosses and px < ((x0 - x1) * (py - y1) / (y0 - y1) + x1):
                inside = not inside
            previous = current
        return inside

    def edge_distance(px: float, py: float) -> float:
        best = float("inf")
        for index, (x0, y0) in enumerate(vertices):
            x1, y1 = vertices[(index + 1) % len(vertices)]
            dx, dy = x1 - x0, y1 - y0
            length_sq = dx * dx + dy * dy
            amount = 0.0 if length_sq == 0 else max(
                0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / length_sq))
            qx, qy = x0 + amount * dx, y0 + amount * dy
            best = min(best, ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5)
        return best

    # DIB color rows are top-down. CreateBitmap's 1-bpp rows are WORD aligned.
    pixels = bytearray(size * size * 4)
    mask_stride = ((size + 15) // 16) * 2
    mask = bytearray(b"\xff" * (mask_stride * size))
    gold = (42, 158, 219, 255)       # BGRA #db9e2a
    gold_bright = (95, 205, 250, 255)  # #facd5f
    obsidian = (17, 12, 9, 255)      # #090c11
    cyan = (190, 218, 52, 255)       # #34dabe
    ember = (45, 100, 229, 255)      # #e5642d

    for y in range(size):
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            if not inside_polygon(px, py):
                continue
            mask[y * mask_stride + x // 8] &= ~(0x80 >> (x % 8))
            distance = edge_distance(px, py)
            radius = max(abs(px - cx), abs(py - cy))
            if distance <= 1.55 * scale:
                color = gold_bright if y < cy else gold
            elif abs(px - cx) + abs(py - cy) <= 4.6 * scale:
                color = ember if abs(px - cx) + abs(py - cy) <= 1.8 * scale else cyan
            elif radius <= 8.0 * scale and ((x + y) % max(2, int(4 * scale)) == 0):
                color = (27, 34, 23, 255)  # restrained teal-black texture
            else:
                color = obsidian
            offset = (y * size + x) * 4
            pixels[offset:offset + 4] = bytes(color)
    return bytes(pixels), bytes(mask), mask_stride


class WindowsTrayIcon:
    """A native Shell_NotifyIcon icon with no runtime dependencies."""

    def __init__(self, tooltip: str = "Spin's Loremaster") -> None:
        self.tooltip = str(tooltip or "Spin's Loremaster")[:127]
        self.commands = TrayCommandQueue()
        self.error = ""
        self._active = False
        self._added = False
        self._closing = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._hwnd = 0
        self._thread_id = 0
        self._wndproc = None
        self._hicon = 0
        self._owns_icon = False
        self._notify_data = None
        self._shell32 = self._user32 = self._gdi32 = self._kernel32 = None

    @property
    def active(self) -> bool:
        return bool(self._active)

    def start(self, timeout: float = 1.5) -> bool:
        if os.name != "nt":
            return False
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.active
            if self._closing.is_set():
                return False
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._run, name="LoremasterTray", daemon=True)
            self._thread.start()
        self._ready.wait(max(0.05, float(timeout)))
        return self.active

    def poll(self, limit: int = 16) -> list[str]:
        return self.commands.drain(limit)

    def close(self, timeout: float = 1.0) -> None:
        if self._closing.is_set():
            thread = self._thread
            if thread and thread.is_alive() and threading.current_thread() is not thread:
                thread.join(max(0.0, float(timeout)))
            return
        self._closing.set()
        if os.name == "nt":
            try:
                user32 = self._user32 or ctypes.WinDLL("user32", use_last_error=True)
                if self._hwnd:
                    user32.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_CLOSE
                elif self._thread_id:
                    user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
            except (AttributeError, OSError):
                pass
        thread = self._thread
        if thread and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(max(0.0, float(timeout)))

    def _run(self) -> None:
        try:
            self._run_native()
        except Exception as exc:  # tray failure must never prevent log tracking
            self.error = str(exc)[:240]
        finally:
            self._delete_icon()
            if self._hwnd and self._user32 is not None:
                try:
                    if self._user32.IsWindow(self._hwnd):
                        self._user32.DestroyWindow(self._hwnd)
                except (AttributeError, OSError):
                    pass
            self._hwnd = 0
            self._active = False
            self._ready.set()

    def _run_native(self) -> None:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32, self._shell32 = user32, shell32
        self._gdi32, self._kernel32 = gdi32, kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())

        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WndClass(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        class Guid(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8),
            ]

        class NotifyIconData(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", Guid),
                ("hBalloonIcon", wintypes.HICON),
            ]

        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WndClass)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterWindowMessageW.restype = wintypes.UINT
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.CreatePopupMenu.argtypes = []
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.AppendMenuW.argtypes = [
            wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        user32.AppendMenuW.restype = wintypes.BOOL
        user32.TrackPopupMenuEx.argtypes = [
            wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.LPVOID]
        user32.TrackPopupMenuEx.restype = wintypes.UINT
        user32.DestroyMenu.argtypes = [wintypes.HMENU]
        user32.DestroyMenu.restype = wintypes.BOOL
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND,
            wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = LRESULT
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        shell32.Shell_NotifyIconW.argtypes = [
            wintypes.DWORD, ctypes.POINTER(NotifyIconData)]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL

        callback_message = 0x8000 + 0x53  # WM_APP + 'S'
        taskbar_created = int(user32.RegisterWindowMessageW("TaskbarCreated"))
        command_ids = {1001: TRAY_SHOW, 1002: TRAY_HIDE, 1003: TRAY_EXIT}

        def show_menu(hwnd: int) -> None:
            menu = user32.CreatePopupMenu()
            if not menu:
                return
            try:
                user32.AppendMenuW(menu, 0x0000, 1001, "OPEN LOREMASTER")
                user32.AppendMenuW(menu, 0x0000, 1002, "HIDE HUD")
                user32.AppendMenuW(menu, 0x0800, 0, None)
                user32.AppendMenuW(menu, 0x0000, 1003, "EXIT LOREMASTER")
                point = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(point))
                user32.SetForegroundWindow(hwnd)
                selected = int(user32.TrackPopupMenuEx(
                    menu, 0x0100 | 0x0002, point.x, point.y, hwnd, None))
                user32.PostMessageW(hwnd, 0x0000, 0, 0)  # WM_NULL dismisses reliably
                command = command_ids.get(selected)
                if command:
                    self.commands.emit(command)
            finally:
                user32.DestroyMenu(menu)

        def add_icon() -> bool:
            data = NotifyIconData()
            data.cbSize = ctypes.sizeof(NotifyIconData)
            data.hWnd = self._hwnd
            data.uID = 1
            data.uFlags = 0x0001 | 0x0002 | 0x0004
            data.uCallbackMessage = callback_message
            data.hIcon = self._hicon
            data.szTip = self.tooltip
            if not shell32.Shell_NotifyIconW(0x00000000, ctypes.byref(data)):  # NIM_ADD
                return False
            data.uVersion = 4
            shell32.Shell_NotifyIconW(0x00000004, ctypes.byref(data))  # NIM_SETVERSION
            self._notify_data = data
            self._added = True
            return True

        def window_proc(hwnd, message, w_param, l_param):
            try:
                if message == callback_message:
                    event = int(l_param) & 0xFFFF
                    if event in (0x0202, 0x0203, 0x0400, 0x0401):
                        self.commands.emit(TRAY_SHOW)
                        return 0
                    if event in (0x0205, 0x007B):
                        show_menu(hwnd)
                        return 0
                if taskbar_created and message == taskbar_created:
                    self._added = False
                    add_icon()
                    return 0
                if message == 0x0010:  # WM_CLOSE
                    user32.DestroyWindow(hwnd)
                    return 0
                if message == 0x0002:  # WM_DESTROY
                    self._delete_icon()
                    user32.PostQuitMessage(0)
                    return 0
            except Exception as exc:
                self.error = str(exc)[:240]
            return user32.DefWindowProcW(hwnd, message, w_param, l_param)

        self._wndproc = WNDPROC(window_proc)
        instance = kernel32.GetModuleHandleW(None)
        class_name = f"SpinsLoremasterTray.{os.getpid()}"
        wc = WndClass()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = instance
        wc.lpszClassName = class_name
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            raise OSError(ctypes.get_last_error(), "RegisterClassW failed")
        self._hicon, self._owns_icon = self._create_brand_icon(user32, gdi32)
        self._hwnd = int(user32.CreateWindowExW(
            0, class_name, "Spin's Loremaster Tray", 0, 0, 0, 0, 0,
            None, None, instance, None) or 0)
        if not self._hwnd:
            raise OSError(ctypes.get_last_error(), "CreateWindowExW failed")
        if not add_icon():
            raise OSError(ctypes.get_last_error(), "Shell_NotifyIconW failed")
        self._active = True
        self._ready.set()

        message = wintypes.MSG()
        while not self._closing.is_set():
            result = int(user32.GetMessageW(ctypes.byref(message), None, 0, 0))
            if result <= 0:
                break
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        self._delete_icon()
        if self._hwnd and user32.IsWindow(self._hwnd):
            user32.DestroyWindow(self._hwnd)
        self._hwnd = 0

    def _create_brand_icon(self, user32, gdi32) -> tuple[int, bool]:
        from ctypes import wintypes

        class BitmapInfoHeader(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class BitmapInfo(ctypes.Structure):
            _fields_ = [("bmiHeader", BitmapInfoHeader),
                        ("bmiColors", wintypes.DWORD * 1)]

        class IconInfo(ctypes.Structure):
            _fields_ = [
                ("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                ("hbmColor", wintypes.HBITMAP),
            ]

        try:
            gdi32.CreateDIBSection.argtypes = [
                wintypes.HDC, ctypes.POINTER(BitmapInfo), wintypes.UINT,
                ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
            gdi32.CreateDIBSection.restype = wintypes.HBITMAP
            gdi32.CreateBitmap.argtypes = [
                ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT,
                wintypes.LPVOID]
            gdi32.CreateBitmap.restype = wintypes.HBITMAP
            gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
            gdi32.DeleteObject.restype = wintypes.BOOL
            user32.CreateIconIndirect.argtypes = [ctypes.POINTER(IconInfo)]
            user32.CreateIconIndirect.restype = wintypes.HICON
            user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
            user32.LoadIconW.restype = wintypes.HICON
            user32.DestroyIcon.argtypes = [wintypes.HICON]
            user32.DestroyIcon.restype = wintypes.BOOL
            pixels, mask, _mask_stride = build_brand_icon_planes(32)
            info = BitmapInfo()
            info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
            info.bmiHeader.biWidth = 32
            info.bmiHeader.biHeight = -32
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            bits = ctypes.c_void_p()
            color = gdi32.CreateDIBSection(
                None, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
            if not color or not bits.value:
                raise OSError("CreateDIBSection failed")
            ctypes.memmove(bits.value, pixels, len(pixels))
            mask_buffer = (ctypes.c_ubyte * len(mask)).from_buffer_copy(mask)
            mask_bitmap = gdi32.CreateBitmap(32, 32, 1, 1, mask_buffer)
            if not mask_bitmap:
                gdi32.DeleteObject(color)
                raise OSError("CreateBitmap failed")
            icon_info = IconInfo(True, 0, 0, mask_bitmap, color)
            icon = user32.CreateIconIndirect(ctypes.byref(icon_info))
            gdi32.DeleteObject(mask_bitmap)
            gdi32.DeleteObject(color)
            if not icon:
                raise OSError("CreateIconIndirect failed")
            return int(icon), True
        except (AttributeError, OSError, ValueError):
            # MAKEINTRESOURCEW(IDI_APPLICATION)
            icon = user32.LoadIconW(None, ctypes.cast(
                ctypes.c_void_p(32512), wintypes.LPCWSTR))
            return int(icon or 0), False

    def _delete_icon(self) -> None:
        if self._added and self._shell32 is not None and self._notify_data is not None:
            try:
                self._shell32.Shell_NotifyIconW(
                    0x00000002, ctypes.byref(self._notify_data))  # NIM_DELETE
            except (AttributeError, OSError):
                pass
        self._added = False
        if self._hicon and self._owns_icon and self._user32 is not None:
            try:
                self._user32.DestroyIcon(self._hicon)
            except (AttributeError, OSError):
                pass
        self._hicon = 0
        self._owns_icon = False
