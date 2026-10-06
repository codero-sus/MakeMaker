"""Tests for project generation."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from makemaker.engine import TemplateError
from makemaker.generator import generate, plan_files, read_project_metadata
from makemaker.registry import load_registry, resolve_variable_defaults
from makemaker.variables import build_variables

UNRESOLVED_MARKERS = ("{{", "{%", "{#")

# What each template must produce for its default configuration.
EXPECTED_FILES = {
    ("linux", "console", "c"): {
        "Makefile", "CMakeLists.txt", "README.md", ".gitignore",
        "src/app.h", "src/app.c", "src/main.c", "tests/test_app.c",
    },
    ("linux", "console", "cpp"): {
        "src/app.cpp", "src/main.cpp", "tests/test_app.cpp",
    },
    ("linux", "gui", "cpp"): {"src/gui.cpp"},
    ("macos", "console", "cpp"): {"src/main.cpp", "CMakeLists.txt"},
    ("macos", "gui", "objc"): {
        "src/gui.m", "src/app.m", "Info.plist", "Makefile",
    },
    ("windows", "console", "cpp"): {"src/main.cpp", "tests/test_app.cpp"},
    ("windows", "gui", "cpp"): {
        "src/winmain.cpp", "src/app.rc", "src/resource.h", "src/AppTest.manifest",
    },
    ("android", "gui", "kotlin"): {
        "settings.gradle.kts", "build.gradle.kts", "gradle.properties",
        "gradlew", "gradlew.bat", "app/build.gradle.kts",
        "app/src/main/AndroidManifest.xml",
        "app/src/main/java/com/example/apptest/MainActivity.kt",
        "app/src/main/java/com/example/apptest/Greeter.kt",
        "app/src/main/res/values/strings.xml",
        "app/src/main/res/values/themes.xml",
        "app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml",
        "app/src/test/java/com/example/apptest/GreeterTest.kt",
    },
    ("ios", "gui", "swift"): {
        "Makefile", "AppTest/AppTestApp.swift", "AppTest/ContentView.swift",
        "AppTest/Greeter.swift", "AppTest/Info.plist",
        "AppTest/Assets.xcassets/Contents.json",
        "AppTestTests/AppTestTests.swift",
        "AppTest.xcodeproj/project.pbxproj",
        "AppTest.xcodeproj/project.xcworkspace/contents.xcworkspacedata",
        "AppTest.xcodeproj/xcshareddata/xcschemes/AppTest.xcscheme",
    },
}


def make_context(template, flavor, language, name="App Test"):
    context = build_variables(name, target=template.os, flavor=flavor, language=language)
    context.update(resolve_variable_defaults(template, context))
    return context


class GenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry()
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tempdir.name)

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def generate(self, target, flavor, language, name="App Test", **kwargs):
        template = self.registry.get(target)
        context = make_context(template, flavor, language, name)
        destination = self.root / f"{target}-{flavor}-{language}-{kwargs.pop('suffix', 'x')}"
        return generate(template, context, destination, **kwargs), context, destination

    # -- planning -------------------------------------------------------
    def test_expected_files_are_produced(self):
        for (target, flavor, language), expected in EXPECTED_FILES.items():
            result, _context, destination = self.generate(
                target, flavor, language, suffix="plan"
            )
            produced = {item.rel_path for item in plan_files(
                self.registry.get(target),
                make_context(self.registry.get(target), flavor, language),
            )}
            for path in expected:
                self.assertIn(path, produced, f"{target}/{flavor}/{language}: missing {path}")
            self.assertTrue(result.file_count > 0)
            self.assertTrue(destination.is_dir())

    def test_gui_and_console_sources_are_mutually_exclusive(self):
        console, _c, console_dir = self.generate("linux", "console", "c", suffix="excl1")
        gui, _g, gui_dir = self.generate("linux", "gui", "c", suffix="excl2")
        console_files = set(console.created)
        gui_files = set(gui.created)
        self.assertIn("src/main.c", console_files)
        self.assertNotIn("src/gui.c", console_files)
        self.assertIn("src/gui.c", gui_files)
        self.assertNotIn("src/main.c", gui_files)
        self.assertTrue((console_dir / "tests").is_dir())
        self.assertFalse((gui_dir / "tests").exists())

    # -- rendering ------------------------------------------------------
    def test_no_template_syntax_survives_into_generated_files(self):
        for template in self.registry.sorted():
            flavors = template.flavors or [""]
            for flavor in flavors:
                for language in template.languages_for(flavor) or [""]:
                    context = make_context(template, flavor, language)
                    result = generate(
                        template,
                        context,
                        self.root / f"scan-{template.id}-{flavor}-{language}",
                    )
                    for rel in result.created:
                        path = result.destination / rel
                        if not path.is_file():
                            continue
                        try:
                            text = path.read_text(encoding="utf-8")
                        except UnicodeDecodeError:
                            continue
                        for marker in UNRESOLVED_MARKERS:
                            self.assertNotIn(
                                marker, text,
                                f"{template.id}/{flavor}/{language}: {rel} still contains {marker}",
                            )

    def test_project_metadata_is_written_and_readable(self):
        result, context, destination = self.generate(
            "linux", "console", "c", suffix="meta",
            commands={"build": [["make"]]},
        )
        metadata = read_project_metadata(destination)
        self.assertEqual(metadata["template"], "linux")
        self.assertEqual(metadata["flavor"], "console")
        self.assertEqual(metadata["language"], "c")
        self.assertEqual(metadata["project"]["name"], "App Test")
        self.assertEqual(metadata["generator"]["name"], "makemaker")
        self.assertEqual(metadata["commands"]["build"], [["make"]])
        self.assertIn("slug", metadata["variables"])
        self.assertEqual(result.destination, destination)

    def test_reading_metadata_from_a_foreign_directory_fails_clearly(self):
        with self.assertRaises(TemplateError):
            read_project_metadata(self.root)

    # -- substitution spot checks --------------------------------------
    def test_android_package_paths_match_the_application_id(self):
        result, context, destination = self.generate(
            "android", "gui", "kotlin", suffix="pkg"
        )
        expected_dir = context["package_path"]
        self.assertTrue(
            (destination / "app/src/main/java" / expected_dir / "MainActivity.kt").is_file()
        )
        manifest = (destination / "app/src/main/AndroidManifest.xml").read_text()
        self.assertIn('android:theme="@style/Theme.AppTest"', manifest)
        gradle = (destination / "app/build.gradle.kts").read_text()
        self.assertIn(f'applicationId = "{context["application_id"]}"', gradle)

    def test_makefile_recipes_use_real_tabs(self):
        result, _context, destination = self.generate(
            "linux", "console", "cpp", suffix="tabs"
        )
        makefile = (destination / "Makefile").read_text()
        recipe_lines = [line for line in makefile.splitlines() if line.startswith("\t")]
        self.assertTrue(recipe_lines, "the generated Makefile has no tab-indented recipes")

    def test_gradlew_is_executable(self):
        result, _context, destination = self.generate(
            "android", "gui", "kotlin", suffix="exec"
        )
        gradlew = destination / "gradlew"
        self.assertTrue(os.access(gradlew, os.X_OK), "gradlew is not executable")
        self.assertTrue(gradlew.stat().st_mode & stat.S_IXUSR)

    def test_paths_cannot_escape_the_destination(self):
        """A template that renders '..' into a path must be rejected."""
        from makemaker.generator import _safe_join

        with self.assertRaises(TemplateError):
            _safe_join("../escape.txt", "linux")
        with self.assertRaises(TemplateError):
            _safe_join("/etc/passwd", "linux")

    # -- safety ---------------------------------------------------------
    def test_refuses_to_overwrite_without_force(self):
        template = self.registry.get("linux")
        context = make_context(template, "console", "c")
        destination = self.root / "overwrite-guard"
        generate(template, context, destination)
        with self.assertRaises(FileExistsError):
            generate(template, context, destination)
        # ...and succeeds with force
        result = generate(template, context, destination, force=True)
        self.assertTrue(result.overwritten)

    def test_dry_run_writes_nothing(self):
        template = self.registry.get("linux")
        context = make_context(template, "console", "c")
        destination = self.root / "dry-run"
        result = generate(template, context, destination, dry_run=True)
        self.assertTrue(result.created)
        self.assertFalse(destination.exists())

    def test_generation_is_reproducible(self):
        template = self.registry.get("ios")
        context = make_context(template, "gui", "swift")
        first = self.root / "repro-1"
        second = self.root / "repro-2"
        generate(template, context, first)
        generate(template, context, second)
        left = {
            path.relative_to(first).as_posix(): path.read_bytes()
            for path in sorted(first.rglob("*")) if path.is_file()
        }
        right = {
            path.relative_to(second).as_posix(): path.read_bytes()
            for path in sorted(second.rglob("*")) if path.is_file()
        }
        self.assertEqual(set(left), set(right))
        for name, content in left.items():
            if name == ".makemaker/project.json":
                continue  # contains a timestamp
            self.assertEqual(content, right[name], name)


if __name__ == "__main__":
    unittest.main()
