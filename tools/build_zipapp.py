#!/usr/bin/env python3
"""Build MakeMaker as a single-file executable (a zipapp).

The result is one file -- ``dist/makemaker.pyz`` -- that contains the Python
package and every bundled template. It runs on Linux, macOS and Windows
anywhere Python 3.9+ is installed::

    python3 tools/build_zipapp.py
    ./dist/makemaker.pyz list
    ./dist/makemaker.pyz new "Todo List" --target linux

On Unix the file carries a shebang and is made executable, so it can be run
directly. On Windows, run it as ``python dist\\makemaker.pyz``.

This script uses only the standard library, matching MakeMaker's own
no-dependency rule. It is *not* a cross-compiler: a zipapp is bytecode plus
data, so the one file produced here works on every platform, but it does need
a Python interpreter present. ``tools/build_native.py`` covers the optional C
companion, which is platform specific.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import zipapp
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "makemaker"
DEFAULT_INTERPRETER = "/usr/bin/env python3"

#: Directories that must never end up in the archive.
SKIP_DIRS = {"__pycache__", "templates_backup", ".pytest_cache", ".mypy_cache"}


def stage(build_dir: Path, repo_root: Path) -> Path:
    """Copy the package into a staging directory ready to be zipped."""
    source = repo_root / PACKAGE
    if not source.is_dir():
        raise SystemExit(f"cannot find {source}")

    destination = build_dir / PACKAGE
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(*SKIP_DIRS, "*.pyc"),
    )

    # The archive needs a top-level __main__.py; the package's own
    # __main__.py is relative-import based, so write an absolute one.
    (build_dir / "__main__.py").write_text(
        '"""Zipapp entry point for MakeMaker."""\n'
        "\n"
        "import sys\n"
        "\n"
        "from makemaker.cli import main\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    sys.exit(main())\n",
        encoding="utf-8",
    )
    return build_dir


def count_templates(staging: Path) -> int:
    """How many template files were staged -- a cheap sanity check."""
    templates = staging / PACKAGE / "templates"
    if not templates.is_dir():
        raise SystemExit(f"no templates staged at {templates}")
    return sum(1 for path in templates.rglob("*") if path.is_file())


def build(
    output: Path,
    interpreter: str,
    compressed: bool,
    repo_root: Path = REPO_ROOT,
) -> Path:
    """Build the zipapp and return its path."""
    output.parent.mkdir(parents=True, exist_ok=True)
    build_dir = output.parent / "build-zipapp"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    staging = stage(build_dir, repo_root)
    templates = count_templates(staging)

    zipapp.create_archive(
        source=staging,
        target=output,
        interpreter=interpreter,
        main=None,  # __main__.py is already staged
        compressed=compressed,
    )
    shutil.rmtree(build_dir, ignore_errors=True)

    if os.name == "posix":
        mode = output.stat().st_mode
        output.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    size_kb = output.stat().st_size / 1024
    print(f"wrote {output} ({size_kb:.0f} KiB, {templates} template files)")
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output", "-o", type=Path,
        default=REPO_ROOT / "dist" / "makemaker.pyz",
        help="where to write the zipapp (default: dist/makemaker.pyz)",
    )
    parser.add_argument(
        "--interpreter", "-p", default=DEFAULT_INTERPRETER,
        help=f"shebang line (default: {DEFAULT_INTERPRETER})",
    )
    parser.add_argument(
        "--uncompressed", action="store_true",
        help="do not compress the archive (faster startup, larger file)",
    )
    args = parser.parse_args(argv)

    build(args.output, args.interpreter, compressed=not args.uncompressed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
