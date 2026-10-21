#!/usr/bin/env python3
"""Build a self-contained native executable with PyInstaller.

This is the *optional* no-Python-needed distribution. Unlike the zipapp it
does not require an interpreter on the target machine, but it is platform
specific: PyInstaller cannot cross-compile, so the release workflow runs this
on one runner per operating system.

    python3 tools/build_binary.py

PyInstaller is a **build-time** tool only -- MakeMaker itself still has no
runtime dependencies, and nothing PyInstaller produces is imported by the
Python package.

Standard library only, so this script runs anywhere.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "makemaker"

#: PyInstaller's --add-data separator differs by platform.
PATHSEP = ";" if os.name == "nt" else ":"

ENTRY = """\
\"\"\"PyInstaller entry point for MakeMaker.\"\"\"
import sys

from makemaker.cli import main

if __name__ == "__main__":
    sys.exit(main())
"""


def find_pyinstaller() -> str:
    """Prefer the console script, fall back to `python -m PyInstaller`."""
    if shutil.which("pyinstaller"):
        return "pyinstaller"
    probe = subprocess.run(
        [sys.executable, "-c", "import PyInstaller"],
        capture_output=True,
    )
    if probe.returncode == 0:
        return f"{sys.executable} -m PyInstaller"
    raise SystemExit(
        "PyInstaller is not installed.\n"
        "  python3 -m pip install pyinstaller\n"
        "It is needed only to build this optional binary; the zipapp "
        "(tools/build_zipapp.py) needs nothing but the standard library."
    )


def build(
    output_dir: Path = REPO_ROOT / "dist",
    work_dir: Path = REPO_ROOT / "build" / "binary",
    onefile: bool = True,
) -> Path:
    """Run PyInstaller and return the path to the produced executable."""
    templates = PACKAGE / "templates"
    if not templates.is_dir():
        raise SystemExit(f"cannot find {templates}")

    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    entry = work_dir / "entry.py"
    entry.write_text(ENTRY, encoding="utf-8")

    name = "makemaker.exe" if os.name == "nt" else "makemaker"
    command = [
        *find_pyinstaller().split(),
        "--name", name,
        "--noconfirm",
        "--clean",
        "--distpath", str(output_dir),
        "--workpath", str(work_dir / "work"),
        "--specpath", str(work_dir),
        "--paths", str(REPO_ROOT),
        "--add-data", f"{templates}{PATHSEP}makemaker/templates",
    ]
    if onefile:
        command.append("--onefile")
    command.append(str(entry))

    print("+", " ".join(command))
    subprocess.run(command, check=True)

    produced = output_dir / name
    if not produced.is_file():
        raise SystemExit(f"PyInstaller finished but {produced} is missing")

    size_mb = produced.stat().st_size / (1024 * 1024)
    print(
        f"wrote {produced} ({size_mb:.1f} MiB, "
        f"{platform.system()} {platform.machine()})"
    )
    return produced


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output-dir", "-o", type=Path, default=REPO_ROOT / "dist",
        help="where to write the binary (default: dist/)",
    )
    parser.add_argument(
        "--onedir", action="store_true",
        help="produce a directory bundle instead of a single file",
    )
    args = parser.parse_args(argv)

    build(args.output_dir, onefile=not args.onedir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
