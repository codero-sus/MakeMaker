"""Tests for the command line interface."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from makemaker import __version__
from makemaker.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main


def run_cli(*argv):
    """Invoke the CLI in-process and capture its output and exit code.

    argparse reports usage errors by raising SystemExit(2); that is a real
    exit status, so surface it the same way main() would.
    """
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(["--color", "never", *argv])
        except SystemExit as exit_error:
            code = int(exit_error.code or 0)
    return code, out.getvalue(), err.getvalue()


class BasicCommandTests(unittest.TestCase):
    def test_version(self):
        code, out, _ = run_cli("version")
        self.assertEqual(code, EXIT_OK)
        self.assertIn(__version__, out)

    def test_version_json(self):
        code, out, _ = run_cli("version", "--json")
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(json.loads(out)["makemaker"], __version__)

    def test_no_command_prints_help(self):
        code, out, _ = run_cli()
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("usage:", out)

    def test_list_shows_all_targets(self):
        code, out, _ = run_cli("list")
        self.assertEqual(code, EXIT_OK)
        for target in ("android", "ios", "linux", "macos", "windows"):
            self.assertIn(target, out)

    def test_list_json_is_parseable(self):
        code, out, _ = run_cli("list", "--json")
        self.assertEqual(code, EXIT_OK)
        payload = json.loads(out)
        self.assertEqual({item["id"] for item in payload},
                         {"android", "ios", "linux", "macos", "windows"})

    def test_info_lists_files(self):
        code, out, _ = run_cli("info", "linux")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Makefile", out)
        self.assertIn("src/app.h", out)

    def test_info_json(self):
        code, out, _ = run_cli("info", "linux", "--json")
        self.assertEqual(code, EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(payload["id"], "linux")
        self.assertIn("Makefile", payload["files"])

    def test_info_rejects_unknown_target(self):
        code, _out, err = run_cli("info", "beos")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("unknown target", err)

    def test_template_check_passes(self):
        code, out, _ = run_cli("template", "check")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("render cleanly", out)

    def test_template_check_counts_only_reachable_combinations(self):
        """The headline number must match what `makemaker new` can generate.

        5 templates expand to 12 reachable flavour/language combinations;
        the raw cross product is 16, four of which the CLI rejects.
        """
        from makemaker.registry import load_registry

        registry = load_registry()
        reachable = sum(
            len(template.languages_for(flavor) or [""])
            for template in registry.sorted()
            for flavor in (template.flavors or [""])
        )
        cross_product = sum(
            len(template.flavors or [""]) * len(template.languages or [""])
            for template in registry.sorted()
        )
        self.assertEqual(reachable, 12)
        self.assertGreater(cross_product, reachable)

        code, out, _ = run_cli("template", "check")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn(f"OK {reachable} template combination(s)", out)
        self.assertIn("unreachable", out)

    def test_template_check_all_includes_unreachable_combinations(self):
        from makemaker.registry import load_registry

        registry = load_registry()
        cross_product = sum(
            len(template.flavors or [""]) * len(template.languages or [""])
            for template in registry.sorted()
        )
        code, out, _ = run_cli("template", "check", "--all")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn(f"OK {cross_product} template combination(s)", out)
        self.assertIn("full cross product", out)

    def test_doctor_reports_the_host(self):
        code, out, _ = run_cli("doctor", "--no-native")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Host", out)
        self.assertIn("Toolchain", out)

    def test_doctor_json(self):
        code, out, _ = run_cli("doctor", "--no-native", "--json")
        self.assertEqual(code, EXIT_OK)
        payload = json.loads(out)
        self.assertIn("host", payload)
        self.assertIn("tools", payload)


class NewCommandTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_creates_project_in_a_slug_directory(self):
        code, out, _ = run_cli(
            "new", "Todo List", "--target", "linux", "--lang", "c",
            "--out", str(self.root), "--yes",
        )
        self.assertEqual(code, EXIT_OK, out)
        project = self.root / "todo-list"
        self.assertTrue((project / "Makefile").is_file())
        self.assertTrue((project / "src" / "main.c").is_file())
        self.assertTrue((project / ".makemaker" / "project.json").is_file())

    def test_multiple_targets_get_separate_directories(self):
        code, _out, _err = run_cli(
            "new", "Todo List",
            "--target", "linux", "--target", "macos",
            "--out", str(self.root), "--yes",
        )
        self.assertEqual(code, EXIT_OK)
        self.assertTrue((self.root / "todo-list-linux").is_dir())
        self.assertTrue((self.root / "todo-list-macos").is_dir())

    def test_dry_run_creates_nothing(self):
        code, out, _ = run_cli(
            "new", "Ghost", "--target", "linux", "--out", str(self.root),
            "--yes", "--dry-run",
        )
        self.assertEqual(code, EXIT_OK)
        self.assertIn("would write", out)
        self.assertFalse((self.root / "ghost").exists())

    def test_set_overrides_a_template_variable(self):
        code, _out, _err = run_cli(
            "new", "Todo List", "--target", "linux", "--lang", "c",
            "--out", str(self.root), "--yes", "--set", "app_name=Overridden",
        )
        self.assertEqual(code, EXIT_OK)
        makefile = (self.root / "todo-list" / "Makefile").read_text()
        self.assertIn("Overridden", makefile)

    def test_rejects_a_bad_set_pair(self):
        code, _out, err = run_cli(
            "new", "Todo List", "--target", "linux", "--out", str(self.root),
            "--yes", "--set", "nonsense",
        )
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("key=value", err)

    def test_rejects_an_invalid_project_name(self):
        code, _out, err = run_cli(
            "new", "bad/name", "--target", "linux", "--out", str(self.root), "--yes"
        )
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("invalid project name", err)

    def test_missing_target_is_a_usage_error(self):
        code, _out, err = run_cli(
            "new", "Todo List", "--out", str(self.root), "--yes"
        )
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("--target", err)

    def test_unknown_target_is_reported(self):
        code, _out, err = run_cli(
            "new", "Todo List", "--target", "amiga", "--out", str(self.root), "--yes"
        )
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("unknown target", err)

    def test_refuses_to_clobber_without_force(self):
        for extra in ([], ["--force"]):
            code, _out, err = run_cli(
                "new", "Todo List", "--target", "linux", "--out", str(self.root),
                "--yes", *extra,
            )
            self.assertEqual(code, EXIT_OK, err)

    def test_clobbering_without_force_is_an_error(self):
        run_cli("new", "Todo List", "--target", "linux", "--out", str(self.root), "--yes")
        code, _out, err = run_cli(
            "new", "Todo List", "--target", "linux", "--out", str(self.root), "--yes"
        )
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("--force", err)


class RecipeCommandTests(unittest.TestCase):
    """build/run/clean/test read the recipe written into project.json."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        code, _out, err = run_cli(
            "new", "Todo List", "--target", "linux", "--lang", "c",
            "--out", str(self.root), "--yes",
        )
        self.assertEqual(code, EXIT_OK, err)
        self.project = self.root / "todo-list"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_unknown_recipe_is_reported(self):
        code, _out, err = run_cli("deploy", str(self.project), "--no-native")
        # 'deploy' is not a registered subcommand at all
        self.assertEqual(code, EXIT_USAGE)

    def test_recipe_is_read_from_the_project(self):
        from makemaker.cli import _project_commands

        steps = _project_commands(self.project, "build")
        self.assertEqual(steps, [["make"]])

    def test_malformed_recipe_is_reported(self):
        from makemaker.cli import _project_commands
        from makemaker.engine import TemplateError

        metadata_path = self.project / ".makemaker" / "project.json"
        payload = json.loads(metadata_path.read_text())
        payload["commands"]["build"] = ["make"]  # flat, not a list of commands
        metadata_path.write_text(json.dumps(payload))
        with self.assertRaises(TemplateError):
            _project_commands(self.project, "build")

    def test_foreign_directory_is_reported(self):
        from makemaker.cli import _project_commands
        from makemaker.engine import TemplateError

        with self.assertRaises(TemplateError):
            _project_commands(self.root, "build")


if __name__ == "__main__":
    unittest.main()
