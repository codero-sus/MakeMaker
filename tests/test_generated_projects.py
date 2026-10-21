"""End-to-end tests: generate a project, then build and run it for real.

These are the tests that prove a template produces a *working* project rather
than merely a plausible looking one. Each is skipped when its toolchain is not
installed, so the suite still runs on a bare machine.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from makemaker.generator import generate, read_project_metadata
from makemaker.recipes import build_commands
from makemaker.registry import load_registry, resolve_variable_defaults
from makemaker.variables import build_variables

HAS_MAKE = shutil.which("make") is not None
HAS_PYTHON = shutil.which("python3") is not None
HAS_NODE = shutil.which("node") is not None
HAS_BASH = shutil.which("bash") is not None
HAS_PERL = shutil.which("perl") is not None
HAS_AWK = shutil.which("awk") is not None

# Windows cannot execute a shebang script via subprocess, and has no Unix
# permission bits, so the script-driven projects are only exercised on POSIX.
IS_POSIX = os.name == "posix"


#: Node's test runner prints its summary as "# fail 0" in TAP mode and as
#: "ℹ fail 0" in the newer spec reporter, so match the substance, not the glyph.
NO_FAILURES = re.compile(r"(?<![\w-])fail\s+0(?![0-9])")


def assert_node_tests_passed(test_case, stdout: str) -> None:
    test_case.assertRegex(stdout, NO_FAILURES)


def scaffold(target: str, language: str, destination: Path) -> Path:
    """Generate one project and return its directory."""
    registry = load_registry()
    template = registry.get(target)
    context = build_variables(
        "Gen Check", target=template.os, flavor=template.default_flavor, language=language
    )
    context.update(resolve_variable_defaults(template, context))
    generate(
        template, context, destination,
        commands=build_commands(template, context),
    )
    return destination


@unittest.skipUnless(HAS_MAKE and HAS_PYTHON, "needs make and python3")
class GeneratedPythonProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.project = scaffold(
            "python", "python", Path(cls.tempdir.name) / "gen-check"
        )

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_make_test_passes(self):
        completed = subprocess.run(
            ["make", "test"], cwd=str(self.project),
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("OK", completed.stderr + completed.stdout)

    def test_cli_runs(self):
        completed = subprocess.run(
            ["make", "run"], cwd=str(self.project),
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Gen Check says hello (2 + 3 = 5)", completed.stdout)

    def test_cli_accepts_arguments(self):
        # Point PYTHONPATH at src/ so the package has to be importable on its
        # own, without make's help. Inherit the parent environment instead of
        # replacing it: on Windows a child Python started without SystemRoot
        # cannot seed its hash and aborts during startup, and a hardcoded
        # POSIX PATH resolves nothing there.
        env = dict(os.environ, PYTHONPATH=str(self.project / "src"))
        completed = subprocess.run(
            [sys.executable, "-m", "gen_check", "1", "2", "3", "4"],
            cwd=str(self.project), capture_output=True, text=True, env=env,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "10")

    def test_lint_target_byte_compiles(self):
        completed = subprocess.run(
            ["make", "lint"], cwd=str(self.project),
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


@unittest.skipUnless(HAS_NODE, "needs node")
class GeneratedNodeProjectTests(unittest.TestCase):
    """Both languages, because they take different paths through the template."""

    def run_for_language(self, language: str) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        project = scaffold(
            "node", language, Path(tempdir.name) / "gen-check"
        )
        self.project = project
        return project

    def test_typescript_tests_pass_without_a_build_step(self):
        project = self.run_for_language("typescript")
        self.assertTrue((project / "src" / "index.ts").is_file())
        self.assertFalse((project / "node_modules").exists(), "should need no install")
        completed = subprocess.run(
            ["node", "--test"], cwd=str(project), capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        assert_node_tests_passed(self, completed.stdout)

    def test_javascript_tests_pass(self):
        project = self.run_for_language("javascript")
        completed = subprocess.run(
            ["node", "--test"], cwd=str(project), capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        assert_node_tests_passed(self, completed.stdout)

    def test_cli_runs_and_sums_arguments(self):
        for language, extension in (("typescript", "ts"), ("javascript", "js")):
            project = self.run_for_language(language)
            entry = project / "src" / f"index.{extension}"

            plain = subprocess.run(
                ["node", str(entry)], cwd=str(project), capture_output=True, text=True,
            )
            self.assertEqual(plain.returncode, 0, plain.stderr)
            self.assertIn("Gen Check says hello (2 + 3 = 5)", plain.stdout)

            summed = subprocess.run(
                ["node", str(entry), "1", "2", "3", "4"],
                cwd=str(project), capture_output=True, text=True,
            )
            self.assertEqual(summed.returncode, 0, summed.stderr)
            self.assertEqual(summed.stdout.strip(), "10")


@unittest.skipUnless(HAS_MAKE, "needs make")
class GeneratedProjectMetadataTests(unittest.TestCase):
    """Every generated project must record a recipe the CLI can drive."""

    def test_every_target_records_a_build_recipe(self):
        import json

        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)

        registry = load_registry()
        for template in registry.sorted():
            flavor = template.default_flavor
            language = template.language(None, flavor)
            project = scaffold(template.id, language, root / template.id)
            metadata = json.loads(
                (project / ".makemaker" / "project.json").read_text()
            )
            self.assertEqual(metadata["template"], template.id)
            self.assertIn("build", metadata["commands"], template.id)
            for action, recipe in metadata["commands"].items():
                for command in recipe:
                    self.assertIsInstance(command, list, f"{template.id}:{action}")
                    self.assertTrue(
                        all(isinstance(part, str) for part in command),
                        f"{template.id}:{action} -> {command!r}",
                    )


@unittest.skipUnless(IS_POSIX, "needs POSIX script execution")
@unittest.skipUnless(HAS_BASH, "needs bash")
class GeneratedBashProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.project = scaffold("bash", "bash", Path(cls.tempdir.name) / "gen-check")

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_scripts_are_executable(self):
        for name in ("bin/gen-check", "run-tests.sh"):
            path = self.project / name
            self.assertTrue(path.exists(), f"{name} was not generated")
            self.assertTrue(os.access(path, os.X_OK), f"{name} is not executable")

    def test_the_cli_runs_and_greets(self):
        completed = subprocess.run(
            ["./bin/gen-check", "Ada"], cwd=str(self.project),
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Hello, Ada!", completed.stdout)

    def test_its_own_test_suite_passes(self):
        completed = subprocess.run(
            ["./run-tests.sh"], cwd=str(self.project),
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("0 failed", completed.stdout)

    def test_recorded_recipe_matches_the_working_command(self):
        commands = read_project_metadata(self.project)["commands"]
        self.assertEqual(commands["test"], [["make", "test"]])


@unittest.skipUnless(HAS_PERL, "needs perl")
class GeneratedPerlProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.project = scaffold("perl", "perl", Path(cls.tempdir.name) / "gen-check")

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_module_and_script_compile(self):
        for path in ("lib/GenCheck.pm", "bin/gen-check.pl"):
            completed = subprocess.run(
                ["perl", "-Ilib", "-c", path], cwd=str(self.project),
                capture_output=True, text=True,
            )
            self.assertEqual(
                completed.returncode, 0,
                f"{path} failed to compile: {completed.stderr}",
            )

    def test_the_cli_runs_and_greets(self):
        completed = subprocess.run(
            ["perl", "-Ilib", "bin/gen-check.pl", "Ada"],
            cwd=str(self.project), capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Hello, Ada!", completed.stdout)

    def test_the_version_flag_reports_the_project_version(self):
        completed = subprocess.run(
            ["perl", "-Ilib", "bin/gen-check.pl", "--version"],
            cwd=str(self.project), capture_output=True, text=True,
        )
        self.assertEqual(completed.stdout.strip(), "0.1.0")


@unittest.skipUnless(HAS_NODE, "needs node")
class GeneratedWebProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.project = scaffold("web", "javascript", Path(cls.tempdir.name) / "gen-check")

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_scripts_parse(self):
        for path in ("src/gen_check.js", "src/main.js"):
            completed = subprocess.run(
                ["node", "--check", path], cwd=str(self.project),
                capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_node_test_suite_passes(self):
        completed = subprocess.run(
            ["node", "--test"], cwd=str(self.project),
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        assert_node_tests_passed(self, completed.stdout)

    def test_index_html_references_the_generated_assets(self):
        html = (self.project / "index.html").read_text(encoding="utf-8")
        self.assertIn("src/main.js", html)
        self.assertIn("styles/main.css", html)
        # No template syntax may survive into the page.
        self.assertNotIn("{{", html)


@unittest.skipUnless(IS_POSIX, "needs POSIX script execution")
@unittest.skipUnless(HAS_AWK, "needs awk")
class GeneratedAwkProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.project = scaffold("awk", "awk", Path(cls.tempdir.name) / "gen-check")

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_scripts_are_executable(self):
        for name in ("bin/gen-check", "run-tests.sh", "lib/greet.awk"):
            path = self.project / name
            self.assertTrue(path.exists(), f"{name} was not generated")
            self.assertTrue(os.access(path, os.X_OK), f"{name} is not executable")

    def test_the_cli_runs_and_greets(self):
        completed = subprocess.run(
            ["./bin/gen-check", "Ada"], cwd=str(self.project),
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Hello, Ada!", completed.stdout)

    def test_its_own_test_suite_passes(self):
        completed = subprocess.run(
            ["./run-tests.sh"], cwd=str(self.project),
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("0 failed", completed.stdout)


if __name__ == "__main__":
    unittest.main()
