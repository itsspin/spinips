"""On-demand, non-invasive OCR for an EverQuest tooltip near the cursor.

The scanner deliberately does not inspect or inject into ``eqgame.exe``.  A
single bounded worker asks a hidden Windows PowerShell process to capture a
small cursor-centred screen region and recognize it with Windows.Media.Ocr.
Tk and network work remain on their existing Loremaster threads.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import queue
import re
import shutil
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


@dataclass(frozen=True)
class OcrLine:
    text: str
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass
class HoverOcrResult:
    request_id: int
    candidates: list[str] = field(default_factory=list)
    lines: list[OcrLine] = field(default_factory=list)
    error: str = ""
    elapsed_seconds: float = 0.0


_PROPERTY_PREFIXES = (
    "ac", "agility", "agi", "attack", "attunable", "augment", "charisma",
    "cha", "class", "click effect", "damage", "delay", "description",
    "dex", "dexterity", "effect", "endurance", "focus effect", "haste",
    "heirloom", "hp", "int", "intelligence", "lore", "magic item", "mana",
    "no drop", "placeable", "prestige", "proc effect", "race", "recommended",
    "required", "size", "slot", "sta", "stamina", "str", "strength",
    "tribute", "unmodified", "value", "weight", "wis", "wisdom", "worn effect",
    "wt",
)
_UI_NOISE = {
    "inventory", "equipment", "pet", "loadouts", "storage", "destroy",
    "skills", "achievements", "find item", "done", "inspect", "close",
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
    if folded in _UI_NOISE or any(
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
    # A tooltip title is normally a short title-cased line and often uses a
    # slightly larger font than its stat block.  Position is a soft signal so
    # tooltips clamped to a monitor edge still work.
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
    """Rank conservative possible item titles from OCR output.

    This only proposes candidates.  Loremaster's existing Wiki client still
    validates that a candidate resolves to an actual EQL Wiki Itempage.
    """
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
            # EQ item names are often drawn both in a tooltip header and by
            # the inspected icon.  Agreement is useful evidence, not noise.
            best, first_index, original, count = previous
            count += 1
            scored[key] = (max(best, score) + min(6.0, 2.5 * (count - 1)),
                           first_index, original, count)
    ordered = sorted(scored.values(), reverse=True)
    return [candidate for _score, _index, candidate, _count in ordered[:limit]]


def get_cursor_position() -> tuple[int, int]:
    """Read physical cursor coordinates without permanently changing Tk DPI."""
    if os.name != "nt":
        raise RuntimeError("Hover scan is available on Windows only.")

    class Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetCursorPos.argtypes = [ctypes.POINTER(Point)]
    user32.GetCursorPos.restype = ctypes.c_bool
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    point = Point()
    try:
        if not user32.GetCursorPos(ctypes.byref(point)):
            raise OSError(ctypes.get_last_error(), "Cursor position unavailable")
        return int(point.x), int(point.y)
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(previous)


def _powershell_script() -> str:
    # Keep capture and OCR in one hidden process: the screenshot is taken
    # before the Python UI receives any result and therefore before Lore Lens
    # is shown.  SetThreadDpiAwarenessContext plus virtual-screen metrics make
    # coordinates physical-pixel correct across mixed-DPI monitors.
    return rf"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
trap {{ [Console]::Error.Write([string]$_.Exception.Message); exit 2 }}
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class SpinHoverNative {{
  [StructLayout(LayoutKind.Sequential)] public struct POINT {{ public int X; public int Y; }}
  [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT point);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
  [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
  [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);
}}
'@
[SpinHoverNative]::SetThreadDpiAwarenessContext([IntPtr](-4)) | Out-Null
$point = New-Object SpinHoverNative+POINT
$point.X = [int]$env:SPIN_LOREMASTER_CURSOR_X
$point.Y = [int]$env:SPIN_LOREMASTER_CURSOR_Y
$processId = [uint32]0
$foreground = [SpinHoverNative]::GetForegroundWindow()
[SpinHoverNative]::GetWindowThreadProcessId($foreground, [ref]$processId) | Out-Null
try {{ $processName = [System.Diagnostics.Process]::GetProcessById([int]$processId).ProcessName }}
catch {{ throw 'EverQuest is no longer in the foreground.' }}
if ($processName -ine 'eqgame') {{ throw 'Hover scan only captures EverQuest.' }}
$virtualX = [SpinHoverNative]::GetSystemMetrics(76)
$virtualY = [SpinHoverNative]::GetSystemMetrics(77)
$virtualW = [SpinHoverNative]::GetSystemMetrics(78)
$virtualH = [SpinHoverNative]::GetSystemMetrics(79)
$width = [Math]::Min({ROI_WIDTH}, $virtualW)
$height = [Math]::Min({ROI_HEIGHT}, $virtualH)
$left = [Math]::Max($virtualX, [Math]::Min($point.X - [int]($width / 2), $virtualX + $virtualW - $width))
$top = [Math]::Max($virtualY, [Math]::Min($point.Y - [int]($height / 2), $virtualY + $virtualH - $height))
$raw = New-Object System.Drawing.Bitmap($width, $height, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$graphics = [System.Drawing.Graphics]::FromImage($raw)
try {{
  $graphics.CopyFromScreen($left, $top, 0, 0, $raw.Size, [System.Drawing.CopyPixelOperation]::SourceCopy)
}} finally {{ $graphics.Dispose() }}
$scaled = New-Object System.Drawing.Bitmap($width * {OCR_SCALE}, $height * {OCR_SCALE}, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
$scaleGraphics = [System.Drawing.Graphics]::FromImage($scaled)
try {{
  $scaleGraphics.Clear([System.Drawing.Color]::Black)
  $scaleGraphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $scaleGraphics.DrawImage($raw, 0, 0, $scaled.Width, $scaled.Height)
}} finally {{ $scaleGraphics.Dispose(); $raw.Dispose() }}
$imagePath = $env:SPIN_LOREMASTER_OCR_PATH
$scaled.Save($imagePath, [System.Drawing.Imaging.ImageFormat]::Png)
$scaled.Dispose()
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
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
$file = Await-WinRt ([Windows.Storage.StorageFile]::GetFileFromPathAsync($imagePath)) ([Windows.Storage.StorageFile])
$stream = Await-WinRt ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
try {{
  $decoder = Await-WinRt ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bitmap = Await-WinRt ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  try {{
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) {{ throw 'No Windows OCR language is installed.' }}
    $result = Await-WinRt ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
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
        x = [double]$minX / {OCR_SCALE}
        y = [double]$minY / {OCR_SCALE}
        width = [double]($maxX - $minX) / {OCR_SCALE}
        height = [double]($maxY - $minY) / {OCR_SCALE}
      }}
    }}
    $payload = [pscustomobject]@{{
      cursorX = [double]($point.X - $left)
      cursorY = [double]($point.Y - $top)
      lines = $lines
    }} | ConvertTo-Json -Depth 4 -Compress
    [Console]::Out.Write($payload)
  }} finally {{ if ($null -ne $bitmap) {{ $bitmap.Dispose() }} }}
}} finally {{ $stream.Dispose() }}
"""


