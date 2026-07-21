"""On-demand, non-invasive OCR for an EverQuest tooltip near the cursor.

The hotkey thread freezes a small physical-pixel screen region immediately
with typed Win32 GDI calls.  An immutable capture is then handed to one
bounded worker, which asks a hidden Windows PowerShell process to decode,
scale, and recognize it with Windows.Media.Ocr.  Nothing inspects or injects
into ``eqgame.exe`` and neither Tk nor network code runs on the OCR worker.
"""

from __future__ import annotations

import base64
import ctypes
import json
import ntpath
import os
import queue
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable


ROI_WIDTH = 960
ROI_HEIGHT = 720
OCR_SCALE = 2
OCR_TIMEOUT_SECONDS = 5.0
MAX_OCR_STDOUT_BYTES = 256 * 1024
MAX_CANDIDATES = 4
STALE_SCAN_SECONDS = 24 * 60 * 60
TEMP_ROOT_NAME = "SpinsLoremasterOCR"


@dataclass(frozen=True)
class OcrLine:
    text: str
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass(frozen=True)
class CaptureMetadata:
    """Small immutable placement/identity record safe to return to Tk."""

    cursor_x: int
    cursor_y: int
    region_left: int
    region_top: int
    region_width: int
    region_height: int
    foreground_hwnd: int
    foreground_pid: int
    captured_at: float

    @property
    def cursor_in_region(self) -> tuple[float, float]:
        return (float(self.cursor_x - self.region_left),
                float(self.cursor_y - self.region_top))


@dataclass(frozen=True)
class HoverCapture:
    """An immutable top-down 24-bit BMP and the exact EQ window it came from."""

    metadata: CaptureMetadata
    bmp_bytes: bytes
    luminance_range: int = 0


@dataclass
class HoverOcrResult:
    request_id: int
    candidates: list[str] = field(default_factory=list)
    lines: list[OcrLine] = field(default_factory=list)
    error: str = ""
    elapsed_seconds: float = 0.0
    capture: CaptureMetadata | None = None


class HoverCaptureError(RuntimeError):
    """A safe, short failure from the synchronous Win32 capture boundary."""


_PROPERTY_PREFIXES = (
    "ac", "agility", "agi", "attack", "attunable", "augment", "charisma",
    "cha", "class", "click effect", "damage", "delay", "description",
    "dex", "dexterity", "effect", "endurance", "focus effect", "haste",
    "heirloom", "hp", "int", "intelligence", "lore", "lore equipped",
    "magic item", "mana", "no drop", "no trade", "placeable", "prestige",
    "proc effect", "race", "recommended",
    "required", "size", "slot", "sta", "stamina", "str", "strength", "tier",
    "tribute", "unmodified", "value", "weight", "wis", "wisdom", "worn effect",
    "wt",
)
_UI_NOISE = {
    "inventory", "equipment", "pet", "loadouts", "storage", "destroy",
    "skills", "achievements", "find item", "done", "inspect", "close",
    "merge", "merge place", "place item",
    "charm", "ear", "head", "face", "neck", "shoulders", "arms", "back",
    "wrist", "range", "hands", "primary", "secondary", "finger", "chest",
    "legs", "feet", "waist", "ammo",
}


def _clean_candidate(value: str) -> str:
    value = (value or "").replace("\u2018", "'").replace("\u2019", "'")
    value = value.replace("\u201c", '"').replace("\u201d", '"')
    value = re.sub(r"^[\s\[\]<>|]+|[\s\[\]<>|]+$", "", value)
    value = re.sub(r"\s+", " ", value).strip(" .,:;-_")
    # Legends' upgraded item ranks resolve to the base EQL Wiki page.
    return re.sub(r"\s+\+\d+\s*$", "", value).strip()


