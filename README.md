# MakeMaker

Generate real applications for **Android, iOS, Windows, macOS and Linux** from
templates.

MakeMaker is a **Python + C** combination:

* **Python** (standard library only, no dependencies) renders templates and
  scaffolds the project.
* **C11** ([`native/mknative.c`](native/mknative.c)) probes the host toolchain
  and drives builds. It is optional -- every command has a Python fallback --
  but `makemaker doctor/build/run/test/clean` use it when it has been compiled.

```
$ makemaker new "Todo List" --target linux --lang c
==> Linux Application (console, c) -> ./todo-list
    wrote 9 file(s)

$ cd todo-list && makemaker build . && makemaker run .
+ make
cc -std=c11 -Wall -Wextra -O2 -g -Isrc -c src/app.c -o build/app.c.o
cc -std=c11 -Wall -Wextra -O2 -g -Isrc -c src/main.c -o build/main.c.o
cc build/app.c.o build/main.c.o -o build/todo-list
+ make run
Todo List says hello (2 + 3 = 5)
```

## Install

```sh
git clone https://github.com/codero-sus/MakeMaker
cd MakeMaker

# run straight from the checkout
python3 -m makemaker --help

# or install the CLI
pip install .
makemaker --help

# optional: compile the C companion
makemaker native build        # or: make -C native
```

Requires Python 3.9+. No third-party packages.

## Quick start

```sh
makemaker list                                  # what can I generate?
makemaker info linux                            # details for one target
makemaker new "Todo List" --target linux        # create ./todo-list
makemaker new "Todo List" --target android --target ios   # several at once
makemaker doctor                                # what toolchain do I have?

cd todo-list
makemaker build .                               # build it
makemaker test .                                # run its tests
makemaker run .                                 # run it
```

Projects are written to `<--out>/<slug>` (default `--out .`). Passing several
`--target`s in one command writes `<slug>-<target>` directories side by side.

## Targets

| Target    | Flavours          | Languages       | What you get |
|-----------|-------------------|-----------------|--------------|
| `android` | gui               | kotlin          | Gradle (Kotlin DSL) app, Material 3 UI, adaptive icon, JUnit tests, dependency-free `gradlew` |
| `ios`     | gui               | swift           | SwiftUI app, generated `.xcodeproj` **with a shared scheme**, XCTest target, `xcodebuild`/`simctl` Makefile |
| `windows` | console, gui      | c, cpp (gui: cpp) | Win32 window class + message loop, `.rc` version info, embedded DPI-aware manifest, CMake + MinGW Makefile |
| `macos`   | console, gui      | c, cpp (gui: objc) | Command line tool, or an AppKit/ARC app that builds into a real `.app` bundle |
| `linux`   | console, gui      | c, cpp          | Makefile + CMake, unit test target; GUI flavour uses GTK 4 |

Every project also gets a `.gitignore`, a `README.md` describing its own build,
and `.makemaker/project.json` recording how it was made.

Flavour/language combinations that cannot work are rejected rather than
half-generated -- `--flavor gui --lang cpp` on macOS tells you the GUI flavour
is Objective-C.

## Commands

| Command | Purpose |
|---------|---------|
| `makemaker new NAME --target T [--flavor F] [--lang L]` | create a project |
| `makemaker list [--json]` | list templates |
| `makemaker info TARGET [--json]` | flavours, languages, variables, file list |
| `makemaker doctor [--json]` | host + toolchain report (via `mknative` when built) |
| `makemaker build\|run\|test\|clean PROJECT` | run the recorded recipe |
| `makemaker native build\|doctor\|path` | manage the C companion |
| `makemaker template check [--all]` | render every reachable template combination (template authoring) |
| `makemaker version [--json]` | versions |

Useful flags for `new`: `--out DIR`, `--set key=value`, `--force`, `--dry-run`,
`--yes` (never prompt), `--verbose`.

`--set` overrides any variable, e.g.:

```sh
makemaker new "Todo List" --target android --set min_sdk=26
makemaker new "Todo List" --target ios --set team_id=XXXXXXXXXX
makemaker new "Todo List" --target linux --set domain=example.com --set author="Ada"
```

## How it works

