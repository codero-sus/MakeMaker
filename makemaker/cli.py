"""Command line interface for MakeMaker."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from .engine import TemplateError, compile_template, render
from .generator import generate, plan_files, read_project_metadata
from .registry import TemplateDef, load_registry, resolve_variable_defaults, template_root
from .variables import ValidationError, build_variables

PROG = "makemaker"
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


# --------------------------------------------------------------------------
# terminal helpers
# --------------------------------------------------------------------------


class UI:
    """Minimal coloured output that respects NO_COLOR and non-TTY output."""

    def __init__(self, color: str = "auto", quiet: bool = False):
        self.quiet = quiet
        if color == "always":
            self.enabled = True
        elif color == "never":
            self.enabled = False
        else:
            self.enabled = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def info(self, message: str) -> None:
        if not self.quiet:
            print(message)

    def step(self, message: str) -> None:
        if not self.quiet:
            print(f"{self.green('==>')} {message}")

    def warn(self, message: str) -> None:
        print(f"{self.yellow('warning:')} {message}", file=sys.stderr)

    def error(self, message: str) -> None:
        print(f"{PROG}: {self.red('error:')} {message}", file=sys.stderr)


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------


def _parse_set_pairs(pairs: Sequence[str]) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValidationError(f"--set expects key=value, got {pair!r}")
        key, _, value = pair.partition("=")
        key = key.strip()
        if not key.isidentifier():
            raise ValidationError(f"--set key must be an identifier, got {key!r}")
        values[key] = value.strip()
    return values


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{label}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


def _context_for(
    template: TemplateDef,
    name: str,
    *,
    flavor: str,
    language: str,
    overrides: Dict[str, Any],
    interactive: bool,
) -> Dict[str, Any]:
    base_kwargs = {key: value for key, value in overrides.items() if key in _KNOWN_KWARGS}
    context = build_variables(
        name,
        target=template.os,
        flavor=flavor,
        language=language,
        **base_kwargs,
    )
    context.update(overrides)

    for var_name, spec in template.variables.items():
        if var_name in overrides:
            continue
        if interactive and sys.stdin.isatty():
            context[var_name] = _prompt(f"{spec.description or var_name}", spec.default)
            continue
        default = spec.default
        if default and ("{{" in default or "{%" in default):
            default = render(default, context, name=f"<default {var_name}>")
        context[var_name] = default
    return context


_KNOWN_KWARGS = {
    "description",
    "author",
    "email",
    "organization",
    "domain",
    "version",
    "min_sdk",
    "target_sdk",
    "compile_sdk",
    "ios_deployment_target",
    "macos_deployment_target",
}


def _coerce_known(overrides: Dict[str, Any]) -> Dict[str, Any]:
    coerced = dict(overrides)
    for key in ("min_sdk", "target_sdk", "compile_sdk"):
        if key in coerced:
            coerced[key] = int(coerced[key])
    return coerced


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_new(args: argparse.Namespace, ui: UI) -> int:
    from .recipes import build_commands

    registry = load_registry()
    overrides = _coerce_known(_parse_set_pairs(args.set or []))
    targets = args.target or []
    if not targets:
        ui.error("no --target given; see 'makemaker list'")
        return EXIT_USAGE

    created: List[Path] = []
    for target_id in targets:
        template = registry.get(target_id)
        flavor = template.flavor(args.flavor)
        language = template.language(args.lang, flavor)
        context = _context_for(
            template,
            args.name,
            flavor=flavor,
            language=language,
            overrides=overrides,
            interactive=not args.yes,
        )

        parent = Path(args.out).expanduser()
        slug = context["slug"]
        directory = parent / (f"{slug}-{target_id}" if len(targets) > 1 else slug)
        commands = build_commands(template, context)

        ui.step(
            f"{template.display_name} ({flavor or 'default'}"
            f"{', ' + language if language else ''}) -> {directory}"
        )
        result = generate(
            template,
            context,
            directory,
            force=args.force,
            dry_run=args.dry_run,
            commands=commands,
        )
        verb = "would write" if args.dry_run else "wrote"
        ui.info(f"    {verb} {result.file_count} file(s)")
        if args.verbose:
            for rel in result.created:
                ui.info(f"      + {rel}")
            for rel in result.overwritten:
                ui.info(f"      ~ {rel}")
        if not args.dry_run:
            created.append(directory)

    if created and not args.quiet:
        first = created[0]
        ui.info("")
        ui.info(ui.bold("Next steps"))
        ui.info(f"  cd {first}")
        ui.info(f"  {PROG} doctor")
        ui.info(f"  {PROG} build .")
        ui.info(f"  {PROG} run .")
    return EXIT_OK


def cmd_list(args: argparse.Namespace, ui: UI) -> int:
    registry = load_registry()
    templates = registry.sorted()
    if args.json:
        payload = [
            {
                "id": t.id,
                "name": t.display_name,
                "os": t.os,
                "summary": t.summary,
                "flavors": t.flavors,
                "languages": t.languages,
            }
            for t in templates
        ]
        print(json.dumps(payload, indent=2))
        return EXIT_OK

    id_width = max(len(t.id) for t in templates)
    name_width = max(len(t.display_name) for t in templates)
    ui.info(ui.bold(f"{'TARGET'.ljust(id_width)}  {'NAME'.ljust(name_width)}  FLAVOURS / LANGUAGES"))
    for template in templates:
        options = []
        if template.flavors:
            options.append("flavours: " + ", ".join(template.flavors))
        if template.languages:
            options.append("languages: " + ", ".join(template.languages))
        ui.info(
            f"{ui.green(template.id.ljust(id_width))}  {template.display_name.ljust(name_width)}  "
            f"{ui.dim(' | '.join(options))}"
        )
        ui.info(f"{''.ljust(id_width)}  {ui.dim(template.summary)}")
    ui.info("")
    ui.info(f"Create one with: {PROG} new \"My App\" --target {templates[0].id}")
    return EXIT_OK


def cmd_info(args: argparse.Namespace, ui: UI) -> int:
    registry = load_registry()
    template = registry.get(args.target)
    if args.json:
        print(json.dumps(_template_payload(template), indent=2))
        return EXIT_OK
    ui.info(ui.bold(f"{template.display_name} ({template.id})"))
    ui.info(f"  host OS   : {template.os}")
    ui.info(f"  summary   : {template.summary}")
    if template.flavors:
        ui.info(f"  flavours  : {', '.join(template.flavors)} (default: {template.default_flavor})")
    if template.languages:
        ui.info(f"  languages : {', '.join(template.languages)} (default: {template.default_language})")
    requires = template.requirements(template.default_flavor)
    if requires:
        ui.info(f"  needs     : {', '.join(requires)}")
    if template.variables:
        ui.info("  variables :")
        for name, spec in sorted(template.variables.items()):
            default = f" = {spec.default}" if spec.default else ""
            ui.info(f"    --set {name}=...{default}   {ui.dim(spec.description)}")
    if template.notes:
        ui.info("")
        for line in template.notes.strip().splitlines():
            ui.info(f"  {line}")
    files = plan_files(template, _sample_context(template))
    ui.info("")
    ui.info(f"  files ({len(files)}):")
    for item in files:
        marker = "render" if item.rendered else "copy  "
        ui.info(f"    {ui.dim(marker)} {item.rel_path}")
    return EXIT_OK


def _template_payload(template: TemplateDef) -> Dict[str, Any]:
    return {
        "id": template.id,
        "display_name": template.display_name,
        "os": template.os,
        "summary": template.summary,
        "flavors": template.flavors,
        "default_flavor": template.default_flavor,
        "languages": template.languages,
        "default_language": template.default_language,
        "variables": {
            name: {"description": spec.description, "default": spec.default}
            for name, spec in template.variables.items()
        },
        "requires": template.requires,
        "files": [item.rel_path for item in plan_files(template, _sample_context(template))],
    }


def _sample_context(template: TemplateDef) -> Dict[str, Any]:
    """A throwaway context good enough to enumerate a template's files."""
    context = build_variables(
        "Sample App",
        target=template.os,
        flavor=template.default_flavor,
        language=template.language(None, template.default_flavor),
    )
    context.update(resolve_variable_defaults(template, context))
    return context