def _candidate_score(line: OcrLine, cursor_x: float, cursor_y: float) -> float | None:
    text = _clean_candidate(line.text)
    folded = text.casefold()
    if not 3 <= len(text) <= 90 or not re.search(r"[A-Za-z]", text):
        return None
    if folded in _UI_NOISE or folded.startswith("tier ") or any(
            folded == prefix or folded.startswith(prefix + ":")
            for prefix in _PROPERTY_PREFIXES):
        return None
    if re.search(r"(?:\b\d+\s*%|[+:=]\s*[-+]?\d|\b\d+\s*/\s*\d+|\bEMPTY\b)",
                 text, re.I):
        return None
    if any(mark in text for mark in ("{", "}", "\\", "/", "@", "=")):
        return None
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'`-]*", text)
    if not 1 <= len(words) <= 9:
        return None
    # Wrapped class lists in Legends tooltips are otherwise plausible-looking
    # all-caps candidates (for example ``SHM BST BER``).
    class_codes = {
        "WAR", "CLR", "PAL", "RNG", "SHD", "DRU", "MNK", "BRD",
        "ROG", "SHM", "NEC", "WIZ", "MAG", "ENC", "BST", "BER",
    }
    if len(words) >= 2 and all(word.upper() in class_codes for word in words):
        return None
    title_words = sum(1 for word in words if word[:1].isupper())
    score = min(18.0, max(0.0, line.height) * 1.4)
    score += 8.0 if 2 <= len(words) <= 6 else 2.0
    score += 7.0 * title_words / len(words)
    if text.isupper():
        score -= 4.0
    center_x = line.x + line.width / 2.0
    center_y = line.y + line.height / 2.0
    distance = ((center_x - cursor_x) ** 2 + (center_y - cursor_y) ** 2) ** 0.5
    score += max(0.0, 9.0 - distance / 75.0)
    if re.search(r"\b(?:cloak|robe|belt|ring|earring|mask|helm|boots|gloves|"
                 r"bracer|shield|sword|dagger|staff|bow|axe|mace|tunic|"
                 r"breastplate|leggings|greaves|necklace|amulet)\b", text, re.I):
        score += 5.0
    return score


def rank_item_candidates(lines: Iterable[OcrLine], cursor_x: float = 0.0,
                         cursor_y: float = 0.0,
                         limit: int = MAX_CANDIDATES) -> list[str]:
    """Rank conservative candidates; the Wiki client validates Itempages."""
    if limit <= 0:
        return []
    scored: dict[str, tuple[float, int, str, int]] = {}
    for index, line in enumerate(lines):
        candidate = _clean_candidate(line.text)
        key = candidate.casefold()
        score = _candidate_score(line, cursor_x, cursor_y)
        if score is None:
            continue
        previous = scored.get(key)
        if previous is None:
            scored[key] = (score, -index, candidate, 1)
        else:
            best, first_index, original, count = previous
            count += 1
            scored[key] = (max(best, score) + min(6.0, 2.5 * (count - 1)),
                           first_index, original, count)
    ordered = sorted(scored.values(), reverse=True)
    return [candidate for _score, _index, candidate, _count in ordered[:limit]]


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class _RgbQuad(ctypes.Structure):
    _fields_ = [("blue", ctypes.c_ubyte), ("green", ctypes.c_ubyte),
                ("red", ctypes.c_ubyte), ("reserved", ctypes.c_ubyte)]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", _RgbQuad * 1)]


