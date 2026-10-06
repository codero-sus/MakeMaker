"""Tests for template discovery and metadata."""

from __future__ import annotations

import unittest

from makemaker.engine import TemplateError
from makemaker.registry import load_registry, rule_matches, template_root

EXPECTED_TARGETS = {"android", "ios", "linux", "macos", "windows"}


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry()

    def test_all_five_platforms_are_bundled(self):
        self.assertEqual(set(self.registry.ids()), EXPECTED_TARGETS)

    def test_template_root_exists(self):
        self.assertTrue(template_root().is_dir())

    def test_every_template_has_metadata(self):
        for template in self.registry.sorted():
            self.assertTrue(template.display_name, template.id)
            self.assertTrue(template.summary, template.id)
            self.assertTrue(template.os, template.id)
            self.assertTrue(template.files(), f"{template.id} has no files")

    def test_default_flavor_and_language_are_valid(self):
        for template in self.registry.sorted():
            if template.flavors:
                self.assertIn(template.default_flavor, template.flavors, template.id)
            if template.languages:
                self.assertIn(template.default_language, template.languages, template.id)

    def test_flavor_languages_are_a_subset(self):
        for template in self.registry.sorted():
            for flavor, languages in template.flavor_languages.items():
                self.assertIn(flavor, template.flavors, template.id)
                for language in languages:
                    self.assertIn(language, template.languages, template.id)

    def test_unknown_target_is_reported(self):
        with self.assertRaises(TemplateError) as caught:
            self.registry.get("symbian")
        self.assertIn("available", str(caught.exception))

    def test_unknown_flavor_is_reported(self):
        template = self.registry.get("linux")
        with self.assertRaises(TemplateError):
            template.flavor("server")

    def test_unknown_language_is_reported(self):
        template = self.registry.get("linux")
        with self.assertRaises(TemplateError):
            template.language("rust", "console")

    def test_macos_gui_only_accepts_objective_c(self):
        template = self.registry.get("macos")
        self.assertEqual(template.language(None, "gui"), "objc")
        self.assertEqual(template.language(None, "console"), "cpp")
        with self.assertRaises(TemplateError):
            template.language("cpp", "gui")

    def test_windows_gui_only_accepts_cpp(self):
        template = self.registry.get("windows")
        self.assertEqual(template.language(None, "gui"), "cpp")
        with self.assertRaises(TemplateError):
            template.language("c", "gui")

    def test_mobile_templates_default_to_gui(self):
        for target in ("android", "ios"):
            template = self.registry.get(target)
            self.assertEqual(template.default_flavor, "gui", target)
            self.assertFalse(template.supports_flavors, target)

    def test_requirements_are_declared(self):
        self.assertIn("make", self.registry.get("linux").requirements("console"))
        self.assertIn("xcodebuild", self.registry.get("ios").requirements("gui"))


class RuleMatchingTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(rule_matches("README.md.tmpl", "README.md.tmpl"))
        self.assertFalse(rule_matches("README.md.tmpl", "README.md"))

    def test_glob_match(self):
        self.assertTrue(rule_matches("src/main.*", "src/main.cpp.tmpl"))
        self.assertFalse(rule_matches("src/main.*", "src/gui.cpp.tmpl"))

    def test_directory_prefix_match(self):
        self.assertTrue(rule_matches("tests/**", "tests/test_app.c.tmpl"))
        self.assertTrue(rule_matches("tests/**", "tests/nested/deep.c.tmpl"))
        self.assertFalse(rule_matches("tests/**", "src/test_app.c.tmpl"))


if __name__ == "__main__":
    unittest.main()
