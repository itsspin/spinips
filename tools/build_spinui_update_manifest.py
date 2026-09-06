#!/usr/bin/env python3
"""Build and verify the checksum-pinned SpinUI update manifest.

The manifest is deliberately deterministic: it contains no timestamps and all
theme files are emitted in normalized, ordinal path order.  Loremaster can use
the exact per-file inventory to reject partial, modified, or unexpectedly
expanded update payloads before replacing an installed skin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable


REPO = Path(__file__).resolve().parent.parent
THEMES = ("spinui_reloaded", "spinui_glass", "spinui_pearlescent")
SCHEMA_VERSION = 1
TREE_HASH_ALGORITHM = "sha256-path-size-content-v1"
VERSION_RE = re.compile(
    r"^(?:v)?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)$"
)


class ManifestError(RuntimeError):
    """Raised when source files or the release archive are not safe and exact."""


def _sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return _sha256_stream(stream)


def normalize_version(value: str) -> str:
    match = VERSION_RE.fullmatch(value.strip())
    if not match:
        raise ManifestError(f"invalid semantic release version: {value!r}")
    return match.group(1)


def _safe_relative_path(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    pure = PurePosixPath(relative)
    if (
        not relative
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ManifestError(f"unsafe theme path: {relative!r}")
    return relative


def _tree_sha256(files: Iterable[dict[str, object]]) -> str:
    """Hash an exact ordered tree using unambiguous field separators."""

    digest = hashlib.sha256()
    for item in files:
        path = str(item["path"])
        size = int(item["size"])
        content_digest = str(item["sha256"])
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(content_digest))
        digest.update(b"\n")
    return digest.hexdigest()


def scan_theme(root: Path) -> dict[str, object]:
    if not root.is_dir():
        raise ManifestError(f"missing theme directory: {root}")

    rows: list[dict[str, object]] = []
    casefold_paths: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ManifestError(f"theme contains a symbolic link: {path}")
        if not path.is_file():
            continue
        relative = _safe_relative_path(path, root)
        folded = relative.casefold()
        if folded in casefold_paths:
            raise ManifestError(
                "theme contains Windows-colliding paths: "
                f"{casefold_paths[folded]!r} and {relative!r}"
            )
        casefold_paths[folded] = relative
        rows.append({
            "path": relative,
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        })

    if not rows:
        raise ManifestError(f"theme contains no files: {root}")
    rows.sort(key=lambda item: str(item["path"]))
    return {
        "fileCount": len(rows),
        "totalBytes": sum(int(item["size"]) for item in rows),
        "treeSha256": _tree_sha256(rows),
        "files": rows,
    }


def _safe_zip_name(raw_name: str) -> str:
    name = raw_name.replace("\\", "/")
    pure = PurePosixPath(name)
    if (
        not name
        or "\0" in name
        or name.startswith("/")
        or re.match(r"^[A-Za-z]:", name)
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ManifestError(f"archive contains unsafe path: {raw_name!r}")
    return pure.as_posix()


def verify_archive_themes(
    archive_path: Path,
    themes: dict[str, dict[str, object]],
) -> None:
    """Verify the ZIP contains each theme tree exactly once and byte-for-byte."""

    if not archive_path.is_file():
        raise ManifestError(f"missing release archive: {archive_path}")

    expected: dict[str, dict[str, object]] = {}
    for theme, payload in themes.items():
        for row in payload["files"]:  # type: ignore[index]
            expected[f"{theme}/{row['path']}"] = row

    found: dict[str, zipfile.ZipInfo] = {}
    folded: dict[str, str] = {}
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            for info in archive.infolist():
                name = _safe_zip_name(info.filename.rstrip("/"))
                if info.is_dir():
                    continue
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_IFMT(unix_mode) == stat.S_IFLNK:
                    raise ManifestError(f"archive entry is a symbolic link: {name}")
                lower = name.casefold()
                if lower in folded:
                    raise ManifestError(
                        "archive contains duplicate Windows paths: "
                        f"{folded[lower]!r} and {name!r}"
                    )
                folded[lower] = name
                if name.split("/", 1)[0] in themes:
                    found[name] = info

            missing = sorted(set(expected) - set(found))
            extra = sorted(set(found) - set(expected))
            if missing or extra:
                details: list[str] = []
                if missing:
                    details.append("missing " + ", ".join(missing[:8]))
                if extra:
                    details.append("unexpected " + ", ".join(extra[:8]))
                raise ManifestError("archive theme inventory differs: " + "; ".join(details))

            for name in sorted(expected):
                wanted = expected[name]
                info = found[name]
                if info.flag_bits & 0x1:
                    raise ManifestError(f"archive entry is encrypted: {name}")
                if info.file_size != int(wanted["size"]):
                    raise ManifestError(f"archive entry has wrong size: {name}")
                with archive.open(info, "r") as stream:
                    actual_hash = _sha256_stream(stream)
                if actual_hash != wanted["sha256"]:
                    raise ManifestError(f"archive entry has wrong content: {name}")
    except zipfile.BadZipFile as exc:
        raise ManifestError(f"invalid release ZIP: {archive_path}") from exc


def build_manifest(repo_root: Path, archive_path: Path, version: str) -> dict[str, object]:
    normalized_version = normalize_version(version)
    theme_payloads = {
        theme: scan_theme(repo_root / theme)
        for theme in THEMES
    }
    verify_archive_themes(archive_path, theme_payloads)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "releaseVersion": normalized_version,
        "treeHashAlgorithm": TREE_HASH_ALGORITHM,
        "archive": {
            "name": archive_path.name,
            "size": archive_path.stat().st_size,
            "sha256": sha256_file(archive_path),
        },
        "themes": theme_payloads,
    }


def manifest_bytes(payload: dict[str, object]) -> bytes:
    return (json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n").encode("utf-8")


def write_manifest(
    repo_root: Path,
    archive_path: Path,
    version: str,
    output_path: Path,
) -> dict[str, object]:
    payload = build_manifest(repo_root, archive_path, version)
    encoded = manifest_bytes(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(encoded)
    if output_path.read_bytes() != encoded:
        raise ManifestError(f"manifest write verification failed: {output_path}")
    return payload


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="spinui-manifest-") as temporary:
        root = Path(temporary)
        fixtures = {
            "spinui_reloaded/EQUI.xml": b"<XML>reload</XML>\n",
            "spinui_reloaded/art/frame.tga": b"TGA\x00reload",
            "spinui_pearlescent/EQUI.xml": b"<XML>pearl</XML>\n",
            "spinui_pearlescent/art/frame.tga": b"TGA\x00pearl",
            "spinui_glass/EQUI.xml": b"<XML>glass</XML>\n",
            "spinui_glass/art/frame.tga": b"TGA\x00glass",
        }
        for relative, content in fixtures.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        archive_path = root / "SpinUI-UI.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for relative in sorted(fixtures):
                archive.write(root / relative, relative)

        first = build_manifest(root, archive_path, "v1.2.3")
        second = build_manifest(root, archive_path, "1.2.3")
        if manifest_bytes(first) != manifest_bytes(second):
            raise AssertionError("manifest output is not deterministic")
        if first["releaseVersion"] != "1.2.3":
            raise AssertionError("release version was not normalized")
        if first["themes"]["spinui_glass"]["fileCount"] != 2:  # type: ignore[index]
            raise AssertionError("theme inventory is incomplete")

        (root / "spinui_glass" / "EQUI.xml").write_bytes(b"changed")
        try:
            build_manifest(root, archive_path, "1.2.3")
        except ManifestError as exc:
            if "inventory differs" not in str(exc) and "wrong" not in str(exc):
                raise
        else:
            raise AssertionError("stale archive was not rejected")

    print("SpinUI update manifest selftest: ALL PASS | deterministic + exact ZIP trees")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        self_test()
        return 0
    if not args.archive or not args.version or not args.output:
        parser.error("--archive, --version, and --output are required")

    payload = write_manifest(
        args.repo_root.resolve(),
        args.archive.resolve(),
        args.version,
        args.output.resolve(),
    )
    print(
        f"SpinUI update manifest: {args.output} | "
        f"version {payload['releaseVersion']} | "
        + " | ".join(
            f"{theme} {payload['themes'][theme]['fileCount']} files"  # type: ignore[index]
            for theme in THEMES
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ManifestError as exc:
        raise SystemExit(f"SpinUI update manifest: FAIL: {exc}") from exc
