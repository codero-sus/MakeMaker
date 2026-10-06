"""Bridge to ``mknative`` -- the C half of MakeMaker.

MakeMaker is a Python + C combination:

* Python renders templates and scaffolds projects.
* ``native/mknative.c`` (C11, no dependencies) probes the host toolchain and
  drives builds.  ``makemaker doctor/build/run`` shell out to it when it has
  been compiled, and fall back to equivalent Python otherwise, so the CLI
  always works.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "repo_root",
    "find_binary",
    "is_available",
    "build_binary",
    "run_native",
    "native_doctor",
    "python_doctor",
    "execute_commands",
]

_ENV_VAR = "MAKEMAKER_NATIVE"
BINARY_NAME = "mknative.exe" if os.name == "nt" else "mknative"


def repo_root() -> Path:
    """Root of the MakeMaker checkout (best effort)."""
    return Path(__file__).resolve().parent.parent


def candidate_paths() -> List[Path]:
    root = repo_root()
    return [
        root / "build" / BINARY_NAME,
        root / "native" / BINARY_NAME,
    ]


def find_binary() -> Optional[Path]:
    """Locate a compiled ``mknative``."""
    override = os.environ.get(_ENV_VAR)
    if override:
        path = Path(override)
        if path.is_file() and os.access(path, os.X_OK):
            return path
        return None
    for path in candidate_paths():
        if path.is_file() and os.access(path, os.X_OK):
            return path
    found = shutil.which("mknative")
    return Path(found) if found else None


def is_available() -> bool:
    return find_binary() is not None


def _compiler() -> Optional[str]:
    for name in ("cc", "gcc", "clang"):
        path = shutil.which(name)
        if path:
            return path
    return None


def build_binary(verbose: bool = False) -> Path:
    """Compile ``native/mknative.c`` and return the binary path."""
    source = repo_root() / "native" / "mknative.c"
    if not source.is_file():
        raise FileNotFoundError(f"missing native source: {source}")
    out_dir = repo_root() / "build"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / BINARY_NAME
    compiler = _compiler()
    if compiler is None:
        raise RuntimeError("no C compiler found (tried cc, gcc, clang)")
    command = [compiler, "-std=c11", "-O2", "-Wall", "-Wextra", str(source), "-o", str(target)]
    if verbose:
        print(" ".join(command), file=sys.stderr)
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"failed to compile mknative (exit {completed.returncode}):\n"
            f"{completed.stdout}{completed.stderr}"
        )
    return target


def run_native(args: Sequence[str], cwd: Optional[Path] = None, capture: bool = False) -> subprocess.CompletedProcess:
    """Run the companion binary."""
    binary = find_binary()
    if binary is None:
        raise RuntimeError("mknative is not built; run 'makemaker native build'")
    # The child writes straight to the terminal; without this our own buffered
    # header lands after the build output when stdout is a pipe.
    sys.stdout.flush()
    return subprocess.run(
        [str(binary), *args],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=capture,
    )


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

_PROBES: Dict[str, List[str]] = {
    "cc": ["--version"],
    "gcc": ["--version"],
    "g++": ["--version"],
    "clang": ["--version"],
    "cl": [],
    "make": ["--version"],
    "cmake": ["--version"],
    "ninja": ["--version"],
    "pkg-config": ["--version"],
    "python3": ["--version"],
    "java": ["-version"],
    "gradle": ["--version"],
    "dotnet": ["--version"],
    "xcodebuild": ["-version"],
    "xcrun": ["--version"],
    "adb": ["--version"],
    "git": ["--version"],
}


def _version_of(name: str) -> Optional[Dict[str, Any]]:
    path = shutil.which(name)
    if not path:
        return None
    args = _PROBES.get(name, ["--version"])
    version = ""
    if args:
        try:
            completed = subprocess.run(
                [path, *args], capture_output=True, text=True, timeout=15
            )
            output = (completed.stdout or completed.stderr or "").strip()
            version = output.splitlines()[0] if output else ""
        except (OSError, subprocess.SubprocessError):
            version = ""
    return {"path": path, "version": version}


def python_doctor() -> Dict[str, Any]:
    """Pure-Python equivalent of ``mknative doctor``."""
    tools = {name: _version_of(name) for name in _PROBES}
    return {
        "source": "python",
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "tools": {name: info for name, info in tools.items() if info},
        "missing": sorted(name for name, info in tools.items() if not info),
        "native": find_binary() is not None,
    }


def native_doctor(as_json: bool = True) -> Optional[Dict[str, Any]]:
    """Ask the C companion for a toolchain report."""
    binary = find_binary()
    if binary is None:
        return None
    args = [str(binary), "doctor"]
    if as_json:
        args.append("--json")
    completed = subprocess.run(args, capture_output=True, text=True)
    if completed.returncode != 0:
        return None
    if not as_json:
        return {"source": "native", "text": completed.stdout}
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    data["source"] = "native"
    data["native"] = True
    return data


# --------------------------------------------------------------------------
# build / run
# --------------------------------------------------------------------------


def execute_commands(
    commands: Sequence[Sequence[str]],
    cwd: Path,
    *,
    echo: bool = True,
) -> int:
    """Run a recipe's command list in order; return the first failure code."""
    for command in commands:
        if not command:
            continue
        argv = list(command)
        program = argv[0]
        if program.startswith("./") and os.name == "nt":
            argv[0] = program[2:] + ".bat"
        resolved = argv[0] if Path(argv[0]).is_absolute() or os.sep in argv[0] else shutil.which(argv[0])
        if resolved is None:
            print(f"makemaker: command not found: {argv[0]}", file=sys.stderr)
            return 127
        if echo:
            print(f"+ {' '.join(argv)}")
        sys.stdout.flush()
        completed = subprocess.run(argv, cwd=str(cwd))
        if completed.returncode != 0:
            return completed.returncode
    return 0
