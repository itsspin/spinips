#!/usr/bin/env python3
"""One-command release quality gate for SpinUI and Loremaster.

The source gate is safe to run from a dirty worktree: generated layouts are
rebuilt in memory and compared without rewriting the player's files. Pillow is
used by the Studio visual self-test. Package checks operate on staged
directories so the workflow cannot publish a partial or stale payload.

Local/CI usage::

    python tools/release_quality_gate.py
    python tools/release_quality_gate.py --packages-only \
        --package installer=package/SpinUI-Installer \
        --package manual=package/SpinUI-Manual \
        --package studio=package/SpinUI-Studio
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import os
import re
import struct
import subprocess
import sys
import time
import tracemalloc
import xml.etree.ElementTree as ET
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
SKIN = REPO / "spinui_reloaded"

# The generator deliberately uses this stock file as its immutable layout
# input.  CI checks out full history so drift is never compared against an
# already-generated worktree file.
PRISTINE_LAYOUT_SPEC = "0eac353:default_modern/default1440.ini"

BENCHMARK_LINES = 30_000
MIN_INGEST_LINES_PER_SECOND = 1_000
MAX_INGEST_PEAK_MIB = 16.0
BENCHMARK_ABORT_SECONDS = 45.0
CLIENT_FALLBACK_INCLUDES = {"sidl.xml"}

SOURCE_REQUIRED = (
    "README.md",
    "UI_Spin_qeynos_LO1.ini",
    "spinui_reloaded/EQUI.xml",
    "spinui_reloaded/default1440.ini",
    "layouts/combat-focus/UI_Spin_qeynos_LO1.ini",
    "layouts/hybrid/UI_Spin_qeynos_LO1.ini",
    "layouts/original/UI_Spin_qeynos_LO1.ini",
    "layouts/social-focus/UI_Spin_qeynos_LO1.ini",
    "layouts/spin-live/UI_Spin_qeynos_LO1.ini",
    "docs/previews/combat_command_center.png",
    "docs/previews/equipment_page.png",
    "docs/previews/loremaster_panel.png",
    "docs/previews/persona_page.png",
    "docs/previews/spinui_reloaded_3440.png",
    "docs/screenshots/inventory-live.png",
    "docs/screenshots/loremaster-encounter-live.png",
    "docs/screenshots/loremaster-live-tour.gif",
    "docs/screenshots/loremaster-session-live.png",
    "docs/screenshots/spinui-live-hero.jpg",
    "loremaster/hover_ocr.py",
    "loremaster/loremaster.py",
    "loremaster/log_ingest.py",
    "loremaster/windows_hotkeys.py",
    "loremaster/windows_tray.py",
    "loremaster/wiki_overlay.py",
    "loremaster/tests/test_compositions.py",
    "loremaster/tests/test_config.py",
    "loremaster/tests/test_hover_ocr.py",
    "loremaster/tests/test_log_ingest.py",
    "loremaster/tests/test_windows_hotkeys.py",
    "loremaster/tests/test_windows_tray.py",
    "loremaster/tests/test_wiki_overlay.py",
    "loremaster/tests/fixtures/cloak_of_flames.json",
    "loremaster/tests/fixtures/studded_belt.json",
    "docs/SPINUI-STUDIO.md",
    "installer/spinui_installer.py",
    "installer/INSTALL-MANUAL.md",
    "tools/audit_combat_ui.py",
    "tools/audit_spinui.py",
    "tools/build_showcase_media.py",
    "tools/generate_spinui_layout.py",
    "tools/generate_spinui_textures.py",
    "tools/render_loremaster_preview.py",
    "tools/render_preview.py",
    "tools/spinui_studio.py",
    "tools/spinui_theme.py",
    ".github/workflows/build-loremaster.yml",
)

# Values: (format, allowed dimensions, byte cap).
README_MEDIA = {
    "docs/screenshots/spinui-live-hero.jpg": ("JPEG", {(1600, 670)}, 1_000_000),
    "docs/screenshots/inventory-live.png": ("PNG", {(670, 671)}, 1_000_000),
    "docs/screenshots/loremaster-encounter-live.png": ("PNG", {(400, 480)}, 1_000_000),
    "docs/screenshots/loremaster-session-live.png": ("PNG", {(400, 480)}, 1_000_000),
    "docs/screenshots/loremaster-live-tour.gif": ("GIF", {(960, 540)}, 4_000_000),
    "docs/previews/loremaster_panel.png": ("PNG", {(1704, 1658)}, 2_000_000),
}

PUBLIC_LAYOUT_PRESETS = ("combat-focus", "social-focus", "hybrid")

COMMON_PACKAGE_TOP_LEVEL = {
    "docs",
    "spinui_reloaded",
    "layouts",
    "UI_Spin_qeynos_LO1.ini",
    "README.md",
    "INSTALL.md",
    "Loremaster.exe",
    "SpinUIStudio.exe",
}

# The standalone Studio release ships only the editor and its build sources:
# no Loremaster, no installer, and only the previews Studio itself renders.
STUDIO_PACKAGE_TOP_LEVEL = {
    "docs",
    "spinui_reloaded",
    "layouts",
    "UI_Spin_qeynos_LO1.ini",
    "README.md",
    "SpinUIStudio.exe",
}


class GateFailure(RuntimeError):
    """An actionable release check failure."""


def section(title: str) -> None:
    print(f"\n== {title} ==", flush=True)


def fail(message: str) -> None:
    raise GateFailure(message)


def run_command(label: str, command: list[str], timeout: int = 180) -> None:
    print(f"[RUN] {label}", flush=True)
    try:
        result = subprocess.run(command, cwd=REPO, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        fail(f"{label} exceeded the {timeout}s safety timeout")
    except OSError as exc:
        fail(f"could not start {label}: {exc}")
    if result.returncode:
        fail(f"{label} exited with code {result.returncode}")
    print(f"[PASS] {label}", flush=True)


def import_file(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        fail(f"could not import {path.relative_to(REPO)}")
    module = importlib.util.module_from_spec(spec)
    # Dataclasses and a few other standard-library helpers resolve the module
    # by name while its body executes.
    sys.modules[name] = module
    module_dir = str(path.parent)
    inserted_path = module_dir not in sys.path
    if inserted_path:
        sys.path.insert(0, module_dir)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        fail(
            f"could not import {path.relative_to(REPO)}: "
            f"{type(exc).__name__}: {exc}"
        )
    finally:
        if inserted_path:
            try:
                sys.path.remove(module_dir)
            except ValueError:
                pass
    return module


def check_source_manifest() -> None:
    section("Source manifest")
    missing = [rel for rel in SOURCE_REQUIRED if not (REPO / rel).is_file()]
    if missing:
        fail("missing required source files: " + ", ".join(missing))
    empty_required = [
        rel for rel in SOURCE_REQUIRED if (REPO / rel).stat().st_size == 0
    ]
    if empty_required:
        fail("empty required source files: " + ", ".join(empty_required))

    xml_count = len(list(SKIN.glob("*.xml")))
    texture_count = sum(
        len(list(SKIN.glob(pattern))) for pattern in ("*.tga", "*.dds", "*.cur")
    )
    if xml_count < 200:
        fail(f"skin payload looks incomplete: only {xml_count} XML files")
    if texture_count < 500:
        fail(f"skin payload looks incomplete: only {texture_count} binary assets")

    zero_length = [
        path.relative_to(REPO).as_posix()
        for pattern in ("*.xml", "*.ini", "*.tga", "*.dds", "*.cur")
        for path in SKIN.glob(pattern)
        if path.stat().st_size == 0
    ]
    if zero_length:
        fail("zero-length skin assets: " + ", ".join(zero_length[:10]))
    print(
        f"[PASS] required files present | XML {xml_count} | binary assets {texture_count}",
        flush=True,
    )


def _jpeg_dimensions(payload: bytes) -> tuple[int, int]:
    """Return JPEG dimensions without adding an image-library CI dependency."""

    if not payload.startswith(b"\xff\xd8"):
        fail("invalid JPEG start-of-image marker")
    position = 2
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    while position + 4 <= len(payload):
        while position < len(payload) and payload[position] == 0xFF:
            position += 1
        if position >= len(payload):
            break
        marker = payload[position]
        position += 1
        if marker in {0x01, *range(0xD0, 0xDA)}:
            continue
        if position + 2 > len(payload):
            break
        segment_length = struct.unpack(">H", payload[position:position + 2])[0]
        if segment_length < 2 or position + segment_length > len(payload):
            fail("invalid JPEG segment length")
        if marker in sof_markers:
            if segment_length < 7:
                fail("invalid JPEG frame header")
            height, width = struct.unpack(
                ">HH", payload[position + 3:position + 7])
            return width, height
        position += segment_length
    fail("JPEG has no supported frame header")


def _image_identity(path: Path) -> tuple[str, int, int]:
    payload = path.read_bytes()
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(payload) < 24 or payload[12:16] != b"IHDR":
            fail(f"invalid PNG header: {path.relative_to(REPO)}")
        width, height = struct.unpack(">II", payload[16:24])
        return "PNG", width, height
    if payload[:6] in {b"GIF87a", b"GIF89a"}:
        if len(payload) < 10:
            fail(f"invalid GIF header: {path.relative_to(REPO)}")
        width, height = struct.unpack("<HH", payload[6:10])
        return "GIF", width, height
    if payload.startswith(b"\xff\xd8"):
        width, height = _jpeg_dimensions(payload)
        return "JPEG", width, height
    fail(f"unsupported or corrupt image: {path.relative_to(REPO)}")


def check_readme_media() -> None:
    """Keep the public and packaged README gallery complete and lightweight."""

    section("README showcase media")
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    raw_links = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", readme)
    local_links = {
        link.replace("\\", "/")
        for link in raw_links
        if "://" not in link and not link.startswith("#")
    }
    expected = set(README_MEDIA)
    missing_links = sorted(expected - local_links)
    if missing_links:
        fail("README does not display required showcase media: " + ", ".join(missing_links))
    for relative in sorted(local_links):
        candidate = (REPO / relative).resolve()
        try:
            candidate.relative_to(REPO.resolve())
        except ValueError:
            fail(f"README media escapes the repository: {relative}")
        if not candidate.is_file():
            fail(f"README media link is broken: {relative}")
    for relative, (kind, allowed_dims, size_limit) in README_MEDIA.items():
        path = REPO / relative
        actual_kind, actual_width, actual_height = _image_identity(path)
        if actual_kind != kind or (actual_width, actual_height) not in allowed_dims:
            expected = " or ".join(
                f"{width}x{height}" for width, height in sorted(allowed_dims))
            fail(
                f"{relative} identity drifted: expected {kind} {expected}, "
                f"got {actual_kind} {actual_width}x{actual_height}"
            )
        if path.stat().st_size > size_limit:
            fail(
                f"{relative} is too large for the public README "
                f"({path.stat().st_size:,} > {size_limit:,} bytes)"
            )
    print(
        f"[PASS] {len(README_MEDIA)} linked assets | headers, dimensions, and size caps",
        flush=True,
    )


def check_no_retired_content_references() -> None:
    """Keep retired demo and legacy-product names out of repository text."""

    section("Retired demo content")
    # Keep the forbidden bytes out of source and documentation too, so this
    # guard cannot become the last reference it is intended to prevent.
    retired_phrases = (
        bytes.fromhex(
            "72 75 6e 65 64 20 62 6f 6c 73 74 65 72 20 62 65 6c 74"),
        bytes.fromhex("65 71 62 75 64 64 79"),
        bytes.fromhex("65 71 20 62 75 64 64 79"),
    )
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"],
            cwd=REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        fail(f"could not enumerate repository files for retired-content scan: {exc}")
    if listed.returncode:
        detail = listed.stderr.decode("utf-8", errors="replace").strip()
        fail(f"could not enumerate repository files for retired-content scan: {detail}")

    matches: list[str] = []
    for raw_name in listed.stdout.split(b"\0"):
        if not raw_name:
            continue
        relative = Path(os.fsdecode(raw_name))
        path = REPO / relative
        if not path.is_file():
            continue
        try:
            payload = path.read_bytes()
        except OSError as exc:
            fail(f"could not scan {relative.as_posix()}: {exc}")
        # Match ripgrep's normal behavior by ignoring binary payloads.
        if b"\0" in payload[:4096]:
            continue
        if any(phrase in payload.lower() for phrase in retired_phrases):
            matches.append(relative.as_posix())
    if matches:
        fail("retired content still appears in: " + ", ".join(matches))
    print("[PASS] retired demo and legacy names have zero text references", flush=True)


def run_discovered_audits() -> None:
    section("SpinUI structural and geometry audits")
    audits = sorted(TOOLS.glob("audit_*.py"), key=lambda path: path.name.casefold())
    if not audits:
        fail("no tools/audit_*.py checks were found")
    for audit in audits:
        run_command(audit.name, [sys.executable, str(audit)])


def _include_map(equi_path: Path) -> dict[str, str]:
    try:
        root = ET.parse(equi_path).getroot()
    except (OSError, ET.ParseError) as exc:
        fail(f"cannot parse reference manifest {equi_path}: {exc}")
    includes: dict[str, str] = {}
    for node in root.iter("Include"):
        if not node.text or not node.text.strip():
            continue
        name = Path(node.text.strip().replace("\\", "/")).name
        key = name.casefold()
        if key in includes:
            fail(f"duplicate include in {equi_path}: {name}")
        includes[key] = name
    return includes


def check_reference_ui(reference: Path) -> None:
    """Optionally catch a Legends patch changing the client window manifest.

    Internal EQTypes are intentionally not compared: custom combat and
    inventory windows legitimately reorganize those bindings.  Include-set
    and document-root drift are stable signals that the skin needs a patch
    compatibility review.
    """
    section("Installed EverQuest Legends UI compatibility")
    candidates = (reference, reference / "uifiles" / "default")
    reference_ui = next((path for path in candidates if (path / "EQUI.xml").is_file()), None)
    if reference_ui is None:
        fail(
            "--reference-ui must point to uifiles/default or an EverQuest "
            f"Legends root containing it: {reference}"
        )

    custom_includes = _include_map(SKIN / "EQUI.xml")
    stock_includes = _include_map(reference_ui / "EQUI.xml")
    added = sorted(set(stock_includes) - set(custom_includes))
    retired = sorted(set(custom_includes) - set(stock_includes))
    if added or retired:
        details = []
        if added:
            details.append(
                "new client includes " + ", ".join(stock_includes[key] for key in added)
            )
        if retired:
            details.append(
                "retired client includes "
                + ", ".join(custom_includes[key] for key in retired)
            )
        fail("EQL client EQUI.xml manifest drift: " + "; ".join(details))

    reference_files = {
        path.name.casefold(): path for path in reference_ui.glob("*.xml")
    }
    custom_files = {path.name.casefold(): path for path in SKIN.glob("*.xml")}
    root_mismatches: list[str] = []
    missing_overrides: list[str] = []
    compared = 0
    for key, display_name in stock_includes.items():
        stock_path = reference_files.get(key)
        custom_path = custom_files.get(key)
        if stock_path is None:
            # Some client-provided resources are resolved outside the default
            # folder; the existing asset audit owns those fallback cases.
            continue
        if custom_path is None:
            if key in CLIENT_FALLBACK_INCLUDES:
                continue
            missing_overrides.append(display_name)
            continue
        try:
            stock_root = ET.parse(stock_path).getroot().tag
            custom_root = ET.parse(custom_path).getroot().tag
        except ET.ParseError as exc:
            fail(f"cannot compare {display_name} with installed UI: {exc}")
        compared += 1
        if stock_root != custom_root:
            root_mismatches.append(
                f"{display_name} ({custom_root}, client expects {stock_root})"
            )
    if missing_overrides:
        fail("skin is missing client windows: " + ", ".join(missing_overrides))
    if root_mismatches:
        fail("XML document-root drift: " + ", ".join(root_mismatches))
    print(
        f"[PASS] manifest matches installed client | {len(stock_includes)} includes | "
        f"{compared} window roots compared",
        flush=True,
    )


def _git_file(spec: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO), "show", spec],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail(f"cannot read generated-layout source {spec}: {exc}")
    if result.returncode or not result.stdout:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        fail(
            f"cannot read generated-layout source {spec}; fetch full git history"
            + (f" ({detail})" if detail else "")
        )
    return result.stdout.decode("utf-8", errors="replace")


def _first_text_difference(actual: str, expected: str) -> str:
    actual_lines = actual.splitlines()
    expected_lines = expected.splitlines()
    for number, (left, right) in enumerate(zip(actual_lines, expected_lines), 1):
        if left != right:
            return f"line {number}: found {left!r}, expected {right!r}"
    if len(actual_lines) != len(expected_lines):
        return f"line count {len(actual_lines)}, expected {len(expected_lines)}"
    return "byte/text normalization differs"


def check_generated_layout_drift() -> None:
    section("Layout bounds, overlap, and generated-file drift")
    layout = import_file("spinui_release_layout", TOOLS / "generate_spinui_layout.py")

    # These are the generator's own geometry checks, including every 3440
    # chat preset plus the separately-authored 2560 profile.
    try:
        layout.validate_all_presets()
    except SystemExit as exc:
        fail(f"generated layout validation exited with code {exc.code}")
    standard = layout.standard_1440_placements()
    problems = layout.validate_profile(standard, 2560, 1440)
    if problems:
        fail("2560x1440 layout validation failed: " + "; ".join(problems))

    default_source = _git_file(PRISTINE_LAYOUT_SPEC)
    standard_eqmain = {
        "XRef": "right",
        "YRef": "bottom",
        "XPos": f"{8 / 2560 * 100:.6f}%",
        "YPos": f"{4 / 1440 * 100:.6f}%",
        "Show": "1",
    }
    generated_default = layout.transform(
        default_source, layout.DEFAULT_PRESET, standard, standard_eqmain
    )
    expected: dict[Path, str] = {
        SKIN / "default1440.ini": generated_default,
    }

    personal_source = (REPO / layout.PERSONAL_BASE).read_text(encoding="utf-8")
    for preset in layout.CHAT_PRESETS:
        text = layout.merge_missing(
            layout.transform(
                personal_source, preset, layout.personal_placements(preset)
            ),
            generated_default,
        )
        expected[
            REPO / "layouts" / preset / "UI_Spin_qeynos_LO1.ini"
        ] = text
    expected[REPO / "UI_Spin_qeynos_LO1.ini"] = expected[
        REPO / "layouts" / layout.DEFAULT_PRESET / "UI_Spin_qeynos_LO1.ini"
    ]

    drift: list[str] = []
    for path, wanted in expected.items():
        relative = path.relative_to(REPO).as_posix()
        if not path.is_file():
            drift.append(f"{relative}: missing")
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != wanted:
            drift.append(f"{relative}: {_first_text_difference(actual, wanted)}")
    if drift:
        fail(
            "generated layouts are stale; run "
            "`python tools/generate_spinui_layout.py`:\n  - "
            + "\n  - ".join(drift)
        )
    print(
        f"[PASS] {len(expected)} generated layouts current | "
        "2560x1440 + all 3440x1440 presets bounded and non-overlapping",
        flush=True,
    )


def run_source_selftests() -> None:
    section("SpinUI Studio, Loremaster, and installer self-tests")
    run_command(
        "SpinUI Studio --selftest",
        [sys.executable, str(REPO / "tools" / "spinui_studio.py"), "--selftest"],
    )
    run_command(
        "Loremaster --selftest",
        [sys.executable, str(REPO / "loremaster" / "loremaster.py"), "--selftest"],
    )
    run_command(
        "Loremaster unit suite",
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(REPO / "loremaster" / "tests"),
            "-p",
            "test_*.py",
        ],
    )
    run_command(
        "SpinUI installer --selftest",
        [
            sys.executable,
            str(REPO / "installer" / "spinui_installer.py"),
            "--selftest",
        ],
    )


def benchmark_loremaster_ingest() -> None:
    section("Loremaster deterministic ingest budget")
    lore = import_file("loremaster_release_benchmark", REPO / "loremaster" / "loremaster.py")
    feed = (
        "[Sun Jul 19 20:00:00 2026] You slash a quality sentinel for 111 points of damage.",
        "[Sun Jul 19 20:00:00 2026] A quality sentinel hits YOU for 37 points of damage.",
        "[Sun Jul 19 20:00:00 2026] You healed Spin for 83 hit points by Light Healing.",
        "[Sun Jul 19 20:00:00 2026] A quality sentinel has taken 29 damage from your Flame Lick.",
        "[Sun Jul 19 20:00:00 2026] You have slain a quality sentinel!",
        "[Sun Jul 19 20:00:00 2026] You gain experience!! (0.01%)",
        "[Sun Jul 19 20:00:00 2026] You receive 2 gold, 4 silver from the corpse.",
        "[Sun Jul 19 20:00:00 2026] --You have looted a Quality Token from a quality sentinel's corpse.--",
    )
    for line in feed:
        parsed = lore.parse_line(line)
        if parsed is None:
            fail(f"benchmark fixture no longer parses: {line}")

    # Warm regex/date caches so this measures sustained log handling rather
    # than one-time Python imports.  The measured path still performs both
    # parse_line and SessionStats.apply for every line.
    warm = lore.SessionStats("Spin")
    for index in range(1_000):
        parsed = lore.parse_line(feed[index % len(feed)])
        warm.apply(*parsed)
    del warm
    gc.collect()

    stats = lore.SessionStats("Spin")
    tracemalloc.start()
    started = time.perf_counter()
    for index in range(BENCHMARK_LINES):
        parsed = lore.parse_line(feed[index % len(feed)])
        stats.apply(*parsed)
        if index and index % 1_000 == 0:
            partial_elapsed = time.perf_counter() - started
            if partial_elapsed > BENCHMARK_ABORT_SECONDS:
                tracemalloc.stop()
                fail(
                    "Loremaster ingest benchmark exceeded the "
                    f"{BENCHMARK_ABORT_SECONDS:.0f}s abort budget"
                )
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    rate = BENCHMARK_LINES / max(elapsed, 1e-9)
    peak_mib = peak / (1024 * 1024)
    if stats.log_lines != BENCHMARK_LINES:
        fail(
            f"benchmark ingested {stats.log_lines} lines, expected {BENCHMARK_LINES}"
        )
    if rate < MIN_INGEST_LINES_PER_SECOND:
        fail(
            f"Loremaster sustained only {rate:,.0f} lines/s; "
            f"minimum is {MIN_INGEST_LINES_PER_SECOND:,} lines/s"
        )
    if peak_mib > MAX_INGEST_PEAK_MIB:
        fail(
            f"Loremaster allocated {peak_mib:.2f} MiB peak while ingesting; "
            f"maximum is {MAX_INGEST_PEAK_MIB:.2f} MiB"
        )
    print(
        f"[PASS] {BENCHMARK_LINES:,} parsed+applied | {rate:,.0f} lines/s "
        f"(min {MIN_INGEST_LINES_PER_SECOND:,}) | {peak_mib:.2f} MiB peak "
        f"(max {MAX_INGEST_PEAK_MIB:.0f})",
        flush=True,
    )


def _tree_files(root: Path) -> dict[str, Path]:
    return {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file()
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _compare_packaged_tree(source: Path, packaged: Path, label: str) -> int:
    source_files = _tree_files(source)
    package_files = _tree_files(packaged)
    missing = sorted(set(source_files) - set(package_files))
    extra = sorted(set(package_files) - set(source_files))
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing[:10]))
        if extra:
            details.append("unexpected " + ", ".join(extra[:10]))
        fail(f"{label} tree manifest differs: " + "; ".join(details))
    for relative, source_path in source_files.items():
        package_path = package_files[relative]
        if source_path.stat().st_size != package_path.stat().st_size:
            fail(f"{label}/{relative} has the wrong size")
        if _sha256(source_path) != _sha256(package_path):
            fail(f"{label}/{relative} does not match the release source")
    return len(source_files)


def _check_same_file(source: Path, packaged: Path, label: str) -> None:
    if not packaged.is_file():
        fail(f"missing package file {label}")
    if source.stat().st_size != packaged.stat().st_size:
        fail(f"package file {label} has the wrong size")
    if _sha256(source) != _sha256(packaged):
        fail(f"package file {label} does not match its release source")


def _check_windows_executable(path: Path) -> None:
    if not path.is_file():
        fail(f"missing package executable {path.name}")
    if path.stat().st_size < 1_000_000:
        fail(f"{path.name} is unexpectedly small ({path.stat().st_size:,} bytes)")
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            fail(f"{path.name} is not a Windows executable")


def check_staged_package(kind: str, package_root: Path) -> None:
    package_root = package_root.resolve()
    if not package_root.is_dir():
        fail(f"{kind} package directory does not exist: {package_root}")

    if kind == "studio":
        expected_top = set(STUDIO_PACKAGE_TOP_LEVEL)
    else:
        expected_top = set(COMMON_PACKAGE_TOP_LEVEL)
        if kind == "installer":
            expected_top.add("SpinUIInstaller.exe")
    actual_top = {path.name for path in package_root.iterdir()}
    missing = sorted(expected_top - actual_top)
    extra = sorted(actual_top - expected_top)
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        fail(f"{kind} package top-level manifest differs: " + "; ".join(details))

    skin_files = _compare_packaged_tree(
        SKIN, package_root / "spinui_reloaded", f"{kind}/spinui_reloaded"
    )
    # Only the public presets ship; layouts/original and layouts/spin-live are
    # internal generator bases and must never reach a release package.
    package_layouts = package_root / "layouts"
    if not package_layouts.is_dir():
        fail(f"{kind} package is missing the layouts directory")
    actual_presets = {path.name for path in package_layouts.iterdir()}
    if actual_presets != set(PUBLIC_LAYOUT_PRESETS):
        fail(
            f"{kind} package layouts must contain exactly "
            f"{sorted(PUBLIC_LAYOUT_PRESETS)}, found {sorted(actual_presets)}"
        )
    layout_files = 0
    for preset in PUBLIC_LAYOUT_PRESETS:
        layout_files += _compare_packaged_tree(
            REPO / "layouts" / preset, package_layouts / preset,
            f"{kind}/layouts/{preset}"
        )
    if kind == "studio":
        # Studio ships a purpose-built README and only the docs/previews tree
        # its offline renderer actually loads.
        package_docs = package_root / "docs"
        if not package_docs.is_dir():
            fail(f"{kind} package is missing the docs directory")
        actual_docs = {path.name for path in package_docs.iterdir()}
        if actual_docs != {"previews"}:
            fail(
                f"{kind} package docs must contain exactly ['previews'], "
                f"found {sorted(actual_docs)}"
            )
        docs_files = _compare_packaged_tree(
            REPO / "docs" / "previews", package_docs / "previews",
            f"{kind}/docs/previews"
        )
        _check_same_file(
            REPO / "docs" / "SPINUI-STUDIO.md",
            package_root / "README.md",
            f"{kind}/README.md",
        )
    else:
        docs_files = _compare_packaged_tree(
            REPO / "docs", package_root / "docs", f"{kind}/docs"
        )
        _check_same_file(
            REPO / "README.md", package_root / "README.md", f"{kind}/README.md")
        _check_same_file(
            REPO / "installer" / "INSTALL-MANUAL.md",
            package_root / "INSTALL.md",
            f"{kind}/INSTALL.md",
        )
    _check_same_file(
        REPO / "UI_Spin_qeynos_LO1.ini",
        package_root / "UI_Spin_qeynos_LO1.ini",
        f"{kind}/UI_Spin_qeynos_LO1.ini",
    )
    _check_windows_executable(package_root / "SpinUIStudio.exe")
    if kind != "studio":
        _check_windows_executable(package_root / "Loremaster.exe")
    if kind == "installer":
        _check_windows_executable(package_root / "SpinUIInstaller.exe")
    print(
        f"[PASS] {kind} package | skin files {skin_files} | "
        f"layout files {layout_files} | docs files {docs_files} | exact source payload",
        flush=True,
    )


def parse_package(value: str) -> tuple[str, Path]:
    kind, separator, raw_path = value.partition("=")
    kind = kind.strip().casefold()
    if (not separator or kind not in {"installer", "manual", "studio"}
            or not raw_path.strip()):
        raise argparse.ArgumentTypeError(
            "package must be installer=PATH, manual=PATH, or studio=PATH"
        )
    path = Path(raw_path.strip())
    if not path.is_absolute():
        path = REPO / path
    return kind, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--packages-only",
        action="store_true",
        help="skip source checks and validate only staged package directories",
    )
    parser.add_argument(
        "--package",
        action="append",
        type=parse_package,
        default=[],
        metavar="KIND=PATH",
        help="validate a staged installer or manual package (repeatable)",
    )
    parser.add_argument(
        "--reference-ui",
        type=Path,
        help=(
            "optional installed EQL uifiles/default (or game root) used to "
            "detect patch-level EQUI manifest/schema drift"
        ),
    )
    args = parser.parse_args(argv)
    if args.packages_only and not args.package:
        parser.error("--packages-only requires at least one --package")

    started = time.perf_counter()
    try:
        if not args.packages_only:
            check_source_manifest()
            check_readme_media()
            check_no_retired_content_references()
            run_discovered_audits()
            if args.reference_ui is not None:
                check_reference_ui(args.reference_ui.resolve())
            check_generated_layout_drift()
            run_source_selftests()
            benchmark_loremaster_ingest()
        if args.package:
            section("Staged release package manifests")
            seen: set[str] = set()
            for kind, package_root in args.package:
                if kind in seen:
                    fail(f"duplicate {kind} package argument")
                seen.add(kind)
                check_staged_package(kind, package_root)
    except GateFailure as exc:
        print(f"\nRELEASE QUALITY GATE: FAIL\n{exc}", file=sys.stderr, flush=True)
        return 1

    elapsed = time.perf_counter() - started
    print(f"\nRELEASE QUALITY GATE: ALL PASS ({elapsed:.1f}s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