def cmd_doctor(args: argparse.Namespace, ui: UI) -> int:
    from . import native

    if args.no_native:
        report = native.python_doctor()
    else:
        report = native.native_doctor(as_json=not args.text) or native.python_doctor()

    if args.json:
        print(json.dumps(report, indent=2))
        return EXIT_OK

    if "text" in report:
        print(report["text"], end="")
        return EXIT_OK

    host = report.get("host", {})
    ui.info(ui.bold("Host"))
    ui.info(f"  system  : {host.get('system')} {host.get('release', '')}".rstrip())
    ui.info(f"  machine : {host.get('machine')}")
    ui.info(f"  python  : {host.get('python')}")
    ui.info(f"  native  : {'mknative available' if report.get('native') else 'not built (makemaker native build)'}")
    tools = report.get("tools", {})
    if tools:
        ui.info("")
        ui.info(ui.bold("Toolchain"))
        width = max(len(name) for name in tools)
        for name in sorted(tools):
            info = tools[name] or {}
            version = (info.get("version") or "").strip()
            ui.info(f"  {ui.green(name.ljust(width))}  {ui.dim(version or info.get('path', ''))}")
    missing = report.get("missing") or []
    if missing:
        ui.info("")
        ui.info(ui.bold("Not found"))
        ui.info("  " + ui.dim(", ".join(missing)))
    return EXIT_OK


