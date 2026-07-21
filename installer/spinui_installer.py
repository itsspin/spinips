#!/usr/bin/env python3
"""Windows installer for Spin's UI Reloaded and Spin's Loremaster.

The packaged executable sits beside the release payload. It discovers common
EverQuest installations, installs the skin and Loremaster, optionally applies
the 3440x1440 layout with a backup, and can register Loremaster to wait quietly
for eqgame.exe at Windows sign-in.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


APP_NAME = "SpinUI Installer"
SKIN_NAME = "spinui_reloaded"
LAYOUT_NAME = "UI_Spin_qeynos_LO1.ini"
LOREMASTER_NAME = "Loremaster.exe"
STARTUP_LINK = "Spin's Loremaster.lnk"
DESKTOP_LINK = "Spin's Loremaster.lnk"

BG = "#090c11"
PANEL = "#10161d"
RAISED = "#17222a"
LINE = "#303f4e"
GOLD = "#db9e2a"
GOLD_BRIGHT = "#facd5f"
CYAN = "#34dabe"
TEXT = "#eef2f3"
DIM = "#92a1a9"
EMBER = "#e5642d"


def release_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_eq_root(path: Path) -> bool:
    return path.is_dir() and (path / "eqgame.exe").is_file()


def steam_libraries() -> list[Path]:
    roots = [
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Steam",
    ]
    libraries: list[Path] = []
    for steam in roots:
        if not steam.is_dir():
            continue
        libraries.append(steam)
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for raw in re.findall(r'"path"\s+"([^"]+)"', text):
            libraries.append(Path(raw.replace("\\\\", "\\")))
    return libraries


def find_eq_roots() -> list[Path]:
    candidates = [
        Path(r"C:\EQLegends"),
        Path(r"D:\EQLegends"),
        Path(r"C:\Users\Public\Daybreak Game Company\Installed Games\EverQuest Legends"),
        Path(r"C:\Users\Public\Daybreak Game Company\Installed Games\EverQuest"),
        Path(r"C:\Program Files (x86)\Sony\EverQuest"),
        Path.home() / "EverQuest Legends",
    ]
    for library in steam_libraries():
        candidates.extend([
            library / "steamapps" / "common" / "EverQuest",
            library / "steamapps" / "common" / "EverQuest Legends",
        ])
    found: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).lower()
        if key not in seen and is_eq_root(path):
            seen.add(key)
            found.append(path)
    return found


def character_layouts(eq_root: Path) -> list[Path]:
    return sorted(eq_root.glob("UI_*_*_LO*.ini"), key=lambda p: p.name.lower())


def local_app_data(override: Path | None = None) -> Path:
    if override is not None:
        return override
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SpinsLoremaster"


def startup_folder(override: Path | None = None) -> Path:
    if override is not None:
        return override
    appdata = Path(os.environ.get("APPDATA", Path.home()))
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def desktop_folder(override: Path | None = None) -> Path:
    if override is not None:
        return override
    if os.name == "nt":
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(260)
            # CSIDL_DESKTOPDIRECTORY follows redirected/OneDrive desktops.
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer) == 0:
                return Path(buffer.value)
        except (AttributeError, OSError):
            pass
    return Path.home() / "Desktop"


def _ps_quote(value: str) -> str:
    return value.replace("'", "''")


def _write_shortcut(executable: Path, shortcut: Path, *, arguments: str,
                    description: str) -> None:
    if os.name != "nt":
        raise RuntimeError("Windows shortcuts can only be created on Windows")
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$w=New-Object -ComObject WScript.Shell;"
        f"$s=$w.CreateShortcut('{_ps_quote(str(shortcut))}');"
        f"$s.TargetPath='{_ps_quote(str(executable))}';"
        f"$s.Arguments='{_ps_quote(arguments)}';"
        f"$s.WorkingDirectory='{_ps_quote(str(executable.parent))}';"
        f"$s.Description='{_ps_quote(description)}';"
        f"$s.IconLocation='{_ps_quote(str(executable))},0';"
        "$s.Save()"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"Could not create {shortcut.name}: {detail}")


def set_startup_shortcut(executable: Path, enabled: bool,
                         folder: Path | None = None) -> None:
    shortcut = startup_folder(folder) / STARTUP_LINK
    if not enabled:
        shortcut.unlink(missing_ok=True)
        return
    _write_shortcut(
        executable, shortcut, arguments="--wait-for-eq",
        description="Wait for EverQuest, then open Spin's Loremaster",
    )


def set_desktop_shortcut(executable: Path, enabled: bool,
                         folder: Path | None = None) -> None:
    shortcut = desktop_folder(folder) / DESKTOP_LINK
    if not enabled:
        shortcut.unlink(missing_ok=True)
        return
    _write_shortcut(
        executable, shortcut, arguments="",
        description="Open Spin's Loremaster",
    )


def process_is_running(image_name: str) -> bool:
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["tasklist.exe", "/FI", f"IMAGENAME eq {image_name}", "/NH"],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return False
    return result.returncode == 0 and image_name.lower() in result.stdout.lower()


def stop_running_loremaster() -> bool:
    """Close an installed Loremaster so its executable can be updated."""
    if not process_is_running(LOREMASTER_NAME):
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(["taskkill.exe", "/IM", LOREMASTER_NAME],
                   capture_output=True, creationflags=flags)
    for _ in range(10):
        if not process_is_running(LOREMASTER_NAME):
            return True
        time.sleep(0.1)
    subprocess.run(["taskkill.exe", "/F", "/IM", LOREMASTER_NAME],
                   capture_output=True, creationflags=flags)
    for _ in range(10):
        if not process_is_running(LOREMASTER_NAME):
            return True
        time.sleep(0.1)
    raise RuntimeError("Close Loremaster and run the installer again so it can be updated.")


def configure_loremaster(eq_root: Path, app_dir: Path) -> None:
    config = app_dir / "loremaster_config.json"
    try:
        decoded = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
        data = decoded if isinstance(decoded, dict) else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data["log_dir"] = str(eq_root)
    config.write_text(json.dumps(data, indent=2), encoding="utf-8")


def replace_tree(source: Path, destination: Path) -> bool:
    """Install an exact directory copy, restoring the old tree on failure."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    updating = destination.exists()
    with tempfile.TemporaryDirectory(
            prefix=f".{destination.name}-install-", dir=destination.parent,
            ignore_cleanup_errors=True) as temp_name:
        temp_root = Path(temp_name)
        staged = temp_root / "fresh"
        previous = temp_root / "previous"
        shutil.copytree(source, staged)
        if updating:
            os.replace(destination, previous)
        try:
            os.replace(staged, destination)
        except Exception:
            if updating and previous.exists() and not destination.exists():
                os.replace(previous, destination)
            raise
    return updating


