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

## Run it as a single file

```sh
python3 tools/build_zipapp.py          # writes dist/makemaker.pyz
./dist/makemaker.pyz list              # on Unix it is directly executable
python3 dist/makemaker.pyz list        # on Windows, invoke it via python
```

`makemaker.pyz` is one ~230 KiB file containing the package and all 394
template files. It runs on Linux, macOS and Windows wherever Python 3.9+ is
installed, and it is built with the standard library only -- no PyInstaller,
no dependencies. Templates are unpacked to a temporary directory on first use,
because `pathlib` cannot read through a zip archive.

If you want a binary that needs **no Python at all**, that is what
`tools/build_binary.py` is for:

```sh
python3 -m pip install pyinstaller     # build-time only
python3 tools/build_binary.py          # writes dist/makemaker[.exe]
```

PyInstaller cannot cross-compile, so this produces a binary for the machine it
runs on. The release workflow runs it on one runner per OS and attaches the
results. PyInstaller is a build-time tool only -- MakeMaker itself still
imports nothing outside the standard library.

The optional C companion is likewise host-specific:

```sh
python3 tools/build_native.py          # writes build/mknative[.exe]
```

That script exists because `make` is usually absent on Windows.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request across
**ubuntu / macOS / windows × Python 3.9, 3.11 and 3.13**:

1. asserts the stdlib-only claim by walking the AST for non-stdlib imports;
2. runs the full test suite;
3. runs `makemaker template validate` and `template check --all`;
4. generates Python and Node projects (both TypeScript and JavaScript) and
   fails if any template syntax survived into the output;
5. builds the C companion with `-Werror` and the zipapp, then smoke-tests the
   zipapp by generating a project *from inside the bundle*.

Steps that need a toolchain skip themselves when it is absent, so a Windows
runner does not fail for lacking `make` or `perl`.

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

Forty-five templates, forty-three languages:

### Native and mobile

| Target    | Flavours          | Languages       | What you get |
|-----------|-------------------|-----------------|--------------|
| `android` | gui               | kotlin          | Gradle (Kotlin DSL) app, Material 3 UI, adaptive icon, JUnit tests, dependency-free `gradlew` |
| `ios`     | gui               | swift           | SwiftUI app, generated `.xcodeproj` **with a shared scheme**, XCTest target, `xcodebuild`/`simctl` Makefile |
| `windows` | console, gui      | c, cpp (gui: cpp) | Win32 window class + message loop, `.rc` version info, embedded DPI-aware manifest, CMake + MinGW Makefile |
| `macos`   | console, gui      | c, cpp, swift (gui: objc) | Command line tool in C, C++ or Swift, or an AppKit/ARC app that builds into a real `.app` bundle |
| `linux`   | console, gui      | c, cpp          | Makefile + CMake, unit test target; GUI flavour uses GTK 4 |

### Languages and runtimes

