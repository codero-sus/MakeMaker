"""Tests for variable derivation."""

from __future__ import annotations

import datetime as dt
import unittest

from makemaker.variables import (
    ValidationError,
    as_identifier,
    build_variables,
    java_package,
    java_segment,
    validate_name,
)

FIXED_NOW = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)


class NameValidationTests(unittest.TestCase):
    def test_accepts_reasonable_names(self):
        for name in ("Todo List", "data-pipe", "App2", "my_app", "C++ Tools"):
            self.assertEqual(validate_name(name), name)

    def test_trims_whitespace(self):
        self.assertEqual(validate_name("  Padded  "), "Padded")

    def test_rejects_empty_and_junk(self):
        for name in ("", "   ", "-leading", "_leading", "a" * 65):
            with self.assertRaises(ValidationError, msg=repr(name)):
                validate_name(name)


class IdentifierTests(unittest.TestCase):
    def test_case_derivations(self):
        values = build_variables("Todo List", target="linux", now=FIXED_NOW)
        self.assertEqual(values["slug"], "todo-list")
        self.assertEqual(values["identifier"], "todo_list")
        self.assertEqual(values["class_name"], "TodoList")
        self.assertEqual(values["camel_name"], "todoList")
        self.assertEqual(values["screaming_name"], "TODO_LIST")

    def test_derived_names_are_valid_identifiers(self):
        """No language here allows an identifier that starts with a digit."""
        for name in ("2Fast", "3d viewer", "42"):
            values = build_variables(name, target="ios", now=FIXED_NOW)
            for key in ("identifier", "class_name", "camel_name", "screaming_name"):
                derived = values[key]
                self.assertFalse(derived[0].isdigit(), f"{name}: {key}={derived}")
                self.assertTrue(derived.replace("_", "").isalnum(), f"{name}: {key}={derived}")

    def test_as_identifier_only_fixes_leading_digits(self):
        self.assertEqual(as_identifier("2Fast"), "App2Fast")
        self.assertEqual(as_identifier("TodoList"), "TodoList")


class PackageTests(unittest.TestCase):
    def test_application_id_is_one_segment_per_name(self):
        values = build_variables("Todo List", target="android", now=FIXED_NOW)
        self.assertEqual(values["application_id"], "com.example.todolist")
        self.assertEqual(values["package_path"], "com/example/todolist")

    def test_domain_is_reversed(self):
        values = build_variables(
            "Widget", target="android", domain="example.co.uk", now=FIXED_NOW
        )
        self.assertEqual(values["application_id"], "uk.co.example.widget")

    def test_java_segment_sanitises(self):
        self.assertEqual(java_segment("Todo List"), "todolist")
        self.assertEqual(java_segment("2Fast"), "a2fast")
        self.assertEqual(java_segment("class"), "class_")
        self.assertEqual(java_segment("!!!"), "app")

    def test_java_package_rejects_keywords_and_digits(self):
        self.assertEqual(java_package("class"), "class_")
        self.assertEqual(java_package("2cool"), "a2cool")

    def test_invalid_domain_is_rejected(self):
        with self.assertRaises(ValidationError):
            build_variables("App", target="ios", domain="not a domain", now=FIXED_NOW)


class DeterminismTests(unittest.TestCase):
    def test_same_input_gives_same_output(self):
        first = build_variables("Todo List", target="ios", now=FIXED_NOW)
        second = build_variables("Todo List", target="ios", now=FIXED_NOW)
        self.assertEqual(first, second)

    def test_guids_are_stable_and_distinct(self):
        values = build_variables("Todo List", target="windows", now=FIXED_NOW)
        self.assertEqual(len(values["project_guid"]), 36)
        self.assertNotEqual(values["project_guid"], values["upgrade_guid"])

    def test_date_values(self):
        values = build_variables("App", target="linux", now=FIXED_NOW)
        self.assertEqual(values["year"], "2026")
        self.assertEqual(values["date"], "2026-01-02")
        self.assertEqual(values["copyright_year"], "2026")

    def test_extra_values_win(self):
        values = build_variables(
            "App", target="linux", extra={"version": "9.9.9"}, now=FIXED_NOW
        )
        self.assertEqual(values["version"], "9.9.9")

    def test_platform_flags(self):
        values = build_variables("App", target="linux", flavor="gui", language="c", now=FIXED_NOW)
        self.assertTrue(values["is_gui"])
        self.assertFalse(values["is_console"])
        self.assertTrue(values["is_c"])
        self.assertFalse(values["is_cpp"])
        self.assertEqual(values["source_ext"], "c")

    def test_source_extensions(self):
        expected = {"c": "c", "cpp": "cpp", "objc": "m", "swift": "swift", "kotlin": "kt"}
        for language, extension in expected.items():
            values = build_variables("App", target="linux", language=language, now=FIXED_NOW)
            self.assertEqual(values["source_ext"], extension, language)


if __name__ == "__main__":
    unittest.main()
