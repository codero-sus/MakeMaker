#!/usr/bin/env python3
"""Compile ``native/mknative.c`` for the current host.

This exists so the C companion can be built on Windows too, where ``make``
is usually absent. It shells out to whatever C compiler it can find::

    python3 tools/build_native.py
    python3 tools/build_native.py --compiler clang --output build/mknative

Standard library only. It is a *host* build: cross-compiling to another
platform needs that platform's toolchain, which is why the release workflow
builds on one runner per operating system rather than from a single machine.
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
SOURCE = REPO_ROOT / "native" / "mknative.c"

CANDIDATE_COMPILERS = ("cc", "gcc", "clang", "zig cc")


def read_version(repo_root: Path = REPO_ROOT) -> str:
    """Pull the version from makemaker/__init__.py without importing it."""
    init = repo_root / "makemaker" / "__init__.py"
    for line in init.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


def find_compiler(preferred: str = "") -> str:
    """Return a usable compiler command, or raise."""
    order = [preferred] if preferred else list(CANDIDATE_COMPILERS)
    for candidate in order:
        first = candidate.split()[0]
        if shutil.which(first):
            return candidate
    raise SystemExit(
        "no C compiler found; install gcc, clang or cc and retry "
        "(or pass --compiler)"
    )


def executable_name(name: str) -> str:
    """Add .exe on Windows, where the loader requires it."""
    if os.name == "nt" and not name.lower().endswith(".exe"):
        return name + ".exe"
    return name


def build(
    compiler: str = "",
    output: Path = REPO_ROOT / "build" / "mknative",
    extra_flags: list = (),
) -> Path:
    """Compile the companion and return the path to the binary."""
    if not SOURCE.is_file():
        raise SystemExit(f"cannot find {SOURCE}")

    cc = find_compiler(compiler)
    version = read_version()
    output = output.parent / executable_name(output.name)
    output.parent.mkdir(parents=True, exist_ok=True)

    command = [
        *cc.split(),
        "-std=c11", "-O2", "-Wall", "-Wextra", "-pedantic",
        f'-DMAKERNATIVE_VERSION="{version}"',
        *extra_flags,
        str(SOURCE),
        "-o", str(output),
    ]
    print("+", " ".join(command))
    subprocess.run(command, check=True, cwd=str(REPO_ROOT))

    size_kb = output.stat().st_size / 1024
    print(
        f"wrote {output} ({size_kb:.0f} KiB, "
        f"{platform.system()} {platform.machine()})"
    )
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--compiler", default="", help="cc, gcc, clang, ...")
    parser.add_argument(
        "--output", "-o", type=Path,
        default=REPO_ROOT / "build" / "mknative",
        help="where to write the binary (default: build/mknative)",
    )
    parser.add_argument(
        "flags", nargs="*", help="extra compiler flags, e.g. -- -static"
    )
    args = parser.parse_args(argv)

    build(args.compiler, args.output, list(args.flags))
    return 0


if __name__ == "__main__":
    sys.exit(main())
