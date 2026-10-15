"""Tests for the static template validator.

A linter that reports nothing proves nothing, so every check gets a test with
a deliberately broken template that must be caught, alongside the check that
the shipped templates are clean.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from makemaker.lint import Issue, manifest_summary, validate_registry, validate_template
from makemaker.registry import load_registry

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "makemaker" / "templates"


def mutate(source_id: str, changes):
    """Copy a bundled template into a temp dir, break it, validate it.

    ``changes`` is a callable taking the copied template directory.
    """
    tmp = Path(tempfile.mkdtemp())
    target = tmp / source_id
    shutil.copytree(TEMPLATES_DIR / source_id, target)
    changes(target)
    from makemaker.registry import _load_one

    return validate_template(_load_one(target))


def manifest_of(directory: Path) -> dict:
    return json.loads((directory / "template.json").read_text(encoding="utf-8"))


def write_manifest(directory: Path, manifest: dict) -> None:
    (directory / "template.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


class BundledTemplatesTest(unittest.TestCase):
    def test_all_bundled_templates_are_clean(self):
        issues = validate_registry()
        self.assertEqual([i.format() for i in issues], [])

    def test_every_template_id_is_covered(self):
        registry = load_registry()
        summary = [manifest_summary(t) for t in registry.sorted()]
        self.assertEqual(len(summary), len(registry.ids()))
        for entry in summary:
            self.assertGreater(entry["files"], 0, entry["id"])
            self.assertGreater(len(entry["languages"]), 0, entry["id"])

    def test_manifest_summary_shape(self):
        registry = load_registry()
        entry = manifest_summary(registry.get("linux"))
        self.assertEqual(entry["id"], "linux")
        self.assertEqual(sorted(entry), sorted(
            ["id", "flavors", "languages", "files", "rules", "variables"]
        ))


class CatchesBugsTest(unittest.TestCase):
    """Each check must fire on the mistake it exists to catch."""

    def test_catches_template_syntax_in_a_verbatim_file(self):
        # The real bug: go/.gitignore shipped a literal "{{ bin_name }}".
        def break_it(d):
            (d / ".gitignore").write_text("/build/\n{{ bin_name }}\n", encoding="utf-8")

        issues = mutate("linux", break_it)
        self.assertTrue(
            any("copied verbatim" in i.message for i in issues),
            [i.message for i in issues],
        )

    def test_does_not_flag_gitignore_that_is_a_template(self):
        def fix_it(d):
            (d / ".gitignore.tmpl").write_text("/build/\n{{ bin_name }}\n", encoding="utf-8")

        issues = mutate("linux", fix_it)
        self.assertEqual([i.format() for i in issues], [])

    def test_catches_unknown_manifest_key(self):
        def typo(d):
            manifest = manifest_of(d)
            manifest["langauges"] = ["c"]  # transposed
            write_manifest(d, manifest)

        issues = mutate("linux", typo)
        self.assertTrue(
            any("unknown key 'langauges'" in i.message for i in issues),
            [i.message for i in issues],
        )

    def test_catches_unknown_rule_key(self):
        def typo(d):
            manifest = manifest_of(d)
            manifest.setdefault("rules", []).append(
                {"path": "src/**", "executable": True, "exectuable": True}
            )
            write_manifest(d, manifest)

        issues = mutate("linux", typo)
        self.assertTrue(
            any("unknown key 'exectuable'" in i.message for i in issues),
            [i.message for i in issues],
        )

    def test_catches_rule_that_matches_nothing(self):
        def dead_rule(d):
            manifest = manifest_of(d)
            manifest.setdefault("rules", []).append(
                {"path": "src/does-not-exist.*", "executable": True}
            )
            write_manifest(d, manifest)

        issues = mutate("linux", dead_rule)
        self.assertTrue(
            any("matches no template file" in i.message for i in issues),
            [i.message for i in issues],
        )

    def test_catches_two_files_mapping_onto_one_output(self):
        def collide(d):
            # linux already has src/main.{{ source_ext }}.tmpl, which renders
            # to src/main.cpp -- so this literal name collides with it.
            (d / "src" / "main.cpp.tmpl").write_text("/* collision */\n", encoding="utf-8")

        issues = mutate("linux", collide)
        self.assertTrue(
            any("two template files map onto" in i.message for i in issues),
            [i.message for i in issues],
        )

    def test_catches_template_that_does_not_parse(self):
        def broken(d):
            (d / "src" / "main.{{ source_ext }}.tmpl").write_text(
                "{% if x %}never closed\n", encoding="utf-8"
            )

        issues = mutate("linux", broken)
        self.assertTrue(
            any("does not parse" in i.message for i in issues),
            [i.message for i in issues],
        )

    def test_catches_undefined_variable(self):
        def broken(d):
            (d / "src" / "main.{{ source_ext }}.tmpl").write_text(
                "int main(){ return {{ not_a_real_variable }}; }\n", encoding="utf-8"
            )

        issues = mutate("linux", broken)
        self.assertTrue(
            any("undefined variable" in i.message for i in issues),
            [i.message for i in issues],
        )

    def test_catches_unresolvable_output_path(self):
        def broken(d):
            (d / "{{ nope }}.txt.tmpl").write_text("x\n", encoding="utf-8")

        issues = mutate("linux", broken)
        self.assertTrue(
            any("cannot render path" in i.message for i in issues),
            [i.message for i in issues],
        )

    def test_catches_rule_condition_that_fails_at_render(self):
        # Syntax errors are already rejected by the registry at load time, so
        # this uses a condition that parses but blows up when evaluated.
        def broken(d):
            manifest = manifest_of(d)
            # Must be first: rule_for() is first-match-wins, and the bundled
            # "src/main.*" rule would otherwise shadow this one.
            manifest["rules"] = [
                {"path": "src/main.{{ source_ext }}.tmpl",
                 "when": "no_such_variable == 'gui'"}
            ] + manifest.get("rules", [])
            write_manifest(d, manifest)

        issues = mutate("linux", broken)
        self.assertTrue(
            any("rule condition failed" in i.message for i in issues),
            [i.message for i in issues],
        )

    def test_catches_rule_shadowed_by_an_earlier_rule(self):
        # rule_for() is first-match-wins, so a rule after "src/main.*" that
        # only matches main.* files can never fire.
        def shadowed(d):
            manifest = manifest_of(d)
            manifest.setdefault("rules", []).append(
                {"path": "src/main.{{ source_ext }}.tmpl", "executable": True}
            )
            write_manifest(d, manifest)

        issues = mutate("linux", shadowed)
        self.assertTrue(
            any("shadowed" in i.message for i in issues),
            [i.message for i in issues],
        )

    def test_rule_covering_previously_unmatched_files_is_not_shadowed(self):
        # No bundled linux rule matches Makefile.tmpl, so a rule for it is
        # genuinely new rather than dead.
        def narrowed(d):
            manifest = manifest_of(d)
            manifest.setdefault("rules", []).append(
                {"path": "Makefile.tmpl", "executable": True}
            )
            write_manifest(d, manifest)

        issues = mutate("linux", narrowed)
        self.assertEqual(
            [i.format() for i in issues if "shadowed" in i.message], [],
            [i.format() for i in issues],
        )

    def test_registry_rejects_malformed_rule_condition_before_lint_runs(self):
        from makemaker.engine import TemplateError
        from makemaker.registry import _load_one

        tmp = Path(tempfile.mkdtemp()) / "linux"
        shutil.copytree(TEMPLATES_DIR / "linux", tmp)
        manifest = manifest_of(tmp)
        manifest.setdefault("rules", []).append(
            {"path": "src/**", "when": "flavor ===  'gui'"}
        )
        write_manifest(tmp, manifest)
        with self.assertRaises(TemplateError) as ctx:
            _load_one(tmp)
        self.assertIn("bad rule condition", str(ctx.exception))


class ReportingTest(unittest.TestCase):
    def test_issue_formats_with_and_without_a_path(self):
        self.assertEqual(Issue("linux", "boom", "src/x").format(), "linux/src/x: boom")
        self.assertEqual(Issue("linux", "boom").format(), "linux: boom")

    def test_one_broken_file_is_reported_once_per_problem(self):
        # linux renders src/main.{{ source_ext }}.tmpl in every combination;
        # the same complaint must not be repeated per flavour/language.
        def broken(d):
            (d / "src" / "main.{{ source_ext }}.tmpl").write_text(
                "{% if x %}never closed\n", encoding="utf-8"
            )

        issues = mutate("linux", broken)
        parse_issues = [i for i in issues if "does not parse" in i.message]
        self.assertEqual(len(parse_issues), 1, [i.format() for i in issues])

    def test_distinct_problems_in_one_file_are_all_reported(self):
        def broken(d):
            (d / "src" / "main.cpp.tmpl").write_text("{% if x %}nope\n", encoding="utf-8")

        issues = mutate("linux", broken)
        kinds = {i.message.split(":")[0] for i in issues}
        self.assertIn("does not parse", kinds)
        self.assertIn("render failed", kinds)


if __name__ == "__main__":
    unittest.main()