def _project_commands(directory: Path, action: str) -> List[List[str]]:
    """Read a recipe from ``.makemaker/project.json``.

    A recipe is a list of commands; each command is a list of argv strings.
    Anything else is a corrupt project file and is reported as such rather
    than silently doing nothing.
    """
    metadata = read_project_metadata(directory)
    commands = metadata.get("commands") or {}
    steps = commands.get(action)
    if not steps:
        raise TemplateError(
            f"this project has no {action!r} recipe (available: {', '.join(sorted(commands))})"
        )
    if not isinstance(steps, list):
        raise TemplateError(f"the {action!r} recipe must be a list of commands")
    parsed: List[List[str]] = []
    for step in steps:
        if not isinstance(step, list) or not all(isinstance(part, str) for part in step):
            raise TemplateError(
                f"the {action!r} recipe contains a malformed command: {step!r} "
                "(expected a list of strings)"
            )
        if step:
            parsed.append(list(step))
    if not parsed:
        raise TemplateError(f"the {action!r} recipe is empty")
    return parsed


def cmd_run_recipe(args: argparse.Namespace, ui: UI) -> int:
    from . import native

    directory = Path(args.project).expanduser().resolve()
    if not directory.is_dir():
        ui.error(f"no such project directory: {directory}")
        return EXIT_ERROR
    try:
        steps = _project_commands(directory, args.action)
    except TemplateError as exc:
        ui.error(str(exc))
        return EXIT_ERROR

    binary = None if args.no_native else native.find_binary()
    if binary is not None:
        ui.step(f"{args.action} {directory} {ui.dim('(via mknative)')}")
        completed = native.run_native([args.action, str(directory)], cwd=directory)
        return completed.returncode

    ui.step(f"{args.action} {directory}")
    return native.execute_commands(steps, directory, echo=not args.quiet)


