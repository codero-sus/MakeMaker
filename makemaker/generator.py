"""Turning a template + a variable context into a project on disk."""

from __future__ import annotations

import datetime as _dt
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from . import __version__
from .engine import Context, TemplateError, compile_template, evaluate, parse_expression, render
from .registry import FileRule, TemplateDef

__all__ = ["PlannedFile", "GenerationResult", "plan_files", "generate"]

TMPL_SUFFIX = ".tmpl"
META_DIR = ".makemaker"
PROJECT_FILE = "project.json"


@dataclass
class PlannedFile:
    """One file that will be written."""

    source: Path
    rel_path: str
    rendered: bool
    executable: bool = False
    mode: Optional[int] = None

    @property
    def dest_name(self) -> str:
        return PurePosixPath(self.rel_path).name


@dataclass
class GenerationResult:
    """What a generation run produced."""

    destination: Path
    template: str
    flavor: str
    language: str
    created: List[str] = field(default_factory=list)
    overwritten: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.created) + len(self.overwritten)


def _rule_applies(rule: FileRule, context: Dict[str, Any], name: str) -> bool:
    if not rule.when:
        return True
    expr = parse_expression(rule.when)
    result = evaluate(expr, Context(context))
    if result is None:
        return False
    return bool(result)


def _render_path(segment: str, context: Dict[str, Any], where: str) -> str:
    if "{{" not in segment and "{%" not in segment:
        return segment
    return render(segment, context, name=where)


def _safe_join(rel_path: str, template: str) -> PurePosixPath:
    parts = PurePosixPath(rel_path).parts
    if not parts:
        raise TemplateError(f"template {template!r} produced an empty path")
    for part in parts:
        if part in ("", ".", ".."):
            raise TemplateError(
                f"template {template!r} produced an unsafe path component {part!r} in {rel_path!r}"
            )
        if part.startswith("/") or (len(part) > 1 and part[1] == ":"):
            raise TemplateError(f"template {template!r} produced an absolute path {rel_path!r}")
    return PurePosixPath(*parts)


def plan_files(
    template: TemplateDef,
    context: Dict[str, Any],
) -> List[PlannedFile]:
    """Work out which files a generation run would write (no I/O)."""
    planned: List[PlannedFile] = []
    for source in template.files():
        rel_source = source.relative_to(template.directory).as_posix()
        rule = template.rule_for(rel_source)

        keep = True
        if rule is not None and rule.when:
            keep = _rule_applies(rule, context, f"{template.id}:{rel_source} rule")
        if not keep:
            continue

        # Directories and file names may themselves contain {{ placeholders }}.
        rendered_parts = [
            _render_path(part, context, f"{template.id}:{rel_source}")
            for part in PurePosixPath(rel_source).parts
        ]
        rel_path = PurePosixPath(*rendered_parts).as_posix()

        rendered = rel_path.endswith(TMPL_SUFFIX)
        if rendered:
            rel_path = rel_path[: -len(TMPL_SUFFIX)]
        if rule and rule.dest:
            parent = PurePosixPath(rel_path).parent
            rel_path = (parent / rule.dest).as_posix() if str(parent) != "." else rule.dest

        rel_path = _safe_join(rel_path, template.id).as_posix()

        executable = bool(rule and rule.executable)
        mode = rule.mode if rule else None
        planned.append(
            PlannedFile(
                source=source,
                rel_path=rel_path,
                rendered=rendered,
                executable=executable,
                mode=mode,
            )
        )

    seen: Dict[str, PlannedFile] = {}
    for item in planned:
        if item.rel_path in seen:
            raise TemplateError(
                f"template {template.id!r} maps two files onto {item.rel_path!r} "
                f"({seen[item.rel_path].source.name} and {item.source.name}); "
                "add a rule with a 'when' condition to disambiguate"
            )
        seen[item.rel_path] = item
    return sorted(planned, key=lambda item: item.rel_path)


def project_metadata(
    template: TemplateDef,
    context: Dict[str, Any],
    *,
    commands: Optional[Dict[str, List[str]]] = None,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """The ``.makemaker/project.json`` payload."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return {
        "format": 1,
        "generator": {"name": "makemaker", "version": __version__},
        "template": template.id,
        "target": template.os,
        "flavor": context.get("flavor", ""),
        "language": context.get("language", ""),
        "created_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project": {
            "name": context.get("project_name", ""),
            "slug": context.get("slug", ""),
            "version": context.get("version", "0.1.0"),
        },
        "commands": commands or {},
        "variables": {
            key: value
            for key, value in sorted(context.items())
            if isinstance(value, (str, int, float, bool)) or value is None
        },
    }


def _write_rendered(path: Path, source: Path, context: Dict[str, Any]) -> None:
    text = source.read_text(encoding="utf-8")
    try:
        output = compile_template(text, name=source.name, cache=False).render(context)
    except TemplateError as exc:
        raise TemplateError(f"{source.name}: {exc}") from None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(output, encoding="utf-8", newline="\n")


def _apply_mode(path: Path, planned: PlannedFile) -> None:
    if planned.mode is not None:
        path.chmod(planned.mode)
    elif planned.executable:
        current = path.stat().st_mode
        path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def generate(
    template: TemplateDef,
    context: Dict[str, Any],
    destination: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    commands: Optional[Dict[str, List[str]]] = None,
) -> GenerationResult:
    """Materialise ``template`` at ``destination``."""
    destination = Path(destination)
    planned = plan_files(template, context)
    if not planned:
        raise TemplateError(f"template {template.id!r} produced no files for this configuration")

    existing = [item.rel_path for item in planned if (destination / item.rel_path).exists()]
    if existing and not force and not dry_run:
        preview = ", ".join(existing[:3]) + (" ..." if len(existing) > 3 else "")
        raise FileExistsError(
            f"{destination} already contains {len(existing)} file(s) that would be "
            f"overwritten ({preview}); pass --force to overwrite"
        )

    result = GenerationResult(
        destination=destination,
        template=template.id,
        flavor=str(context.get("flavor", "")),
        language=str(context.get("language", "")),
    )

    for item in planned:
        target = destination / item.rel_path
        already = target.exists()
        if dry_run:
            (result.overwritten if already else result.created).append(item.rel_path)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if item.rendered:
            _write_rendered(target, item.source, context)
        else:
            shutil.copyfile(item.source, target)
        _apply_mode(target, item)
        (result.overwritten if already else result.created).append(item.rel_path)

    metadata = project_metadata(template, context, commands=commands)
    meta_path = destination / META_DIR / PROJECT_FILE
    if dry_run:
        result.created.append(f"{META_DIR}/{PROJECT_FILE}")
    else:
        import json

        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        result.created.append(f"{META_DIR}/{PROJECT_FILE}")

    return result


def read_project_metadata(directory: Path) -> Dict[str, Any]:
    """Read ``.makemaker/project.json`` from a generated project."""
    import json

    path = Path(directory) / META_DIR / PROJECT_FILE
    if not path.is_file():
        raise TemplateError(f"{directory} is not a MakeMaker project (missing {META_DIR}/{PROJECT_FILE})")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateError(f"{path}: invalid JSON ({exc})") from None


def chmod_executable(path: Path) -> None:
    """Best-effort ``chmod +x`` (ignored on platforms without POSIX modes)."""
    try:
        current = path.stat().st_mode
        os.chmod(path, current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:  # pragma: no cover - non-POSIX filesystems
        pass
