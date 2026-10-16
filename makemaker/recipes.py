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

    if target == "python":
        return {
            "build": [["make", "lint"]],
            "test": [["make", "test"]],
            "run": [["make", "run"]],
            "clean": [["make", "clean"]],
            "install": [["make", "install"]],
        }

    if target == "node":
        extension = "ts" if str(context.get("language")) == "typescript" else "js"
        entry = f"src/index.{extension}"
        return {
            "build": [["node", "--check", entry]],
            "test": [["node", "--test"]],
            "run": [["node", entry]],
        }

    if target == "rust":
        return {
            "build": [["cargo", "build"]],
            "test": [["cargo", "test"]],
            "run": [["cargo", "run"]],
            "clean": [["cargo", "clean"]],
        }

    if target == "go":
        return {
            "build": [["go", "build", "-o", f"build/{bin_name}", "."]],
            "test": [["go", "test", "./..."]],
            "run": [["go", "run", "."]],
            "clean": [["go", "clean"]],
        }

    if target == "dotnet":
        solution = f"{class_name}.sln"
        return {
            "build": [["dotnet", "build", solution, "-c", "Release"]],
            "test": [["dotnet", "test", solution, "-c", "Release"]],
            "run": [["dotnet", "run", "--project", f"src/{class_name}"]],
            "clean": [["dotnet", "clean", solution]],
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

    if target == "bash":
        return {
            "build": [["make", "lint"]],
            "test": [["make", "test"]],
            "run": [["make", "run"]],
            "clean": [["make", "clean"]],
            "install": [["make", "install"]],
        }

    if target == "perl":
        return {
            "build": [["make", "lint"]],
            "test": [["prove", "-Ilib", "t/"]],
            "run": [["perl", "-Ilib", f"bin/{bin_name}.pl"]],
            "clean": [["make", "clean"]],
        }

    if target == "php":
        return {
            "build": [["make", "lint"]],
            "test": [["php", "tests/run.php"]],
            "run": [["php", f"bin/{bin_name}.php"]],
            "clean": [["make", "clean"]],
        }

    if target == "ruby":
        return {
            "build": [["make", "lint"]],
            "test": [["ruby", "-Ilib", "-Itest", "test/test_greeter.rb"]],
            "run": [["ruby", "-Ilib", f"bin/{bin_name}"]],
            "clean": [["make", "clean"]],
        }

    if target == "lua":
        return {
            "build": [["make", "lint"]],
            "test": [["lua", "tests/run.lua"]],
            "run": [["lua", "main.lua"]],
            "clean": [["make", "clean"]],
        }

    if target == "java":
        return {
            "build": [["mvn", "-q", "compile"]],
            "test": [["mvn", "-q", "test"]],
            "run": [["mvn", "-q", "exec:java"]],
            "clean": [["mvn", "-q", "clean"]],
        }

    if target == "kotlin":
        return {
            "build": [["gradle", "build", "-x", "test"]],
            "test": [["gradle", "test"]],
            "run": [["gradle", "run", "--quiet"]],
            "clean": [["gradle", "clean"]],
        }

    if target == "web":
        return {
            "build": [["node", "--check", "src/main.js"]],
            "test": [["node", "--test"]],
            "run": [["python3", "-m", "http.server", "8000"]],
            "clean": [["rm", "-rf", "dist"]],
        }

    if target == "powershell":
        return {
            "build": [["pwsh", "-NoProfile", "-Command", "exit 0"]],
            "test": [["pwsh", "-File", "tests/Run-Tests.ps1"]],
            "run": [
                ["pwsh", "-Command",
                 f"Import-Module ./{class_name}/{class_name}.psd1; Get-Greeting"]
            ],
            "clean": [["rm", "-f", "*.nupkg"]],
        }

    if target == "fsharp":
        solution = f"{class_name}.sln"
        return {
            "build": [["dotnet", "build", solution, "-c", "Release"]],
            "test": [["dotnet", "test", solution, "-c", "Release"]],
            "run": [["dotnet", "run", "--project", f"src/{class_name}"]],
            "clean": [["dotnet", "clean", solution]],
        }

    if target == "dart":
        return {
            "build": [["dart", "pub", "get"]],
            "test": [["dart", "test"]],
            "run": [["dart", "run", bin_name.replace("-", "_")]],
            "clean": [["rm", "-rf", ".dart_tool", "build"]],
        }

    if target == "zig":
        return {
            "build": [["zig", "build"]],
            "test": [["zig", "build", "test"]],
            "run": [["zig", "build", "run"]],
            "clean": [["rm", "-rf", ".zig-cache", "zig-out"]],
        }

    if target == "nim":
        return {
            "build": [["nimble", "build", "-y"]],
            "test": [["nimble", "test", "-y"]],
            "run": [["nimble", "run", "-y"]],
            "clean": [["rm", "-rf", "nimcache"]],
        }

    if target == "scala":
        return {
            "build": [["sbt", "compile"]],
            "test": [["sbt", "test"]],
            "run": [["sbt", "run"]],
            "clean": [["sbt", "clean"]],
        }

    if target == "ocaml":
        return {
            "build": [["dune", "build"]],
            "test": [["dune", "runtest"]],
            "run": [["dune", "exec", "bin/main.exe"]],
            "clean": [["dune", "clean"]],
        }

    if target == "r":
        return {
            "build": [["Rscript", "-e", 'cat("nothing to compile\\n")']],
            "test": [["Rscript", "tests/test_greeter.R"]],
            "run": [["Rscript", "-e",
                     'source("R/greeter.R"); cat(greet(), "\\n")']],
            "clean": [["make", "clean"]],
        }

    return {"build": [["make"]]}