def cmd_native(args: argparse.Namespace, ui: UI) -> int:
    from . import native

    if args.native_command == "build":
        try:
            path = native.build_binary(verbose=args.verbose)
        except (RuntimeError, FileNotFoundError) as exc:
            ui.error(str(exc))
            return EXIT_ERROR
        ui.info(f"{ui.green('built')} {path}")
        return EXIT_OK
    if args.native_command == "path":
        path = native.find_binary()
        if path is None:
            ui.error("mknative is not built; run 'makemaker native build'")
            return EXIT_ERROR
        print(path)
        return EXIT_OK
    # doctor
    report = native.native_doctor(as_json=True)
    if report is None:
        ui.warn("mknative not built; falling back to the Python probe")
        report = native.python_doctor()
    print(json.dumps(report, indent=2) if args.json else _format_native_text(report))
    return EXIT_OK


def _format_native_text(report: Dict[str, Any]) -> str:
    lines = [f"host: {report.get('host', {})}"]
    for name, info in sorted((report.get("tools") or {}).items()):
        lines.append(f"  {name}: {(info or {}).get('version', '')}")
    return "\n".join(lines)


def cmd_template_check(args: argparse.Namespace, ui: UI) -> int:
    """Render templates in their flavour/language combinations.

    By default only *reachable* combinations are checked -- the ones
    ``makemaker new`` would actually accept, honouring per-flavour language
    restrictions.  ``--all`` additionally renders the full cross product,
    which catches template mistakes in configurations users cannot reach.
    """
    registry = load_registry()
    problems: List[str] = []
    total = 0
    skipped = 0
    for template in registry.sorted():
        flavors = template.flavors or [""]
        for flavor in flavors:
            allowed = template.languages_for(flavor)
            for language in template.languages or [""]:
                if allowed and language not in allowed and not args.all_combinations:
                    skipped += 1
                    continue
                total += 1
                try:
                    context = build_variables(
                        "Check App",
                        target=template.os,
                        flavor=flavor,
                        language=language,
                    )
                    context.update(resolve_variable_defaults(template, context))
                    planned = plan_files(template, context)
                    if not planned:
                        problems.append(f"{template.id}/{flavor}/{language}: no files")
                        continue
                    for item in planned:
                        if not item.rendered:
                            continue
                        text = item.source.read_text(encoding="utf-8")
                        output = compile_template(
                            text, name=item.source.name, cache=False
                        ).render(context)
                        for marker in ("{{", "{%"):
                            if marker in output:
                                problems.append(
                                    f"{template.id}/{flavor}/{language}: unresolved "
                                    f"{marker} in {item.rel_path}"
                                )
                except (TemplateError, ValidationError, OSError) as exc:
                    problems.append(f"{template.id}/{flavor}/{language}: {exc}")

    if problems:
        for problem in problems:
            ui.error(problem)
        ui.info(f"{ui.red('FAILED')} {len(problems)} problem(s) in {total} combination(s)")
        return EXIT_ERROR
    detail = ""
    if skipped and not args.all_combinations:
        detail = f" ({skipped} unreachable combination(s) skipped; use --all to include them)"
    elif args.all_combinations:
        detail = " (full cross product)"
    ui.info(f"{ui.green('OK')} {total} template combination(s) render cleanly{detail}")
    return EXIT_OK


