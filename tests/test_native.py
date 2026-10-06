"""Tests for the C companion (mknative) and the Python fallback.

These compile native/mknative.c and then use it to build and run a generated
project, so the Python -> C -> make -> compiler path is exercised for real.
They are skipped when no C compiler or no GNU make is available.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from makemaker import native
from makemaker.generator import generate, read_project_metadata
from makemaker.recipes import build_commands
from makemaker.registry import load_registry, resolve_variable_defaults
from makemaker.variables import build_variables

HAS_COMPILER = native._compiler() is not None
HAS_MAKE = shutil.which("make") is not None


@unittest.skipUnless(HAS_COMPILER, "no C compiler available")
class NativeBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = native.build_binary()

    def test_binary_exists_and_is_executable(self):
        self.assertTrue(self.binary.is_file())
        self.assertEqual(self.binary.name, "mknative")

    def test_compiles_without_warnings(self):
        compiler = native._compiler()
        source = native.repo_root() / "native" / "mknative.c"
        completed = subprocess.run(
            [compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
             str(source), "-o", str(self.binary.with_suffix(".warnings-check"))],
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.binary.with_suffix(".warnings-check").unlink(missing_ok=True)

    def test_version(self):
        completed = subprocess.run(
            [str(self.binary), "version"], capture_output=True, text=True
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("mknative", completed.stdout)

    def test_usage_on_no_arguments(self):
        completed = subprocess.run([str(self.binary)], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("Usage:", completed.stderr)

    def test_unknown_command(self):
        completed = subprocess.run(
            [str(self.binary), "frobnicate"], capture_output=True, text=True
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("unknown command", completed.stderr)

    def test_doctor_json(self):
        completed = subprocess.run(
            [str(self.binary), "doctor", "--json"], capture_output=True, text=True
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertIn("host", report)
        for key in ("system", "release", "machine"):
            self.assertTrue(report["host"].get(key), key)
        self.assertIn("tools", report)
        self.assertIn("missing", report)
        # This suite is running under a compiler, so at least one tool exists.
        self.assertTrue(report["tools"], "mknative found no tools at all")

    def test_doctor_text(self):
        completed = subprocess.run(
            [str(self.binary), "doctor"], capture_output=True, text=True
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("host:", completed.stdout)

    def test_doctor_agrees_with_the_python_probe(self):
        """Both halves must report the same host, or one of them is lying."""
        from_native = json.loads(
            subprocess.run(
                [str(self.binary), "doctor", "--json"], capture_output=True, text=True
            ).stdout
        )
        from_python = native.python_doctor()
        self.assertEqual(from_native["host"]["system"], from_python["host"]["system"])
        self.assertEqual(from_native["host"]["machine"], from_python["host"]["machine"])
        for tool in ("make", "git"):
            if tool in from_python["tools"]:
                self.assertIn(tool, from_native["tools"], f"mknative missed {tool}")


@unittest.skipUnless(HAS_COMPILER and HAS_MAKE, "needs a C compiler and make")
class NativeDrivesGeneratedProjectTests(unittest.TestCase):
    """Generate a real project, then build/run it through both executors."""

    @classmethod
    def setUpClass(cls):
        cls.binary = native.build_binary()
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tempdir.name)

        registry = load_registry()
        template = registry.get("linux")
        context = build_variables(
            "Native Check", target="linux", flavor="console", language="c"
        )
        context.update(resolve_variable_defaults(template, context))
        cls.project = cls.root / "native-check"
        generate(
            template, context, cls.project,
            commands=build_commands(template, context),
        )

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_project_metadata_is_readable_by_the_c_side(self):
        completed = subprocess.run(
            [str(self.binary), "info", str(self.project)],
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Native Check", completed.stdout)
        self.assertIn("linux", completed.stdout)
        self.assertIn("build", completed.stdout)

    def test_info_json_round_trips(self):
        completed = subprocess.run(
            [str(self.binary), "info", str(self.project), "--json"],
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["project"]["name"], "Native Check")
        self.assertEqual(payload["commands"]["build"], [["make"]])

    def test_info_rejects_a_foreign_directory(self):
        completed = subprocess.run(
            [str(self.binary), "info", str(self.root)], capture_output=True, text=True
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("not a MakeMaker project", completed.stderr)

    def test_mknative_builds_and_runs_the_generated_app(self):
        build = subprocess.run(
            [str(self.binary), "build", str(self.project)],
            capture_output=True, text=True, cwd=str(self.project),
        )
        self.assertEqual(build.returncode, 0, build.stderr + build.stdout)
        self.assertIn("+ make", build.stdout)

        executable = self.project / "build" / "native-check"
        self.assertTrue(executable.is_file(), "the build produced no binary")

        run = subprocess.run(
            [str(self.binary), "run", str(self.project)],
            capture_output=True, text=True, cwd=str(self.project),
        )
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.assertIn("Native Check says hello (2 + 3 = 5)", run.stdout)

    def test_mknative_runs_the_generated_tests(self):
        completed = subprocess.run(
            [str(self.binary), "test", str(self.project)],
            capture_output=True, text=True, cwd=str(self.project),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIn("all tests passed", completed.stdout)

    def test_python_fallback_builds_the_same_project(self):
        """The Python executor must accept the same recipe shape."""
        clean = subprocess.run(
            ["make", "clean"], cwd=str(self.project), capture_output=True, text=True
        )
        self.assertEqual(clean.returncode, 0, clean.stderr)

        metadata = read_project_metadata(self.project)
        status = native.execute_commands(
            metadata["commands"]["build"], self.project, echo=False
        )
        self.assertEqual(status, 0)
        self.assertTrue((self.project / "build" / "native-check").is_file())

    def test_execute_commands_reports_a_missing_program(self):
        status = native.execute_commands(
            [["definitely-not-a-real-program"]], self.project, echo=False
        )
        self.assertEqual(status, 127)

    def test_mknative_clean_removes_build_output(self):
        subprocess.run(
            [str(self.binary), "build", str(self.project)],
            capture_output=True, text=True, cwd=str(self.project),
        )
        self.assertTrue((self.project / "build").is_dir())
        completed = subprocess.run(
            [str(self.binary), "clean", str(self.project)],
            capture_output=True, text=True, cwd=str(self.project),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse((self.project / "build").exists())

    def test_unknown_recipe_is_reported(self):
        completed = subprocess.run(
            [str(self.binary), "deploy", str(self.project)],
            capture_output=True, text=True,
        )
        # 'deploy' is not a mknative subcommand at all
        self.assertEqual(completed.returncode, 2)


class BinaryDiscoveryTests(unittest.TestCase):
    def test_environment_override_wins(self):
        import os

        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "mknative"
            fake.write_text("#!/bin/sh\nexit 0\n")
            fake.chmod(0o755)
            previous = os.environ.get(native._ENV_VAR)
            os.environ[native._ENV_VAR] = str(fake)
            try:
                self.assertEqual(native.find_binary(), fake)
                self.assertTrue(native.is_available())
            finally:
                if previous is None:
                    os.environ.pop(native._ENV_VAR, None)
                else:
                    os.environ[native._ENV_VAR] = previous

    def test_missing_override_returns_none(self):
        import os

        previous = os.environ.get(native._ENV_VAR)
        os.environ[native._ENV_VAR] = "/nonexistent/mknative"
        try:
            self.assertIsNone(native.find_binary())
        finally:
            if previous is None:
                os.environ.pop(native._ENV_VAR, None)
            else:
                os.environ[native._ENV_VAR] = previous


if __name__ == "__main__":
    unittest.main()