def install_payload(payload: Path, eq_root: Path, *, install_layout: bool,
                    layout_target: Path | None, run_at_startup: bool,
                    desktop_shortcut: bool = True,
                    app_dir: Path | None = None,
                    startup_dir: Path | None = None,
                    desktop_dir: Path | None = None,
                    replace_running: bool = False,
                    require_eq_closed: bool = False) -> list[str]:
    payload = payload.resolve()
    eq_root = eq_root.resolve()
    if not is_eq_root(eq_root):
        raise ValueError("Choose the EverQuest folder that contains eqgame.exe.")
    skin_source = payload / SKIN_NAME
    lore_source = payload / LOREMASTER_NAME
    layout_source = payload / LAYOUT_NAME
    if not skin_source.is_dir():
        raise FileNotFoundError(f"Release payload is missing {SKIN_NAME}.")
    if not lore_source.is_file():
        raise FileNotFoundError(f"Release payload is missing {LOREMASTER_NAME}.")
    if require_eq_closed and process_is_running("eqgame.exe"):
        raise RuntimeError(
            "EverQuest is running. Camp out and close eqgame.exe before updating "
            "SpinUI so the client cannot overwrite the installed files."
        )
    stopped_loremaster = stop_running_loremaster() if replace_running else False

    results: list[str] = []
    skin_destination = eq_root / "uifiles" / SKIN_NAME
    # Use a staged directory swap so removed/renamed files from an older build
    # cannot survive the update and interfere with EQ's skin loader.
    updating_skin = replace_tree(skin_source, skin_destination)
    results.append(
        f"{'Updated' if updating_skin else 'Installed'} {SKIN_NAME} at {skin_destination}"
    )

    lore_destination_dir = local_app_data(app_dir)
    lore_destination_dir.mkdir(parents=True, exist_ok=True)
    lore_destination = lore_destination_dir / LOREMASTER_NAME
    updating_loremaster = lore_destination.exists()
    staged_loremaster = lore_destination.with_suffix(".installing")
    try:
        shutil.copy2(lore_source, staged_loremaster)
        os.replace(staged_loremaster, lore_destination)
    finally:
        staged_loremaster.unlink(missing_ok=True)
    configure_loremaster(eq_root, lore_destination_dir)
    results.append(
        f"{'Updated' if updating_loremaster else 'Installed'} Loremaster at {lore_destination}"
    )
    if stopped_loremaster:
        results.append("Closed the previous Loremaster build before updating")

    if install_layout:
        if not layout_source.is_file():
            raise FileNotFoundError(f"Release payload is missing {LAYOUT_NAME}.")
        target = (layout_target or (eq_root / LAYOUT_NAME)).resolve()
        if target.parent != eq_root:
            raise ValueError("The character layout must be an INI in the EverQuest folder.")
        if target.exists():
            backup = target.with_suffix(target.suffix + ".spinui-backup")
            if not backup.exists():
                shutil.copy2(target, backup)
                results.append(f"Backed up the existing layout to {backup.name}")
        shutil.copy2(layout_source, target)
        results.append(f"Installed the optional layout as {target.name}")

    set_startup_shortcut(lore_destination, run_at_startup, startup_dir)
    results.append("Loremaster startup enabled" if run_at_startup
                   else "Loremaster startup disabled")
    set_desktop_shortcut(lore_destination, desktop_shortcut, desktop_dir)
    results.append("Loremaster desktop shortcut created" if desktop_shortcut
                   else "Loremaster desktop shortcut skipped")
    return results


