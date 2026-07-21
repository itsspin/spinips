"""Reliable native global-hotkey ownership for Loremaster on Windows.

Tk owns and drains the main thread's Win32 message queue.  Registering a
thread-level hotkey there and attempting to ``PeekMessage`` from a Tk timer is
racy: Tk can consume ``WM_HOTKEY`` first.  This module gives both Loremaster
hotkeys one dedicated hidden window and one native message-loop thread.  The
window procedure only places small command strings on a thread-safe queue;
Tk remains the sole owner of every UI object.
"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
from dataclasses import dataclass


HOTKEY_RECOVERY = "recovery"
HOTKEY_WIKI = "wiki"
HOTKEY_COMMANDS = frozenset((HOTKEY_RECOVERY, HOTKEY_WIKI))

WM_HOTKEY = 0x0312
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_APP_REBIND = 0x8000 + 0x48  # WM_APP + 'H'


@dataclass(frozen=True)
class HotkeyBinding:
    command: str
    identifier: int
    modifiers: int
    virtual_key: int
    label: str

    def __post_init__(self) -> None:
        if self.command not in HOTKEY_COMMANDS:
            raise ValueError(f"unknown hotkey command: {self.command}")
        if not 1 <= int(self.identifier) <= 0xBFFF:
            raise ValueError("hotkey identifier must be between 1 and 0xBFFF")
        if not int(self.modifiers) or not int(self.virtual_key):
            raise ValueError("hotkey modifiers and key are required")


@dataclass(frozen=True)
class HotkeyStatus:
    binding: HotkeyBinding
    registered: bool
    error: str = ""


@dataclass(frozen=True)
class HotkeyRebindResult:
    success: bool
    status: HotkeyStatus


@dataclass
class _RebindRequest:
    binding: HotkeyBinding
    done: threading.Event
    result: HotkeyRebindResult | None = None


class HotkeyCommandQueue:
    """Tiny thread-safe bridge from the native window procedure to Tk."""

    def __init__(self) -> None:
        self._queue: queue.SimpleQueue[str] = queue.SimpleQueue()

    def emit(self, command: str) -> bool:
        if command not in HOTKEY_COMMANDS:
            return False
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


class WindowsHotkeyService:
    """Own process-lifetime hotkeys on a dedicated Win32 window thread."""

    def __init__(self, recovery: HotkeyBinding, wiki: HotkeyBinding) -> None:
        if recovery.command != HOTKEY_RECOVERY or wiki.command != HOTKEY_WIKI:
            raise ValueError("recovery and wiki bindings are in the wrong slots")
        if recovery.identifier == wiki.identifier:
            raise ValueError("hotkey identifiers must be unique")
        self.commands = HotkeyCommandQueue()
        self.error = ""
        self._bindings = {
            HOTKEY_RECOVERY: recovery,
            HOTKEY_WIKI: wiki,
        }
        self._statuses = {
            command: HotkeyStatus(binding, False, "Not started")
            for command, binding in self._bindings.items()
        }
        self._state_lock = threading.Lock()
        self._requests: queue.SimpleQueue[_RebindRequest] = queue.SimpleQueue()
        self._ready = threading.Event()
        self._closing = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hwnd = 0
        self._wndproc = None
        self._user32 = None

    @property
    def active(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive() and self._hwnd)

    @property
    def window_handle(self) -> int:
        """Native handle exposed for deterministic Windows smoke tests."""
        return int(self._hwnd)

    def start(self, timeout: float = 1.5) -> bool:
        if os.name != "nt":
            self._set_all_unavailable("Global hotkeys are available on Windows only.")
            return False
        if self._thread and self._thread.is_alive():
            return self.active
        if self._closing.is_set():
            return False
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, name="LoremasterHotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(max(0.05, float(timeout)))
        return self.active

    def poll(self, limit: int = 16) -> list[str]:
        return self.commands.drain(limit)

    def status(self, command: str) -> HotkeyStatus:
        with self._state_lock:
            return self._statuses[command]

    def rebind_wiki(self, modifiers: int, virtual_key: int, label: str,
                    timeout: float = 1.0) -> HotkeyRebindResult:
        current = self.status(HOTKEY_WIKI)
        binding = HotkeyBinding(
            HOTKEY_WIKI, current.binding.identifier,
            int(modifiers), int(virtual_key), str(label),
        )
        if not self.active or self._user32 is None:
            status = HotkeyStatus(binding, False, self.error or "Hotkey service is unavailable.")
            return HotkeyRebindResult(False, status)
        request = _RebindRequest(binding, threading.Event())
        self._requests.put(request)
        try:
            posted = bool(self._user32.PostMessageW(
                self._hwnd, WM_APP_REBIND, 0, 0))
        except (AttributeError, OSError):
            posted = False
        if not posted or not request.done.wait(max(0.05, float(timeout))):
            status = self.status(HOTKEY_WIKI)
            return HotkeyRebindResult(False, HotkeyStatus(
                status.binding, status.registered,
                "Hotkey rebind did not reach the Windows owner thread."))
        return request.result or HotkeyRebindResult(False, self.status(HOTKEY_WIKI))

    def close(self, timeout: float = 1.0) -> None:
        if self._closing.is_set():
            thread = self._thread
            if thread and thread.is_alive() and threading.current_thread() is not thread:
                thread.join(max(0.0, float(timeout)))
            return
        self._closing.set()
        try:
            if self._user32 is not None and self._hwnd:
                self._user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            elif self._user32 is not None and self._thread_id:
                self._user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
        except (AttributeError, OSError):
            pass
        thread = self._thread
        if thread and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(max(0.0, float(timeout)))

    def _set_status(self, status: HotkeyStatus) -> None:
        with self._state_lock:
            self._bindings[status.binding.command] = status.binding
            self._statuses[status.binding.command] = status

    def _set_all_unavailable(self, error: str) -> None:
        self.error = str(error)[:240]
        for binding in tuple(self._bindings.values()):
            self._set_status(HotkeyStatus(binding, False, self.error))

    @staticmethod
    def _registration_error(binding: HotkeyBinding, code: int) -> str:
        if int(code) == 1409:  # ERROR_HOTKEY_ALREADY_REGISTERED
            return f"{binding.label} is already in use"
        if code:
            return f"{binding.label} could not be registered (Windows error {code})"
        return f"{binding.label} could not be registered"

    def _run(self) -> None:
        try:
            self._run_native()
        except Exception as exc:
            self._set_all_unavailable(str(exc) or "Native hotkey service failed.")
        finally:
            self._hwnd = 0
            self._ready.set()

    def _run_native(self) -> None:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32 = user32
        self._thread_id = int(kernel32.GetCurrentThreadId())

        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM)

        class WndClass(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WndClass)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.UnregisterClassW.restype = wintypes.BOOL
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostThreadMessageW.restype = wintypes.BOOL
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.RegisterHotKey.argtypes = [
            wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND,
            wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = LRESULT
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        registered: set[int] = set()

        def register(binding: HotkeyBinding) -> HotkeyStatus:
            ctypes.set_last_error(0)
            ok = bool(user32.RegisterHotKey(
                self._hwnd, binding.identifier,
                binding.modifiers, binding.virtual_key))
            if ok:
                registered.add(binding.identifier)
                return HotkeyStatus(binding, True, "")
            return HotkeyStatus(
                binding, False,
                self._registration_error(binding, ctypes.get_last_error()))

        def unregister(binding: HotkeyBinding) -> None:
            if binding.identifier in registered:
                user32.UnregisterHotKey(self._hwnd, binding.identifier)
                registered.discard(binding.identifier)

        def apply_rebind(request: _RebindRequest) -> None:
            old_status = self.status(HOTKEY_WIKI)
            old_binding = old_status.binding
            unregister(old_binding)
            new_status = register(request.binding)
            if new_status.registered:
                self._set_status(new_status)
                request.result = HotkeyRebindResult(True, new_status)
            else:
                # A failed customization must not silently destroy a working
                # shortcut. Restore the prior binding before reporting failure.
                restored = register(old_binding)
                self._set_status(restored)
                request.result = HotkeyRebindResult(False, HotkeyStatus(
                    restored.binding, restored.registered, new_status.error))
            request.done.set()

        def window_proc(hwnd, message, w_param, l_param):
            try:
                if message == WM_HOTKEY:
                    identifier = int(w_param)
                    for command, binding in tuple(self._bindings.items()):
                        if binding.identifier == identifier:
                            self.commands.emit(command)
                            return 0
                if message == WM_APP_REBIND:
                    while True:
                        try:
                            request = self._requests.get_nowait()
                        except queue.Empty:
                            break
                        apply_rebind(request)
                    return 0
                if message == WM_CLOSE:
                    user32.DestroyWindow(hwnd)
                    return 0
                if message == WM_DESTROY:
                    for binding in tuple(self._bindings.values()):
                        unregister(binding)
                    user32.PostQuitMessage(0)
                    return 0
            except Exception as exc:
                self.error = str(exc)[:240]
            return user32.DefWindowProcW(hwnd, message, w_param, l_param)

        self._wndproc = WNDPROC(window_proc)
        instance = kernel32.GetModuleHandleW(None)
        class_name = f"SpinsLoremasterHotkeys.{os.getpid()}.{id(self)}"
        wc = WndClass()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = instance
        wc.lpszClassName = class_name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            raise OSError(ctypes.get_last_error(), "RegisterClassW failed")
        try:
            # HWND_MESSAGE keeps the native owner invisible and out of Alt+Tab.
            message_parent = wintypes.HWND(-3)
            self._hwnd = int(user32.CreateWindowExW(
                0, class_name, "Spin's Loremaster Hotkeys", 0,
                0, 0, 0, 0, message_parent, None, instance, None) or 0)
            if not self._hwnd:
                raise OSError(ctypes.get_last_error(), "CreateWindowExW failed")
            for binding in tuple(self._bindings.values()):
                self._set_status(register(binding))
            self._ready.set()

            message = wintypes.MSG()
            while not self._closing.is_set():
                result = int(user32.GetMessageW(
                    ctypes.byref(message), None, 0, 0))
                if result <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
            if self._hwnd and user32.IsWindow(self._hwnd):
                user32.DestroyWindow(self._hwnd)
        finally:
            for binding in tuple(self._bindings.values()):
                unregister(binding)
            self._hwnd = 0
            user32.UnregisterClassW(class_name, instance)