def _powershell_path() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.exists() else (shutil.which("powershell.exe") or "powershell.exe")


def scan_hovered_tooltip(cursor: tuple[int, int],
                         cancel_event: threading.Event | None = None,
                         timeout: float = OCR_TIMEOUT_SECONDS) -> tuple[list[str], list[OcrLine]]:
    """Capture once and return ranked OCR candidates; raise a short safe error."""
    if os.name != "nt":
        raise RuntimeError("Hover scan is available on Windows only.")
    script = _powershell_script()
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    temp_root = Path(tempfile.gettempdir()) / "SpinsLoremasterOCR"
    temp_root.mkdir(parents=True, exist_ok=True)
    request_dir = temp_root / ("scan-" + uuid.uuid4().hex)
    request_dir.mkdir()
    image_path = request_dir / "cursor-region.png"
    env = os.environ.copy()
    env["SPIN_LOREMASTER_OCR_PATH"] = str(image_path)
    env["SPIN_LOREMASTER_CURSOR_X"] = str(int(cursor[0]))
    env["SPIN_LOREMASTER_CURSOR_Y"] = str(int(cursor[1]))
    startupinfo = None
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    process = None
    try:
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
                # Draining both pipes while waiting prevents dense OCR output
                # from blocking PowerShell before process exit.
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
        candidates = rank_item_candidates(
            lines, float(payload.get("cursorX", 0.0)),
            float(payload.get("cursorY", 0.0)))
        return candidates, lines
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Windows OCR could not read the hovered tooltip.") from exc
    finally:
        try:
            image_path.unlink(missing_ok=True)
            request_dir.rmdir()
            # Remove the shared root only when this was the last active scan.
            temp_root.rmdir()
        except OSError:
            pass


class HoverOcrService:
    """One bounded OCR worker with latest-request-wins result semantics."""

    def __init__(self, scanner: Callable[[tuple[int, int], threading.Event],
                                         tuple[list[str], list[OcrLine]]] = scan_hovered_tooltip):
        self.scanner = scanner
        self.requests: queue.Queue[
            tuple[int, tuple[int, int], threading.Event] | None
        ] = queue.Queue(maxsize=1)
        self.results: queue.Queue[HoverOcrResult] = queue.Queue(maxsize=2)
        self._request_id = 0
        self._closed = threading.Event()
        self._cancel_lock = threading.Lock()
        self._active_cancel: threading.Event | None = None
        self._thread = threading.Thread(target=self._run, name="LoremasterHoverOCR", daemon=True)
        self._thread.start()

    def submit(self, cursor: tuple[int, int] | None = None) -> int:
        if self._closed.is_set():
            raise RuntimeError("Hover OCR service is closed.")
        self._request_id += 1
        request_id = self._request_id
        if cursor is None:
            try:
                cursor = get_cursor_position()
            except Exception as exc:
                self._put_result(HoverOcrResult(request_id, error=str(exc)[:240]))
                return request_id
        request_cancel = threading.Event()
        with self._cancel_lock:
            if self._active_cancel is not None:
                self._active_cancel.set()
            self._active_cancel = request_cancel
        try:
            while True:
                pending = self.requests.get_nowait()
                if pending is not None:
                    pending[2].set()
        except queue.Empty:
            pass
        try:
            self.requests.put_nowait((request_id, cursor, request_cancel))
        except queue.Full:
            pass
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
            request_id, cursor, request_cancel = request
            started = time.monotonic()
            try:
                candidates, lines = self.scanner(cursor, request_cancel)
                result = HoverOcrResult(request_id, candidates=candidates, lines=lines)
            except Exception as exc:
                result = HoverOcrResult(request_id, error=str(exc)[:240])
            result.elapsed_seconds = time.monotonic() - started
            self._put_result(result)
            with self._cancel_lock:
                if self._active_cancel is request_cancel:
                    self._active_cancel = None
