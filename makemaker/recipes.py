"""Build/run recipes for each generated project type.

A recipe is a **list of commands**, and each command is a **list of argv
strings**::

    {"build": [["cmake", "-S", ".", "-B", "build"],
               ["cmake", "--build", "build", "--config", "Release"]]}

That shape is written into ``.makemaker/project.json`` so the C companion
(``mknative``) can drive a build without knowing anything about templates, and
so ``makemaker build`` has an equivalent Python fallback when the companion has
not been compiled.  Both executors consume exactly this shape.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .registry import TemplateDef

__all__ = ["build_commands", "Recipe", "Command"]

Command = List[str]
Recipe = List[Command]


def build_commands(template: TemplateDef, context: Dict[str, Any]) -> Dict[str, Recipe]:
    """Return ``{"build": [[argv...], ...], ...}`` for this project."""
    target = template.os
    flavor = str(context.get("flavor", ""))
    class_name = str(context.get("class_name", ""))
    bin_name = str(context.get("bin_name", "app"))

    if target in ("linux", "macos"):
        commands: Dict[str, Recipe] = {
            "build": [["make"]],
            "clean": [["make", "clean"]],
            "run": [["make", "run"]],
        }
        if flavor != "gui":
            commands["test"] = [["make", "test"]]
        if target == "macos":
            commands["install"] = [["make", "install"]]
        return commands

    if target == "windows":
        return {
            "configure": [["cmake", "-S", ".", "-B", "build"]],
            "build": [
                ["cmake", "-S", ".", "-B", "build"],
                ["cmake", "--build", "build", "--config", "Release"],
            ],
            "clean": [["cmake", "--build", "build", "--target", "clean"]],
            "run": [[f"build/Release/{bin_name}.exe"]],
            "alt_build": [["make"]],
            "alt_run": [["make", "run"]],
        }

    if target == "android":
        return {
            "build": [["./gradlew", "assembleDebug"]],
            "run": [["./gradlew", "installDebug"]],
            "test": [["./gradlew", "test"]],
            "clean": [["./gradlew", "clean"]],
        }

    if target == "ios":
        project = f"{class_name}.xcodeproj"
        return {
            "build": [
                [
                    "xcodebuild",
                    "-project",
                    project,
                    "-scheme",
                    class_name,
                    "-destination",
                    "generic/platform=iOS Simulator",
                    "-configuration",
                    "Debug",
                    "build",
                ]
            ],
            "run": [["make", "run"]],
            "test": [["make", "test"]],
            "clean": [["make", "clean"]],
        }

    return {"build": [["make"]]}