def _typed_win32_libraries():
    """Load the exact small Win32 surface needed by synchronous capture."""
    if os.name != "nt":
        raise HoverCaptureError("Hover scan is available on Windows only.")
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetCursorPos.argtypes = [ctypes.POINTER(_Point)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    if hasattr(user32, "SetThreadDpiAwarenessContext"):
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC, ctypes.POINTER(_BitmapInfo), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = [
        wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.GdiFlush.argtypes = []
    gdi32.GdiFlush.restype = wintypes.BOOL

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return user32, gdi32, kernel32


def _process_image_name(kernel32, pid: int) -> str:
    from ctypes import wintypes

    process = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
    if not process:
        raise HoverCaptureError("EverQuest process identity could not be verified.")
    try:
        capacity = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        if not kernel32.QueryFullProcessImageNameW(
                process, 0, buffer, ctypes.byref(capacity)):
            raise HoverCaptureError("EverQuest process identity could not be verified.")
        return ntpath.basename(buffer.value).casefold()
    finally:
        kernel32.CloseHandle(process)


def _bmp_from_bgr(pixels: bytes, width: int, height: int, stride: int) -> bytes:
    """Build a top-down 24-bit BMP understood by Windows BitmapDecoder."""
    expected = stride * height
    if width <= 0 or height <= 0 or stride < width * 3 or len(pixels) != expected:
        raise ValueError("Invalid captured bitmap dimensions.")
    offset = 14 + 40
    file_size = offset + expected
    file_header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, offset)
    info_header = struct.pack(
        "<IiiHHIIiiII", 40, width, -height, 1, 24, 0, expected,
        3780, 3780, 0, 0)
    return file_header + info_header + pixels


def _luminance_range(pixels: bytes, width: int, height: int, stride: int,
                     sample_limit: int = 8192) -> int:
    """Cheaply reject the uniform black/stale frames common in exclusive mode."""
    total = width * height
    if total <= 0:
        return 0
    step = max(1, total // max(1, sample_limit))
    low, high = 255, 0
    view = memoryview(pixels)
    for pixel_index in range(0, total, step):
        row, column = divmod(pixel_index, width)
        offset = row * stride + column * 3
        blue, green, red = view[offset], view[offset + 1], view[offset + 2]
        luminance = (2 * int(red) + 5 * int(green) + int(blue)) // 8
        low = min(low, luminance)
        high = max(high, luminance)
    return max(0, high - low)


def capture_hovered_tooltip() -> HoverCapture:
    """Synchronously freeze the cursor ROI from the exact foreground EQ HWND."""
    user32, gdi32, kernel32 = _typed_win32_libraries()
    from ctypes import wintypes

    previous_dpi = None
    set_dpi = getattr(user32, "SetThreadDpiAwarenessContext", None)
    if set_dpi is not None:
        previous_dpi = set_dpi(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    screen_dc = memory_dc = bitmap = old_bitmap = None
    try:
        foreground = user32.GetForegroundWindow()
        if not foreground:
            raise HoverCaptureError("EverQuest is not in the foreground.")
        pid = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid)) or not pid.value:
            raise HoverCaptureError("EverQuest foreground window could not be verified.")
        if _process_image_name(kernel32, int(pid.value)) != "eqgame.exe":
            raise HoverCaptureError("Hover scan only captures EverQuest.")

        cursor = _Point()
        if not user32.GetCursorPos(ctypes.byref(cursor)):
            raise HoverCaptureError("Cursor position is unavailable.")
        virtual_x = user32.GetSystemMetrics(76)
        virtual_y = user32.GetSystemMetrics(77)
        virtual_width = user32.GetSystemMetrics(78)
        virtual_height = user32.GetSystemMetrics(79)
        if virtual_width <= 0 or virtual_height <= 0:
            raise HoverCaptureError("Windows desktop bounds are unavailable.")
        width = min(ROI_WIDTH, virtual_width)
        height = min(ROI_HEIGHT, virtual_height)
        left = max(virtual_x, min(
            int(cursor.x) - width // 2, virtual_x + virtual_width - width))
        top = max(virtual_y, min(
            int(cursor.y) - height // 2, virtual_y + virtual_height - height))

        stride = (width * 3 + 3) & ~3
        image_size = stride * height
        info = _BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # top-down: no later row reversal
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 24
        info.bmiHeader.biCompression = 0  # BI_RGB
        info.bmiHeader.biSizeImage = image_size
        bits = ctypes.c_void_p()

        screen_dc = user32.GetDC(None)
        if not screen_dc:
            raise HoverCaptureError("Windows could not open the screen for hover scan.")
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        if not memory_dc:
            raise HoverCaptureError("Windows could not prepare the hover capture.")
        bitmap = gdi32.CreateDIBSection(
            screen_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            raise HoverCaptureError("Windows could not allocate the hover capture.")
        old_bitmap = gdi32.SelectObject(memory_dc, bitmap)
        if not old_bitmap or int(old_bitmap) == -1:
            raise HoverCaptureError("Windows could not select the hover capture.")
        if not gdi32.BitBlt(
                memory_dc, 0, 0, width, height, screen_dc, left, top,
                0x00CC0020 | 0x40000000):  # SRCCOPY | CAPTUREBLT
            raise HoverCaptureError(
                "The EQ frame could not be captured; try windowed or borderless mode.")
        gdi32.GdiFlush()
        pixels = bytes(ctypes.string_at(bits.value, image_size))
        variance = _luminance_range(pixels, width, height, stride)
        if variance < 6:
            raise HoverCaptureError(
                "The EQ frame was blank; try windowed or borderless mode.")
        metadata = CaptureMetadata(
            cursor_x=int(cursor.x), cursor_y=int(cursor.y),
            region_left=left, region_top=top,
            region_width=width, region_height=height,
            foreground_hwnd=int(foreground), foreground_pid=int(pid.value),
            captured_at=time.time(),
        )
        return HoverCapture(
            metadata=metadata,
            bmp_bytes=_bmp_from_bgr(pixels, width, height, stride),
            luminance_range=variance,
        )
    finally:
        if old_bitmap and memory_dc:
            gdi32.SelectObject(memory_dc, old_bitmap)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if screen_dc:
            user32.ReleaseDC(None, screen_dc)
        if set_dpi is not None and previous_dpi:
            set_dpi(previous_dpi)


def _powershell_script() -> str:
    """Return decode/scale/OCR only; pixels were already frozen by Python."""
    return rf"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
trap {{ [Console]::Error.Write([string]$_.Exception.Message); exit 2 }}
[void][System.Reflection.Assembly]::LoadWithPartialName('System.Runtime.WindowsRuntime')
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapTransform, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$asTaskMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{
  $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and
  $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1
}} | Select-Object -First 1
function Await-WinRt($operation, [Type]$resultType) {{
  $method = $asTaskMethod.MakeGenericMethod($resultType)
  $task = $method.Invoke($null, @($operation))
  if (-not $task.Wait(3500)) {{ throw 'Windows OCR operation timed out.' }}
  return $task.Result
}}
$imagePath = $env:SPIN_LOREMASTER_OCR_PATH
$file = Await-WinRt ([Windows.Storage.StorageFile]::GetFileFromPathAsync($imagePath)) ([Windows.Storage.StorageFile])
$stream = Await-WinRt ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
try {{
  $decoder = Await-WinRt ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  if ($null -eq $engine) {{ throw 'No Windows OCR language is installed.' }}
  $maxDimension = [double][Windows.Media.Ocr.OcrEngine]::MaxImageDimension
  $scale = [Math]::Min([double]{OCR_SCALE}, [Math]::Min($maxDimension / [double]$decoder.PixelWidth, $maxDimension / [double]$decoder.PixelHeight))
  $scaledWidth = [uint32][Math]::Max(1, [Math]::Round([double]$decoder.PixelWidth * $scale))
  $scaledHeight = [uint32][Math]::Max(1, [Math]::Round([double]$decoder.PixelHeight * $scale))
  $transform = [Windows.Graphics.Imaging.BitmapTransform]::new()
  $transform.ScaledWidth = $scaledWidth
  $transform.ScaledHeight = $scaledHeight
  $transform.InterpolationMode = [Windows.Graphics.Imaging.BitmapInterpolationMode]::Fant
  # Windows PowerShell's dynamic binder only exposes the shorter projected
  # overloads reliably. Select the exact transform overload so the worker can
  # scale the already-captured pixels without performing another screen read.
  $decodeMethod = [Windows.Graphics.Imaging.BitmapDecoder].GetMethod(
    'GetSoftwareBitmapAsync',
    [Type[]]@(
      [Windows.Graphics.Imaging.BitmapPixelFormat],
      [Windows.Graphics.Imaging.BitmapAlphaMode],
      [Windows.Graphics.Imaging.BitmapTransform],
      [Windows.Graphics.Imaging.ExifOrientationMode],
      [Windows.Graphics.Imaging.ColorManagementMode]
    )
  )
  if ($null -eq $decodeMethod) {{ throw 'Windows bitmap scaling API is unavailable.' }}
  $bitmapOperation = $decodeMethod.Invoke($decoder, [object[]]@(
    [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
    [Windows.Graphics.Imaging.BitmapAlphaMode]::Ignore,
    $transform,
    [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExif,
    [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage
  ))
  $bitmap = Await-WinRt $bitmapOperation ([Windows.Graphics.Imaging.SoftwareBitmap])
  try {{
    $result = Await-WinRt ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $scaleX = [double]$scaledWidth / [double]$decoder.PixelWidth
    $scaleY = [double]$scaledHeight / [double]$decoder.PixelHeight
    $lines = @()
    foreach ($line in $result.Lines) {{
      if ($lines.Count -ge 200) {{ break }}
      $words = @($line.Words)
      if ($words.Count -eq 0) {{ continue }}
      $minX = ($words | ForEach-Object {{ $_.BoundingRect.X }} | Measure-Object -Minimum).Minimum
      $minY = ($words | ForEach-Object {{ $_.BoundingRect.Y }} | Measure-Object -Minimum).Minimum
      $maxX = ($words | ForEach-Object {{ $_.BoundingRect.X + $_.BoundingRect.Width }} | Measure-Object -Maximum).Maximum
      $maxY = ($words | ForEach-Object {{ $_.BoundingRect.Y + $_.BoundingRect.Height }} | Measure-Object -Maximum).Maximum
      $lines += [pscustomobject]@{{
        text = [string]$line.Text
        x = [double]$minX / $scaleX
        y = [double]$minY / $scaleY
        width = [double]($maxX - $minX) / $scaleX
        height = [double]($maxY - $minY) / $scaleY
      }}
    }}
    [Console]::Out.Write(([pscustomobject]@{{ lines = $lines }} | ConvertTo-Json -Depth 4 -Compress))
  }} finally {{ if ($null -ne $bitmap) {{ $bitmap.Dispose() }} }}
}} finally {{ $stream.Dispose() }}
"""


def _powershell_path() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.exists() else (shutil.which("powershell.exe") or "powershell.exe")


def _temp_root() -> Path:
    return Path(tempfile.gettempdir()) / TEMP_ROOT_NAME


def cleanup_stale_scan_dirs(root: Path | None = None, *, now: float | None = None,
                            max_age: float = STALE_SCAN_SECONDS) -> int:
    """Remove only our known old crash remnants; never recursively delete."""
    root = Path(root) if root is not None else _temp_root()
    now = time.time() if now is None else float(now)
    removed = 0
    try:
        children = list(root.iterdir())
    except OSError:
        return 0
    for child in children:
        if (child.is_symlink() or not child.is_dir()
                or not re.fullmatch(r"scan-[0-9a-f]{32}", child.name)):
            continue
        try:
            if now - child.stat().st_mtime < max(0.0, float(max_age)):
                continue
            image = child / "cursor-region.bmp"
            if image.exists() and image.is_file() and not image.is_symlink():
                image.unlink()
            child.rmdir()
            removed += 1
        except OSError:
            continue
    try:
        root.rmdir()
    except OSError:
        pass
    return removed


def scan_hovered_tooltip(capture: HoverCapture,
                         cancel_event: threading.Event | None = None,
                         timeout: float = OCR_TIMEOUT_SECONDS) -> tuple[list[str], list[OcrLine]]:
    """Decode and OCR an already-frozen capture in one hidden worker process."""
    if os.name != "nt":
        raise RuntimeError("Hover scan is available on Windows only.")
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("Hover scan cancelled.")
    script = _powershell_script()
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    temp_root = _temp_root()
    temp_root.mkdir(parents=True, exist_ok=True)
    request_dir = temp_root / ("scan-" + uuid.uuid4().hex)
    request_dir.mkdir()
    image_path = request_dir / "cursor-region.bmp"
    env = os.environ.copy()
    env["SPIN_LOREMASTER_OCR_PATH"] = str(image_path)
    startupinfo = None
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    process = None
    try:
        image_path.write_bytes(capture.bmp_bytes)
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Hover scan cancelled.")
        process = subprocess.Popen(
            [_powershell_path(), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-EncodedCommand", encoded],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, startupinfo=startupinfo,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                           | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)),
        )
        deadline = time.monotonic() + max(1.0, float(timeout))
        while True:
            if cancel_event is not None and cancel_event.is_set():
                process.kill()
                process.communicate()
                raise RuntimeError("Hover scan cancelled.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.communicate()
                raise RuntimeError("Hover scan timed out; type or copy the item name instead.")
            try:
                stdout, stderr = process.communicate(timeout=min(0.05, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            detail = stderr.decode("utf-8", errors="replace").strip()
            detail = re.sub(r"\s+", " ", detail)[:180]
            raise RuntimeError(detail or "Windows OCR could not read the hovered tooltip.")
        if len(stdout) > MAX_OCR_STDOUT_BYTES:
            raise RuntimeError("Windows OCR returned too much text.")
        payload = json.loads(stdout.decode("utf-8", errors="strict") or "{}")
        rows = payload.get("lines", []) if isinstance(payload, dict) else []
        lines = [OcrLine(
            text=str(row.get("text", ""))[:160],
            x=float(row.get("x", 0.0)), y=float(row.get("y", 0.0)),
            width=float(row.get("width", 0.0)), height=float(row.get("height", 0.0)),
        ) for row in rows[:200] if isinstance(row, dict)]
        local_cursor = capture.metadata.cursor_in_region
        candidates = rank_item_candidates(lines, *local_cursor)
        return candidates, lines
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Windows OCR could not read the hovered tooltip.") from exc
    finally:
        try:
            image_path.unlink(missing_ok=True)
            request_dir.rmdir()
            temp_root.rmdir()
        except OSError:
            pass


class HoverOcrService:
    """One bounded OCR worker; capture itself is synchronous and immutable."""

    def __init__(
            self,
            scanner: Callable[[HoverCapture, threading.Event],
                              tuple[list[str], list[OcrLine]]] = scan_hovered_tooltip,
            capture_factory: Callable[[], HoverCapture] = capture_hovered_tooltip):
        self.scanner = scanner
        self.capture_factory = capture_factory
        self.requests: queue.Queue[
            tuple[int, HoverCapture, threading.Event] | None
        ] = queue.Queue(maxsize=1)
        self.results: queue.Queue[HoverOcrResult] = queue.Queue(maxsize=2)
        self._request_id = 0
        self._closed = threading.Event()
        self._cancel_lock = threading.Lock()
        self._active_cancel: threading.Event | None = None
        cleanup_stale_scan_dirs()
        self._thread = threading.Thread(target=self._run, name="LoremasterHoverOCR", daemon=True)
        self._thread.start()

    def submit(self, capture: HoverCapture | None = None) -> int:
        """Freeze pixels before returning, then queue only immutable data."""
        if self._closed.is_set():
            raise RuntimeError("Hover OCR service is closed.")
        self._request_id += 1
        request_id = self._request_id
        with self._cancel_lock:
            if self._active_cancel is not None:
                self._active_cancel.set()
            self._active_cancel = None
        if capture is None:
            try:
                capture = self.capture_factory()
            except Exception as exc:
                self._put_result(HoverOcrResult(request_id, error=str(exc)[:240]))
                return request_id
        request_cancel = threading.Event()
        with self._cancel_lock:
            self._active_cancel = request_cancel
        try:
            while True:
                pending = self.requests.get_nowait()
                if pending is not None:
                    pending[2].set()
        except queue.Empty:
            pass
        try:
            self.requests.put_nowait((request_id, capture, request_cancel))
        except queue.Full:
            request_cancel.set()
        return request_id

    def poll(self) -> list[HoverOcrResult]:
        found = []
        try:
            while True:
                found.append(self.results.get_nowait())
        except queue.Empty:
            return found

    def close(self) -> None:
        self._closed.set()
        with self._cancel_lock:
            if self._active_cancel is not None:
                self._active_cancel.set()
        try:
            while True:
                self.requests.get_nowait()
        except queue.Empty:
            pass
        try:
            self.requests.put_nowait(None)
        except queue.Full:
            pass
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=0.75)

    def _put_result(self, result: HoverOcrResult) -> None:
        try:
            self.results.put_nowait(result)
        except queue.Full:
            try:
                self.results.get_nowait()
            except queue.Empty:
                pass
            try:
                self.results.put_nowait(result)
            except queue.Full:
                pass

    def _run(self) -> None:
        while True:
            request = self.requests.get()
            if request is None or self._closed.is_set():
                return
            request_id, capture, request_cancel = request
            started = time.monotonic()
            try:
                candidates, lines = self.scanner(capture, request_cancel)
                result = HoverOcrResult(
                    request_id, candidates=candidates, lines=lines,
                    capture=capture.metadata)
            except Exception as exc:
                result = HoverOcrResult(
                    request_id, error=str(exc)[:240], capture=capture.metadata)
            result.elapsed_seconds = time.monotonic() - started
            self._put_result(result)
            with self._cancel_lock:
                if self._active_cancel is request_cancel:
                    self._active_cancel = None