| Target | Languages | What you get |
|--------|-----------|--------------|
| `python` | python | src-layout package, argparse CLI, `unittest` suite, `pyproject.toml` with a console script |
| `node` | typescript, javascript | Zero-dependency ESM CLI using the built-in `node:test` runner; TypeScript runs with no build step |
| `deno` | typescript | Deno CLI with `deno test`, `deno check` and `deno lint`; no dependencies, no lockfile step |
| `rust` | rust | Cargo lib + bin of the same name, unit tests in `src/lib.rs`, integration tests in `tests/` |
| `go` | go | Go module, table-driven tests, Makefile with `fmt`/`vet` targets |
| `dotnet` | csharp | Solution with a console app and an xUnit test project, top-level statements entry point |
| `fsharp` | fsharp | F# solution with an xUnit test project |
| `java` | java | Maven project, JUnit 5 tests, `exec:java` run target |
| `kotlin` | kotlin | Kotlin JVM app on Gradle Kotlin DSL with `kotlin.test` |
| `scala` | scala | sbt project with an MUnit suite |
| `groovy` | groovy | Groovy class + CLI with built-in assertions; no Spock or JUnit |
| `dart` | dart | Pub package, `package:test` suite, analysis options |
| `crystal` | crystal | Shard with library, executable and `crystal spec` suite |
| `zig` | zig | `build.zig` with library, executable and a `test` step |
| `nim` | nim | nimble package with built-in `unittest` suite |
| `d` | d | dub package with in-source `unittest` blocks |
| `odin` | odin | Odin package with `@(test)` procedures run by `odin test` |
| `haskell` | haskell | Cabal package: library, executable, exitcode-stdio test suite |
| `ocaml` | ocaml | dune project with library, executable and test stanza |
| `elixir` | elixir | Mix project with ExUnit tests, no Hex dependencies |
| `erlang` | erlang | rebar3 application with an EUnit suite |
| `gleam` | gleam | Gleam project targeting Erlang with a gleeunit suite |
| `clojure` | clojure | deps.edn project with `clojure.test` |
| `julia` | julia | Package with module, entry point and Test stdlib suite |
| `scheme` | scheme | R7RS library and CLI with a tiny assertion harness |
| `racket` | racket | Package with module, CLI and rackunit tests |
| `prolog` | prolog | SWI-Prolog module with a plunit suite |
| `ada` | ada | gprbuild project with a library and assertion-based test |
| `pascal` | pascal | Free Pascal unit, program and a hand-rolled test runner |
| `fortran` | fortran | fpm project: library, program, unit tests |
| `web` | javascript | Static site: HTML, CSS, vanilla ES modules, `node --test` against the logic module |

### Scripting

| Target | Languages | What you get |
|--------|-----------|--------------|
| `bash` | bash | Library file, CLI with `--help`/`--version`, Bash-only test runner, `install` target |
| `perl` | perl | Module + CLI + `Test::More` suite; core modules only |
| `php` | php | Composer package with PSR-4 autoloading and a runnable test script |
| `ruby` | ruby | Gem layout, minitest suite, Rakefile |
| `lua` | lua | Module, entry script and a zero-dependency test runner |
| `tcl` | tcl | Package with library, CLI and tcltest suite |
| `powershell` | powershell | Module manifest + `.psm1` with exported functions and a test script |
| `awk` | awk | POSIX-awk scripts with a shell test runner; works under mawk, gawk, BusyBox |
| `r` | r | DESCRIPTION/NAMESPACE package with base-R tests |

Every project also gets a `.gitignore`, a `README.md` describing its own build,
and `.makemaker/project.json` recording how it was made.

