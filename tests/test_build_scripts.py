"""Tests for the packaging scripts in tools/.

These are the scripts CI and the release workflow depend on, so a silent
regression here would produce broken artifacts rather than a failed build.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import build_binary  # noqa: E402
import build_native  # noqa: E402
import build_zipapp  # noqa: E402

HAS_COMPILER = any(shutil.which(c) for c in ("cc", "gcc", "clang"))

# mknative includes unistd.h / sys/wait.h / sys/utsname.h, so it will not
# compile with MSVC even when a `cc` is on PATH. It is POSIX-only by design.
IS_POSIX = os.name == "posix"


class VersionTest(unittest.TestCase):
    def test_read_version_matches_the_package(self):
        from makemaker import __version__

        self.assertEqual(build_native.read_version(), __version__)

    def test_read_version_is_not_a_placeholder(self):
        self.assertNotEqual(build_native.read_version(), "0.0.0")


class CompilerDiscoveryTest(unittest.TestCase):
    def test_finds_a_compiler_when_one_exists(self):
        if not HAS_COMPILER:
            self.skipTest("no C compiler installed")
        self.assertTrue(build_native.find_compiler())

    def test_a_preferred_compiler_is_honoured(self):
        if not shutil.which("gcc"):
            self.skipTest("gcc not installed")
        self.assertEqual(build_native.find_compiler("gcc"), "gcc")

    def test_missing_compiler_exits_with_a_useful_message(self):
        with self.assertRaises(SystemExit) as ctx:
            build_native.find_compiler("definitely-not-a-real-compiler-xyz")
        self.assertIn("no C compiler found", str(ctx.exception))


class ExecutableNameTest(unittest.TestCase):
    def test_adds_exe_only_on_windows(self):
        original = os.name
        try:
            os.name = "nt"
            self.assertEqual(build_native.executable_name("mknative"), "mknative.exe")
            self.assertEqual(
                build_native.executable_name("mknative.exe"), "mknative.exe"
            )
            os.name = "posix"
            self.assertEqual(build_native.executable_name("mknative"), "mknative")
        finally:
            os.name = original


@unittest.skipUnless(IS_POSIX, "mknative is POSIX-only")
class NativeBuildTest(unittest.TestCase):
    """Actually compile through the script, not just check its arguments."""

    @unittest.skipUnless(HAS_COMPILER, "no C compiler available")
    def test_builds_a_working_companion(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "mknative"
            produced = build_native.build(output=output)
            self.assertTrue(produced.is_file(), produced)

            completed = subprocess.run(
                [str(produced), "doctor", "--json"],
                capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn('"host"', completed.stdout)

    @unittest.skipUnless(HAS_COMPILER, "no C compiler available")
    def test_builds_against_a_missing_source_with_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Point at a directory that is not the repo.
            original = build_native.SOURCE
            build_native.SOURCE = Path(tmp) / "nope.c"
            try:
                with self.assertRaises(SystemExit):
                    build_native.build(output=Path(tmp) / "out" / "mknative")
            finally:
                build_native.SOURCE = original


class ZipappBuilderTest(unittest.TestCase):
    def test_staging_includes_templates_and_a_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = build_zipapp.stage(Path(tmp), REPO_ROOT)
            self.assertTrue((staging / "__main__.py").is_file())
            self.assertTrue((staging / "makemaker" / "cli.py").is_file())
            self.assertGreater(build_zipapp.count_templates(staging), 300)

    def test_staging_excludes_pycache(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = build_zipapp.stage(Path(tmp), REPO_ROOT)
            self.assertEqual(list(staging.rglob("__pycache__")), [])

    def test_count_templates_rejects_a_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                build_zipapp.count_templates(Path(tmp))


class BinaryBuilderTest(unittest.TestCase):
    """build_binary needs PyInstaller, which is intentionally optional."""

    def test_missing_pyinstaller_exits_with_install_instructions(self):
        original_which = shutil.which
        original_run = subprocess.run

        def no_pyinstaller(name, *args, **kwargs):
            return None

        def failing_probe(*args, **kwargs):
            class Result:
                returncode = 1

            return Result()

        build_binary.shutil.which = no_pyinstaller
        build_binary.subprocess.run = failing_probe
        try:
            with self.assertRaises(SystemExit) as ctx:
                build_binary.find_pyinstaller()
        finally:
            build_binary.shutil.which = original_which
            build_binary.subprocess.run = original_run

        message = str(ctx.exception)
        self.assertIn("PyInstaller is not installed", message)
        self.assertIn("zipapp", message, "should point at the dependency-free option")

    def test_add_data_separator_matches_the_platform(self):
        expected = ";" if os.name == "nt" else ":"
        self.assertEqual(build_binary.PATHSEP, expected)

    def test_build_reports_a_missing_templates_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = build_binary.PACKAGE
            build_binary.PACKAGE = Path(tmp) / "makemaker"
            try:
                with self.assertRaises(SystemExit):
                    build_binary.build(output_dir=Path(tmp) / "dist")
            finally:
                build_binary.PACKAGE = original


if __name__ == "__main__":
    unittest.main()
