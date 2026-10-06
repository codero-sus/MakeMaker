"""Structural checks for the generated Xcode project.

Xcode is not available in CI, so instead of building we verify the invariants
that make a .pbxproj loadable: every referenced object id is defined, the
root object exists, delimiters balance, and the shared scheme points at real
targets.
"""

from __future__ import annotations

import re
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from makemaker.generator import generate
from makemaker.registry import load_registry, resolve_variable_defaults
from makemaker.variables import build_variables

OBJECT_ID = re.compile(r"\b[0-9A-F]{24}\b")
DEFINITION = re.compile(r"^\t\t([0-9A-F]{24}) ", re.M)
ROOT_OBJECT = re.compile(r"rootObject = ([0-9A-F]{24})")


class XcodeProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry = load_registry()
        template = registry.get("ios")
        context = build_variables("App Test", target="ios", flavor="gui", language="swift")
        context.update(resolve_variable_defaults(template, context))
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tempdir.name) / "AppTest"
        cls.result = generate(template, context, cls.root)
        cls.project = cls.root / "AppTest.xcodeproj"
        cls.pbxproj = (cls.project / "project.pbxproj").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_project_layout(self):
        self.assertTrue((self.project / "project.pbxproj").is_file())
        self.assertTrue(
            (self.project / "project.xcworkspace" / "contents.xcworkspacedata").is_file()
        )
        self.assertTrue(
            (self.project / "xcshareddata" / "xcschemes" / "AppTest.xcscheme").is_file()
        )

    def test_object_ids_are_24_hex_characters(self):
        for identifier in set(OBJECT_ID.findall(self.pbxproj)):
            self.assertRegex(identifier, r"^[0-9A-F]{24}$")

    def test_every_referenced_id_is_defined(self):
        defined = set(DEFINITION.findall(self.pbxproj))
        referenced = set(OBJECT_ID.findall(self.pbxproj))
        self.assertTrue(defined, "no objects were defined")
        self.assertEqual(
            referenced - defined, set(), "pbxproj references undefined object ids"
        )

    def test_no_object_is_defined_twice(self):
        ids = DEFINITION.findall(self.pbxproj)
        self.assertEqual(len(ids), len(set(ids)), "duplicate object definitions")

    def test_root_object_exists(self):
        match = ROOT_OBJECT.search(self.pbxproj)
        self.assertIsNotNone(match, "no rootObject")
        self.assertIn(match.group(1), set(DEFINITION.findall(self.pbxproj)))

    def test_delimiters_balance(self):
        self.assertEqual(self.pbxproj.count("{"), self.pbxproj.count("}"))
        self.assertEqual(self.pbxproj.count("("), self.pbxproj.count(")"))

    def test_required_sections_are_present(self):
        for section in (
            "PBXBuildFile", "PBXFileReference", "PBXGroup", "PBXNativeTarget",
            "PBXProject", "PBXSourcesBuildPhase", "PBXResourcesBuildPhase",
            "PBXFrameworksBuildPhase", "PBXTargetDependency",
            "XCBuildConfiguration", "XCConfigurationList",
        ):
            self.assertIn(f"/* Begin {section} section */", self.pbxproj, section)
            self.assertIn(f"/* End {section} section */", self.pbxproj, section)

    def test_app_and_test_targets_exist_with_the_right_product_types(self):
        self.assertIn('productType = "com.apple.product-type.application";', self.pbxproj)
        self.assertIn(
            'productType = "com.apple.product-type.bundle.unit-test";', self.pbxproj
        )

    def test_build_settings_reference_real_paths(self):
        self.assertIn('INFOPLIST_FILE = "AppTest/Info.plist";', self.pbxproj)
        self.assertIn("PRODUCT_BUNDLE_IDENTIFIER = com.example.apptest;", self.pbxproj)
        self.assertTrue((self.root / "AppTest" / "Info.plist").is_file())

    def test_every_source_file_in_the_project_exists_on_disk(self):
        for name in ("AppTestApp.swift", "ContentView.swift", "Greeter.swift"):
            self.assertIn(f"path = {name};", self.pbxproj.replace(f'"{name}"', name))
            self.assertTrue((self.root / "AppTest" / name).is_file(), name)

    def test_scheme_is_valid_xml_and_points_at_defined_targets(self):
        scheme_path = self.project / "xcshareddata" / "xcschemes" / "AppTest.xcscheme"
        tree = ElementTree.parse(scheme_path)
        defined = set(DEFINITION.findall(self.pbxproj))
        references = [
            element.get("BlueprintIdentifier")
            for element in tree.iter("BuildableReference")
        ]
        self.assertTrue(references, "the scheme references no targets")
        for reference in references:
            self.assertIn(reference, defined, f"scheme points at unknown target {reference}")
            self.assertRegex(reference, r"^[0-9A-F]{24}$")

    def test_scheme_names_match_the_generated_files(self):
        scheme = (
            self.project / "xcshareddata" / "xcschemes" / "AppTest.xcscheme"
        ).read_text(encoding="utf-8")
        self.assertIn('BuildableName = "AppTest.app"', scheme)
        self.assertIn('BuildableName = "AppTestTests.xctest"', scheme)
        self.assertIn('ReferencedContainer = "container:AppTest.xcodeproj"', scheme)

    def test_swift_sources_declare_the_expected_symbols(self):
        app_swift = (self.root / "AppTest" / "AppTestApp.swift").read_text()
        self.assertIn("@main", app_swift)
        self.assertIn("struct AppTestApp: App", app_swift)
        greeter = (self.root / "AppTest" / "Greeter.swift").read_text()
        self.assertIn("static func sum", greeter)
        tests = (self.root / "AppTestTests" / "AppTestTests.swift").read_text()
        self.assertIn("@testable import AppTest", tests)


if __name__ == "__main__":
    unittest.main()