Those templates expand to **54 reachable flavour/language combinations**.
`makemaker template check` renders all 54; add `--all` to also render the 5
combinations the CLI would reject. `makemaker template validate` runs static
checks over all 45 without generating anything.

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
| `makemaker template validate [-t ID] [--json]` | static checks: dead rules, `template.json` key typos, output-path collisions |
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
├── lint.py          static template checks (`makemaker template validate`)
├── cli.py           argument parsing and output
└── templates/       one directory per target
native/
├── mknative.c       C11: toolchain probe, JSON reader, build driver
└── Makefile
tools/
├── build_zipapp.py  single-file cross-platform bundle (stdlib only)
├── build_binary.py  native binary via PyInstaller (build-time tool)
└── build_native.py  compile mknative, including on Windows
.github/workflows/   ci.yml (test matrix) and release.yml (artifacts)
tests/               253 unittest cases, standard library only
```

### The template engine

Familiar syntax, deliberately strict about mistakes:

```
{{ name }}  {{ name | pascal }}  {{ version | replace('.', ',') }}
{% set id = 'object:main' | id %}          {# stable 24-hex id #}
{% if language == 'cpp' %} ... {% elif ... %} ... {% else %} ... {% endif %}
{% for src in sources %}{{ loop.index }}: {{ src }}{% endfor %}
{% raw %}{{ this is emitted literally }}{% endraw %}
{# a comment #}
```

* Undefined variables raise with a line and column, instead of silently
  rendering empty. `{{ value or 'fallback' }}` and `{% if not value %}` still
  work, because a missing value is falsy.
* Anything that looks like a tag but does not parse is an error.
* Block tags on their own line leave no blank line behind, so generated code
  keeps its indentation.
* Filters: `upper lower capitalize title trim pascal camel snake kebab slug
  screaming dot identifier quote json replace default trim_end length id guid
  join`.
* `{{` is the only special sequence -- `${...}` (Gradle, Kotlin, Swift, CMake)
  and lone braces pass through untouched.

File and directory names are templated too, so
`src/app.{{ source_ext }}.tmpl` becomes `src/app.cpp` and
`app/src/main/java/{{ package_path }}/MainActivity.kt.tmpl` becomes the right
Java tree. Files ending in `.tmpl` are rendered; everything else is copied
byte for byte. A rendered path that tries to escape the destination is
rejected. Generated `.bat`, `.cmd` and `.sln` files are written with CRLF line endings
because Windows tooling expects it; every other file, including POSIX shell
scripts such as `gradlew`, stays LF.

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

253 tests, standard library only. They cover the engine (substitution,
expressions, filters, blocks, `{% raw %}`, the bounded compile cache,
whitespace control, error positions), name and package derivation, registry
validation, line-ending policy, generation of **every** template in **every**
reachable flavour/language combination, the Xcode project's internal
reference integrity, and the static template validator -- including a broken
template for each check, so a linter that stopped finding problems would fail
the suite.

Some tests go further and *run* what was generated:

* `tests/test_native.py` compiles `mknative.c` with `-Werror`, then uses it to
  build, test, run and clean a generated C project with the real compiler.
* `tests/test_generated_projects.py` runs `make test`/`make run` on a generated
  Python package and `node --test` on generated Node projects in **both**
  JavaScript and TypeScript, asserting the CLIs actually print the expected
  output. Each of these skips itself when its toolchain is absent.

## What is verified here, and what is not

Verified on Linux (gcc 12, GNU Make 4.3, Python 3.11, Node 22, Perl 5.36,
Bash 5.2):

* Linux console projects in C and C++ build, run and pass their tests.
* Generated **Python** packages pass `make test`, `make lint`, and run.
* Generated **Node** projects pass `node --test` and run, in both TypeScript
  (native type stripping, no build step) and JavaScript.
* Generated **web** projects parse under `node --check` and pass `node --test`.
* Generated **Bash** projects run and pass their own test runner; the CLI and
  test scripts are checked for the executable bit.
* Generated **Perl** projects compile under `perl -c`, run, and pass their
  `Test::More` suite.
* Generated **AWK** projects run and pass their shell test runner.
* `mknative` compiles warning-free and drives those builds end to end.
* All 45 targets generate; no template syntax survives into any output file.
* Every target has a recipe, and every recipe is a list of argv lists.
* The generated `.pbxproj` is internally consistent (every object id defined,
  root object present, balanced delimiters, scheme pointing at real targets).

**Not** verified here, because this sandbox has no Android SDK, JDK, Gradle,
Xcode, Windows, GTK, Rust, Go, .NET SDK, or the toolchains for most of the
languages above: that the Android, iOS, Windows, macOS, Rust, Go, .NET, Java,
Kotlin, Scala, Groovy, Dart, Deno, Crystal, Zig, Nim, D, Odin, Haskell, OCaml,
Elixir, Erlang, Gleam, Clojure, Julia, Scheme, Racket, Prolog, Ada, Pascal,
Fortran, F#, PHP, Ruby, Lua, Tcl, PowerShell and R projects actually compile
or pass their tests, and that the Linux GTK 4 GUI builds. Those templates
render cleanly, pass the static checks and follow each ecosystem's
conventional layout, but treat the first build as the real test and report
anything that does not line up.

Two caveats worth naming rather than burying:

* Each new template's tests were written but **never executed** here, because
  the interpreter is missing. A test that has not run may itself be wrong; the
  project source has been reviewed by generating and reading it, which caught
  five real bugs, but that is not the same as a green test run.
* The generated code targets current stable versions of each toolchain. If a
  language has moved on, expect the usual version churn.

Where a target needs network access on first use (Maven fetching JUnit, sbt
fetching MUnit, `dart pub get`, Cargo, Go modules), that is noted in the
template's own README and in `makemaker info <target>`.

## License

MIT -- see [LICENSE](LICENSE).