```
makemaker/
├── engine.py        template engine (no Jinja2 -- standard library only)
├── registry.py      template discovery + template.json metadata
├── generator.py     planning + writing files, path safety
├── variables.py     deterministic names: slugs, packages, bundle ids, GUIDs
├── recipes.py       the build recipe recorded into project.json
├── native.py        bridge to the C companion (+ Python fallbacks)
├── cli.py           argument parsing and output
└── templates/       one directory per target
native/
├── mknative.c       C11: toolchain probe, JSON reader, build driver
└── Makefile
tests/               150 unittest cases, standard library only
```

### The template engine

Familiar syntax, deliberately strict about mistakes:

```
{{ name }}  {{ name | pascal }}  {{ version | replace('.', ',') }}
{% set id = 'object:main' | id %}          {# stable 24-hex id #}
{% if language == 'cpp' %} ... {% elif ... %} ... {% else %} ... {% endif %}
{% for src in sources %}{{ loop.index }}: {{ src }}{% endfor %}
{# a comment #}
```

* Undefined variables raise with a line and column, instead of silently
  rendering empty. `{{ value or 'fallback' }}` and `{% if not value %}` still
  work, because a missing value is falsy.
* Anything that looks like a tag but does not parse is an error.
* Block tags on their own line leave no blank line behind, so generated code
  keeps its indentation.
* Filters: `upper lower capitalize title trim pascal camel snake kebab slug
  screaming dot identifier quote json replace default trim_end length id guid`.
* `{{` is the only special sequence -- `${...}` (Gradle, Kotlin, Swift, CMake)
  and lone braces pass through untouched.

File and directory names are templated too, so
`src/app.{{ source_ext }}.tmpl` becomes `src/app.cpp` and
`app/src/main/java/{{ package_path }}/MainActivity.kt.tmpl` becomes the right
Java tree. Files ending in `.tmpl` are rendered; everything else is copied
byte for byte. A rendered path that tries to escape the destination is
rejected.

### Template metadata

Each `templates/<id>/template.json` declares flavours, languages, per-flavour
language restrictions, user-settable variables, required tools, and file rules:

```json
{
  "id": "linux",
  "flavors": ["console", "gui"],
  "languages": ["c", "cpp"],
  "variables": { "app_name": { "description": "Application name",
                               "default": "{{ project_name }}" } },
  "requires": { "console": ["cc", "make"], "gui": ["make", "pkg-config"] },
  "rules": [
    { "path": "src/main.*", "when": "flavor != 'gui'" },
    { "path": "tests/**",   "when": "flavor != 'gui'" }
  ]
}
```

`rules[].path` accepts exact paths, globs (`src/main.*`) and `dir/**` prefixes;
`when` is an engine expression; a rule can also set `executable`, `mode` or
`dest` (that is how `gradlew` gets its `+x` bit).

### The C companion

`native/mknative.c` is C11/POSIX with no dependencies. It contains a small JSON
reader, a `PATH` search, version capture via `popen`, and a `fork`/`exec`
runner:

```sh
mknative doctor --json      # host, tool versions, what is missing
mknative info <project>     # what .makemaker/project.json recorded
mknative build <project>    # run the recorded recipe, propagating exit codes
```

The recipe contract is shared: **a list of commands, each a list of argv
strings.** Both executors validate that shape, so a malformed `project.json`
fails loudly instead of silently doing nothing.

## Tests

```sh
python3 -m unittest discover -s tests -t . -v
```

150 tests, standard library only. They cover the engine (substitution,
expressions, filters, blocks, whitespace control, error positions), name and
package derivation, registry validation, generation of **every** template in
**every** flavour/language combination, and the Xcode project's internal
reference integrity. The native tests compile `mknative.c` with `-Werror`, then
use it to build, test, run and clean a generated C project with the real
compiler.

## What is verified here, and what is not

Verified on Linux (gcc 12, GNU Make 4.3, Python 3.11):

* Linux console projects in C and C++ build, run and pass their tests.
* `mknative` compiles warning-free and drives those builds end to end.
* All five targets generate; no template syntax survives into any output file.
* The generated `.pbxproj` is internally consistent (every object id defined,
  root object present, balanced delimiters, scheme pointing at real targets).

**Not** verified, because the sandbox has no Android SDK, JDK, Gradle, Xcode,
Windows or GTK: that the Android, iOS, Windows and macOS projects compile, and
that the Linux GTK 4 GUI builds. Those templates are conventional and
structurally checked, but treat the first build as the real test and report
anything that does not line up.

## License

MIT -- see [LICENSE](LICENSE).