def selftest() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        payload = root / "payload"
        eq = root / "EverQuest Legends"
        app = root / "appdata"
        startup = root / "startup"
        desktop = root / "desktop"
        (payload / SKIN_NAME).mkdir(parents=True)
        (payload / SKIN_NAME / "EQUI.xml").write_text("<xml/>", encoding="utf-8")
        (payload / LOREMASTER_NAME).write_bytes(b"loremaster")
        (payload / LAYOUT_NAME).write_text("new layout", encoding="utf-8")
        eq.mkdir()
        (eq / "eqgame.exe").write_bytes(b"")
        installed_skin = eq / "uifiles" / SKIN_NAME
        installed_skin.mkdir(parents=True)
        (installed_skin / "EQUI.xml").write_text("old skin", encoding="utf-8")
        (installed_skin / "removed-in-new-build.tga").write_bytes(b"stale")
        app.mkdir()
        (app / LOREMASTER_NAME).write_bytes(b"old loremaster")
        (app / "loremaster_config.json").write_text(
            json.dumps({"mini_mode": False}), encoding="utf-8"
        )
        target = eq / "UI_Test_qeynos_LO1.ini"
        target.write_text("old layout", encoding="utf-8")

        result = install_payload(
            payload, eq, install_layout=True, layout_target=target,
            run_at_startup=False, desktop_shortcut=False, app_dir=app,
            startup_dir=startup, desktop_dir=desktop,
        )
        assert result
        assert (installed_skin / "EQUI.xml").read_text(encoding="utf-8") == "<xml/>"
        assert not (installed_skin / "removed-in-new-build.tga").exists()
        assert (app / LOREMASTER_NAME).read_bytes() == b"loremaster"
        assert target.read_text(encoding="utf-8") == "new layout"
        assert target.with_suffix(".ini.spinui-backup").read_text(encoding="utf-8") == "old layout"
        config = json.loads((app / "loremaster_config.json").read_text())
        assert config["log_dir"] == str(eq.resolve())
        assert config["mini_mode"] is False
        assert not (startup / STARTUP_LINK).exists()
        assert not (desktop / DESKTOP_LINK).exists()
        (app / "loremaster_config.json").write_text("null", encoding="utf-8")
        configure_loremaster(eq.resolve(), app)
        repaired = json.loads((app / "loremaster_config.json").read_text())
        assert repaired == {"log_dir": str(eq.resolve())}
        if os.name == "nt":
            set_desktop_shortcut(app / LOREMASTER_NAME, True, desktop)
            assert (desktop / DESKTOP_LINK).is_file()
            set_desktop_shortcut(app / LOREMASTER_NAME, False, desktop)
            assert not (desktop / DESKTOP_LINK).exists()
    print("SpinUI installer selftest: ALL PASS")
    return 0


