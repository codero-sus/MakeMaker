"""Tests for build recipes.

The recipe shape is a contract between three consumers: the Python fallback
executor, the C companion (mknative), and the generated project.json. A flat
list instead of a list-of-commands is exactly the kind of mistake that fails
silently, so it is checked here for every template.
"""

from __future__ import annotations

import unittest

from makemaker.recipes import build_commands
from makemaker.registry import load_registry, resolve_variable_defaults
from makemaker.variables import build_variables


class RecipeShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry()

    def every_recipe(self):
        for template in self.registry.sorted():
            for flavor in template.flavors or [""]:
                for language in template.languages_for(flavor) or [""]:
                    context = build_variables(
                        "App Test", target=template.os, flavor=flavor, language=language
                    )
                    context.update(resolve_variable_defaults(template, context))
                    yield template, flavor, language, build_commands(template, context)

    def test_every_recipe_is_a_list_of_argv_lists(self):
        checked = 0
        for template, flavor, language, commands in self.every_recipe():
            where = f"{template.id}/{flavor}/{language}"
            self.assertIsInstance(commands, dict, where)
            self.assertTrue(commands, f"{where} has no recipes")
            for action, recipe in commands.items():
                self.assertIsInstance(recipe, list, f"{where}:{action}")
                self.assertTrue(recipe, f"{where}:{action} is empty")
                for command in recipe:
                    self.assertIsInstance(
                        command, list,
                        f"{where}:{action} -> {command!r} is not a list of argv strings",
                    )
                    self.assertTrue(command, f"{where}:{action} has an empty command")
                    for part in command:
                        self.assertIsInstance(
                            part, str,
                            f"{where}:{action} -> {command!r} contains a non-string",
                        )
                    checked += 1
        self.assertGreater(checked, 40, "suspiciously few recipes checked")

    def test_every_template_can_build(self):
        for _template, _flavor, _language, commands in self.every_recipe():
            self.assertIn("build", commands)

    def test_native_targets_use_make(self):
        for target in ("linux", "macos"):
            template = self.registry.get(target)
            context = build_variables(
                "App Test", target=target, flavor="console", language="cpp"
            )
            commands = build_commands(template, context)
            self.assertEqual(commands["build"], [["make"]])
            self.assertEqual(commands["run"], [["make", "run"]])
            self.assertEqual(commands["test"], [["make", "test"]])

    def test_console_targets_have_tests_gui_targets_may_not(self):
        template = self.registry.get("linux")
        context = build_variables("App", target="linux", flavor="console", language="c")
        self.assertIn("test", build_commands(template, context))
        gui = build_variables("App", target="linux", flavor="gui", language="c")
        self.assertNotIn("test", build_commands(template, gui))

    def test_mobile_targets_use_their_own_tools(self):
        android = build_commands(
            self.registry.get("android"),
            build_variables("App", target="android", flavor="gui", language="kotlin"),
        )
        self.assertEqual(android["build"], [["./gradlew", "assembleDebug"]])

        ios = build_commands(
            self.registry.get("ios"),
            build_variables("App", target="ios", flavor="gui", language="swift"),
        )
        self.assertEqual(ios["build"][0][0], "xcodebuild")
        self.assertIn("-scheme", ios["build"][0])

    def test_interpreted_targets_use_their_own_runners(self):
        python = build_commands(
            self.registry.get("python"),
            build_variables("App", target="python", flavor="console", language="python"),
        )
        self.assertEqual(python["test"], [["make", "test"]])
        self.assertEqual(python["run"], [["make", "run"]])

        node = build_commands(
            self.registry.get("node"),
            build_variables("App", target="node", flavor="console", language="typescript"),
        )
        self.assertEqual(node["test"], [["node", "--test"]])
        self.assertEqual(node["run"], [["node", "src/index.ts"]])

        js = build_commands(
            self.registry.get("node"),
            build_variables("App", target="node", flavor="console", language="javascript"),
        )
        self.assertEqual(js["run"], [["node", "src/index.js"]])

    def test_compiled_targets_use_their_toolchains(self):
        rust = build_commands(
            self.registry.get("rust"),
            build_variables("App", target="rust", flavor="console", language="rust"),
        )
        self.assertEqual(rust["build"], [["cargo", "build"]])
        self.assertEqual(rust["test"], [["cargo", "test"]])

        go = build_commands(
            self.registry.get("go"),
            build_variables("App", target="go", flavor="console", language="go"),
        )
        self.assertEqual(go["test"], [["go", "test", "./..."]])
        self.assertEqual(go["build"][0][:3], ["go", "build", "-o"])

        dotnet = build_commands(
            self.registry.get("dotnet"),
            build_variables("App", target="dotnet", flavor="console", language="csharp"),
        )
        self.assertEqual(dotnet["build"], [["dotnet", "build", "App.sln", "-c", "Release"]])
        self.assertEqual(dotnet["test"], [["dotnet", "test", "App.sln", "-c", "Release"]])

    def test_windows_build_configures_then_builds(self):
        windows = build_commands(
            self.registry.get("windows"),
            build_variables("App", target="windows", flavor="console", language="cpp"),
        )
        self.assertEqual(len(windows["build"]), 2)
        self.assertEqual(windows["build"][0][:2], ["cmake", "-S"])
        self.assertEqual(windows["build"][1][:3], ["cmake", "--build", "build"])


if __name__ == "__main__":
    unittest.main()
