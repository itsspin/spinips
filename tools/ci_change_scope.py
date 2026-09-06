#!/usr/bin/env python3
"""Classify a commit range for SpinUI's component-aware CI pipeline.

Push builds should validate only the component that changed. Manual and tagged
releases deliberately force both components so a published Windows package is
always assembled from freshly verified UI and Loremaster artifacts.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable


REPO = Path(__file__).resolve().parent.parent

UI_PREFIXES = (
    "docs/",
    "installer/",
    "layouts/",
    "spinui_glass/",
    "spinui_pearlescent/",
    "spinui_reloaded/",
)
LOREMASTER_PREFIXES = (
    "loremaster/",
    "loremaster-desktop/",
)
UI_EXACT = {
    "README.md",
    "UI_Spin_qeynos_LO1.ini",
}
SHARED_EXACT = {
    ".github/workflows/build-loremaster.yml",
    "tools/ci_change_scope.py",
    "tools/release_quality_gate.py",
}
LOREMASTER_TOOL_PREFIXES = (
    "tools/smoke_loremaster_",
)


@dataclass(frozen=True, slots=True)
class ChangeScope:
    ui: bool
    loremaster: bool
    paths: tuple[str, ...] = ()

    def payload(self) -> dict[str, object]:
        return {
            "ui": self.ui,
            "loremaster": self.loremaster,
            "paths": list(self.paths),
        }


def normalize_path(value: str) -> str:
    return value.strip().replace("\\", "/").lstrip("./")


def classify_paths(paths: Iterable[str], *, force_all: bool = False) -> ChangeScope:
    normalized = tuple(sorted({
        path for value in paths if (path := normalize_path(value))
    }))
    if force_all:
        return ChangeScope(True, True, normalized)

    ui = False
    loremaster = False
    for path in normalized:
        if path in SHARED_EXACT or path.startswith(".github/workflows/"):
            ui = loremaster = True
        elif path in UI_EXACT or path.startswith(UI_PREFIXES):
            ui = True
        elif path.startswith(LOREMASTER_PREFIXES):
            loremaster = True
        elif path.startswith(LOREMASTER_TOOL_PREFIXES):
            loremaster = True
        elif path.startswith("tools/"):
            # UI authoring and geometry tools make up the remainder. Unknown
            # paths outside these known roots fall through to both below.
            ui = True
        else:
            # A new root-level build input must not silently evade either job.
            ui = loremaster = True
    return ChangeScope(ui, loremaster, normalized)


def git(*arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=REPO, check=check,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    return result.stdout.strip()


def commit_exists(revision: str) -> bool:
    if not revision or set(revision) == {"0"}:
        return False
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"], cwd=REPO,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def changed_paths(base: str, head: str, default_branch: str) -> tuple[str, ...]:
    head = head or "HEAD"
    if not commit_exists(base):
        remote_default = f"origin/{default_branch or 'main'}"
        candidate = git("merge-base", head, remote_default, check=False)
        base = candidate if commit_exists(candidate) else f"{head}^"
    output = git(
        "diff", "--name-only", "--diff-filter=ACMRD", base, head,
    )
    return tuple(line for line in output.splitlines() if line.strip())


def write_github_outputs(path: Path, scope: ChangeScope) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"ui={'true' if scope.ui else 'false'}\n")
        output.write(f"loremaster={'true' if scope.loremaster else 'false'}\n")


def self_test() -> None:
    cases = (
        (("spinui_reloaded/EQUI_PlayerWindow.xml",), (True, False)),
        (("spinui_pearlescent/EQUI.xml",), (True, False)),
        (("tools/restyle_combat.py",), (True, False)),
        (("loremaster/desktop_worker.py",), (False, True)),
        (("loremaster-desktop/src/App.tsx",), (False, True)),
        (("tools/smoke_loremaster_engine.py",), (False, True)),
        (("README.md",), (True, False)),
        (("tools/release_quality_gate.py",), (True, True)),
        (("tools/ci_change_scope.py",), (True, True)),
        ((".github/workflows/build-loremaster.yml",), (True, True)),
        (("spinui_glass/EQUI.xml", "loremaster/mez_timer.py"), (True, True)),
        (("brand-new-root.txt",), (True, True)),
    )
    for paths, expected in cases:
        scope = classify_paths(paths)
        actual = (scope.ui, scope.loremaster)
        if actual != expected:
            raise AssertionError(f"{paths}: {actual} != {expected}")
    forced = classify_paths((), force_all=True)
    if not forced.ui or not forced.loremaster:
        raise AssertionError("release scope must force both components")
    print(f"CI change scope selftest: ALL PASS | {len(cases) + 1} cases")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--default-branch", default="main")
    parser.add_argument("--force-all", action="store_true")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        self_test()
        return 0
    paths = () if args.force_all else changed_paths(
        args.base, args.head, args.default_branch)
    scope = classify_paths(paths, force_all=args.force_all)
    if args.github_output:
        write_github_outputs(args.github_output, scope)
    print(json.dumps(scope.payload(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