def run_gui() -> int:
    if os.name != "nt":
        raise RuntimeError("SpinUI Installer is intended for Windows.")
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    payload = release_root()
    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("720x590")
    root.minsize(680, 570)
    root.configure(bg=BG)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TCombobox", fieldbackground=RAISED, background=RAISED,
                    foreground=TEXT, arrowcolor=CYAN, bordercolor=LINE)
    style.map("TCombobox", fieldbackground=[("readonly", RAISED)],
              foreground=[("readonly", TEXT)])
    style.configure("Spin.Horizontal.TProgressbar", troughcolor=PANEL,
                    background=CYAN, bordercolor=LINE, lightcolor=CYAN,
                    darkcolor=CYAN)

    tk.Frame(root, bg=GOLD, height=2).pack(fill="x")
    header = tk.Frame(root, bg=PANEL, padx=24, pady=18)
    header.pack(fill="x")
    tk.Label(header, text="SPIN'S UI RELOADED", bg=PANEL, fg=GOLD_BRIGHT,
             font=("Georgia", 18, "bold")).pack(anchor="w")
    tk.Label(header, text="OBSIDIAN · VENOM · EMBER   /   WINDOWS INSTALLER",
             bg=PANEL, fg=CYAN, font=("Segoe UI Semibold", 9)).pack(anchor="w", pady=(4, 0))
    tk.Frame(root, bg=EMBER, height=2).pack(fill="x")

    content = tk.Frame(root, bg=BG, padx=24, pady=18)
    content.pack(fill="both", expand=True)
    tk.Label(content, text="EVERQUEST INSTALLATION", bg=BG, fg=GOLD,
             font=("Georgia", 9, "bold")).pack(anchor="w")
    path_row = tk.Frame(content, bg=BG)
    path_row.pack(fill="x", pady=(7, 4))
    path_var = tk.StringVar()
    path_entry = tk.Entry(path_row, textvariable=path_var, bg=RAISED, fg=TEXT,
                          insertbackground=CYAN, relief="flat", highlightthickness=1,
                          highlightbackground=LINE, highlightcolor=CYAN,
                          font=("Segoe UI", 10))
    path_entry.pack(side="left", fill="x", expand=True, ipady=7)
    browse = tk.Label(path_row, text="BROWSE", bg=RAISED, fg=CYAN,
                      font=("Segoe UI Semibold", 9), padx=14, pady=8, cursor="hand2")
    browse.pack(side="left", padx=(8, 0))
    detect = tk.Label(content, text="Searching for eqgame.exe…", bg=BG, fg=DIM,
                      font=("Segoe UI", 9))
    detect.pack(anchor="w")

    tk.Frame(content, bg=LINE, height=1).pack(fill="x", pady=16)
    tk.Label(content, text="INSTALL OPTIONS", bg=BG, fg=GOLD,
             font=("Georgia", 9, "bold")).pack(anchor="w")

    layout_var = tk.BooleanVar(value=False)
    startup_var = tk.BooleanVar(value=True)
    desktop_var = tk.BooleanVar(value=True)
    layout_check = tk.Checkbutton(
        content, text="Install the optional 3440x1440 character layout", variable=layout_var,
        bg=BG, activebackground=BG, fg=TEXT, activeforeground=TEXT,
        selectcolor=RAISED, font=("Segoe UI Semibold", 10), anchor="w",
    )
    layout_check.pack(fill="x", pady=(9, 0))
    tk.Label(content, text="Unchecked by default. Leave off at 2560x1440; existing INIs are backed up.",
             bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(anchor="w", padx=23)
    layout_var_name = tk.StringVar()
    layout_combo = ttk.Combobox(content, textvariable=layout_var_name, state="disabled")
    layout_combo.pack(fill="x", padx=(23, 0), pady=(6, 4), ipady=3)

    startup_check = tk.Checkbutton(
        content, text="Start Loremaster with Windows", variable=startup_var,
        bg=BG, activebackground=BG, fg=TEXT, activeforeground=TEXT,
        selectcolor=RAISED, font=("Segoe UI Semibold", 10), anchor="w",
    )
    startup_check.pack(fill="x", pady=(8, 0))
    tk.Label(content, text="It waits for eqgame.exe, then opens with a clock-area recovery icon.",
             bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(anchor="w", padx=23)

    desktop_check = tk.Checkbutton(
        content, text="Create a Loremaster desktop shortcut", variable=desktop_var,
        bg=BG, activebackground=BG, fg=TEXT, activeforeground=TEXT,
        selectcolor=RAISED, font=("Segoe UI Semibold", 10), anchor="w",
    )
    desktop_check.pack(fill="x", pady=(8, 0))
    tk.Label(content, text="Open the HUD directly; its tray icon can restore, hide, or exit it.",
             bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(anchor="w", padx=23)

    progress = ttk.Progressbar(content, mode="indeterminate", style="Spin.Horizontal.TProgressbar")
    progress.pack(fill="x", pady=(18, 5))
    status = tk.Label(content, text="Ready to install the skin and Loremaster.", bg=BG,
                      fg=DIM, font=("Segoe UI", 9), anchor="w")
    status.pack(fill="x")

    button_row = tk.Frame(content, bg=BG)
    button_row.pack(fill="x", pady=(10, 0))
    install_button = tk.Label(button_row, text="INSTALL SPINUI", bg=CYAN, fg=BG,
                              font=("Segoe UI Semibold", 11), padx=22, pady=10,
                              cursor="hand2")
    install_button.pack(side="right")
    install_results: queue.Queue[tuple[str, object]] = queue.Queue()
    installing = {"active": False}

    def refresh_layouts(*_args):
        eq = Path(path_var.get().strip())
        layouts = character_layouts(eq) if is_eq_root(eq) else []
        names = [p.name for p in layouts]
        layout_combo["values"] = names or [LAYOUT_NAME]
        if layout_var_name.get() not in layout_combo["values"]:
            layout_var_name.set(names[0] if names else LAYOUT_NAME)
        valid = is_eq_root(eq)
        detect.configure(
            text=("Ready · eqgame.exe found" if valid else "Choose the folder containing eqgame.exe"),
            fg=(CYAN if valid else DIM),
        )

    def toggle_layout(*_args):
        layout_combo.configure(state="readonly" if layout_var.get() else "disabled")

    def choose_folder(_event=None):
        selected = filedialog.askdirectory(title="Choose the folder containing eqgame.exe")
        if selected:
            path_var.set(selected)
            refresh_layouts()

    def finish_success(lines: list[str]):
        installing["active"] = False
        progress.stop()
        install_button.configure(bg=CYAN, text="INSTALL COMPLETE")
        status.configure(text="Installed successfully.", fg=CYAN)
        messagebox.showinfo("SpinUI installed", "SpinUI is ready.\n\n" + "\n".join(lines))

    def finish_error(exc: Exception):
        installing["active"] = False
        progress.stop()
        install_button.configure(bg=CYAN, text="INSTALL SPINUI")
        status.configure(text=str(exc), fg="#de3e48")
        messagebox.showerror("Installation could not finish", str(exc))

    def begin_install(_event=None):
        if installing["active"]:
            return
        eq = Path(path_var.get().strip())
        if not is_eq_root(eq):
            messagebox.showerror("EverQuest not found", "Choose the folder containing eqgame.exe.")
            return
        target = eq / layout_var_name.get() if layout_var.get() else None
        should_install_layout = layout_var.get()
        should_run_at_startup = startup_var.get()
        should_create_desktop_shortcut = desktop_var.get()
        install_button.configure(bg=LINE, text="INSTALLING…")
        status.configure(text="Copying the UI and configuring Loremaster…", fg=DIM)
        progress.start(12)
        installing["active"] = True

        def worker():
            try:
                lines = install_payload(
                    payload, eq, install_layout=should_install_layout, layout_target=target,
                    run_at_startup=should_run_at_startup,
                    desktop_shortcut=should_create_desktop_shortcut,
                    replace_running=True, require_eq_closed=True,
                )
            except Exception as exc:  # surfaced in a native message box
                install_results.put(("error", exc))
            else:
                install_results.put(("ok", lines))

        threading.Thread(target=worker, daemon=True).start()
        root.after(100, poll_install_result)

    def poll_install_result():
        try:
            outcome, value = install_results.get_nowait()
        except queue.Empty:
            if installing["active"]:
                root.after(100, poll_install_result)
            return
        if outcome == "ok":
            finish_success(value)
        else:
            finish_error(value)

    def request_close():
        if installing["active"]:
            messagebox.showinfo(
                "Installation in progress",
                "SpinUI is still being installed. This window will be ready to close in a moment.",
            )
            return
        root.destroy()

    browse.bind("<Button-1>", choose_folder)
    install_button.bind("<Button-1>", begin_install)
    layout_var.trace_add("write", toggle_layout)
    path_entry.bind("<FocusOut>", refresh_layouts)
    path_entry.bind("<Return>", refresh_layouts)
    root.protocol("WM_DELETE_WINDOW", request_close)

    roots = find_eq_roots()
    if roots:
        path_var.set(str(roots[0]))
        detect.configure(text=f"Auto-detected {roots[0]}", fg=CYAN)
    refresh_layouts()
    root.mainloop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