def cmd_version(args: argparse.Namespace, ui: UI) -> int:
    from . import native

    if args.json:
        binary = native.find_binary()
        print(
            json.dumps(
                {"makemaker": __version__, "python": sys.version.split()[0], "mknative": str(binary) if binary else None},
                indent=2,
            )
        )
    else:
        ui.info(f"{PROG} {__version__} (python {sys.version.split()[0]})")
        ui.info(f"templates: {template_root()}")
        binary = native.find_binary()
        ui.info(f"mknative : {binary or 'not built'}")
    return EXIT_OK


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def _common_parser() -> argparse.ArgumentParser:
    """Flags accepted both before and after the subcommand."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default=argparse.SUPPRESS,
        help="colourise output (default: auto)",
    )
    common.add_argument(
        "-q", "--quiet", action="store_true", default=argparse.SUPPRESS,
        help="only print what is asked for",
    )
    common.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
        help="print every generated file",
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Generate real applications for Android, iOS, Windows, macOS and Linux.",
        parents=[common],
    )
    parser.set_defaults(color="auto", quiet=False, verbose=False)
    parser.add_argument("--version", action="store_true", help="print version and exit")
    sub = parser.add_subparsers(dest="command")

    new = sub.add_parser("new", parents=[common], help="create a new project from a template")
    new.add_argument("name", help="project name, e.g. \"Todo List\"")
    new.add_argument("-t", "--target", action="append", metavar="TARGET",
                     help="template id (repeatable): android, ios, windows, macos, linux")
    new.add_argument("-f", "--flavor", help="template flavour, e.g. console or gui")
    new.add_argument("-l", "--lang", dest="lang", help="language, e.g. c, cpp, objc, swift")
    new.add_argument("-o", "--out", default=".", help="parent directory for the project (default: .)")
    new.add_argument("--set", action="append", metavar="KEY=VALUE", help="set a template variable")
    new.add_argument("--force", action="store_true", help="overwrite existing files")
    new.add_argument("-y", "--yes", action="store_true", help="never prompt")
    new.add_argument("--dry-run", action="store_true", help="show what would be written")
    new.set_defaults(func=cmd_new)

    ls = sub.add_parser("list", parents=[common], help="list available templates")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_list)

    info = sub.add_parser("info", parents=[common], help="describe one template")
    info.add_argument("target")
    info.add_argument("--json", action="store_true")
    info.set_defaults(func=cmd_info)

    doctor = sub.add_parser("doctor", parents=[common], help="report host and toolchain")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--text", action="store_true", help="raw output from mknative")
    doctor.add_argument("--no-native", action="store_true", help="skip the C companion")
    doctor.set_defaults(func=cmd_doctor)

    for action in ("build", "run", "clean", "test"):
        sub_action = sub.add_parser(action, parents=[common], help=f"{action} a generated project")
        sub_action.add_argument("project", nargs="?", default=".", help="project directory")
        sub_action.add_argument("--no-native", action="store_true")
        sub_action.set_defaults(func=cmd_run_recipe, action=action)

    native_parser = sub.add_parser("native", parents=[common], help="manage the C companion (mknative)")
    native_sub = native_parser.add_subparsers(dest="native_command")
    native_sub.add_parser("build", parents=[common], help="compile native/mknative.c")
    native_sub.add_parser("path", parents=[common], help="print the path to the compiled binary")
    native_doctor = native_sub.add_parser("doctor", parents=[common], help="toolchain report from the C side")
    native_doctor.add_argument("--json", action="store_true")
    native_parser.set_defaults(func=cmd_native, native_command="doctor", json=False)

    check = sub.add_parser("template", parents=[common], help="template maintenance commands")
    check_sub = check.add_subparsers(dest="template_command")
    check = check_sub.add_parser(
        "check", parents=[common], help="render template combinations"
    )
    check.add_argument(
        "--all",
        dest="all_combinations",
        action="store_true",
        help="also render flavour/language combinations the CLI would reject",
    )
    check.set_defaults(func=cmd_template_check, template_command="check")

    version = sub.add_parser("version", parents=[common], help="print version information")
    version.add_argument("--json", action="store_true")
    version.set_defaults(func=cmd_version)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ui = UI(color=args.color, quiet=args.quiet)

    if args.version:
        return cmd_version(args, ui)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE

    try:
        return int(args.func(args, ui) or EXIT_OK)
    except ValidationError as exc:
        ui.error(str(exc))
        return EXIT_USAGE
    except TemplateError as exc:
        ui.error(str(exc))
        return EXIT_ERROR
    except FileExistsError as exc:
        ui.error(str(exc))
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        ui.error("interrupted")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
