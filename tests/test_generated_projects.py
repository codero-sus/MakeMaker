"""End-to-end tests: generate a project, then build and run it for real.

These are the tests that prove a template produces a *working* project rather
than merely a plausible looking one. Each is skipped when its toolchain is not
installed, so the suite still runs on a bare machine.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from makemaker.generator import generate
from makemaker.recipes import build_commands
from makemaker.registry import load_registry, resolve_variable_defaults
from makemaker.variables import build_variables

HAS_MAKE = shutil.which("make") is not None
HAS_PYTHON = shutil.which("python3") is not None
HAS_NODE = shutil.which("node") is not None


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
        env = {"PYTHONPATH": str(self.project / "src"), "PATH": "/usr/bin:/bin"}
        completed = subprocess.run(
            ["python3", "-m", "gen_check", "1", "2", "3", "4"],
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
        self.assertIn("# fail 0", completed.stdout)

    def test_javascript_tests_pass(self):
        project = self.run_for_language("javascript")
        completed = subprocess.run(
            ["node", "--test"], cwd=str(project), capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("# fail 0", completed.stdout)

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


if __name__ == "__main__":
    unittest.main()
