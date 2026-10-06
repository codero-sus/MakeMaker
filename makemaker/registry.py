"""Template discovery and metadata handling.

A template is a directory under ``makemaker/templates/<id>/`` containing:

* ``template.json``  -- metadata (name, flavours, languages, file rules)
* the file tree to copy, where ``*.tmpl`` files are rendered and everything
  else is copied byte for byte.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .engine import TemplateError, parse_expression, render

__all__ = [
    "TemplateError",
    "FileRule",
    "TemplateDef",
    "Registry",
    "load_registry",
    "template_root",
    "rule_matches",
]

MANIFEST_NAME = "template.json"


def rule_matches(pattern: str, rel_path: str) -> bool:
    """Does a rule pattern apply to a template-relative path?

    Supports exact paths, ``dir/**`` prefixes and shell globs.
    """
    if pattern == rel_path:
        return True
    if pattern.endswith("/**"):
        return rel_path == pattern[:-3] or rel_path.startswith(pattern[:-2])
    return fnmatch.fnmatchcase(rel_path, pattern)


def template_root() -> Path:
    """Directory that holds the bundled templates."""
    return Path(__file__).resolve().parent / "templates"


@dataclass
class FileRule:
    """A per-file inclusion / permission rule from ``template.json``."""

    path: str
    when: str = ""
    executable: bool = False
    mode: Optional[int] = None
    dest: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileRule":
        if "path" not in data:
            raise TemplateError(f"file rule without a 'path': {data!r}")
        return cls(
            path=data["path"],
            when=data.get("when", ""),
            executable=bool(data.get("executable", False)),
            mode=data.get("mode"),
            dest=data.get("dest"),
        )


@dataclass
class VariableSpec:
    """A user-settable variable declared by a template."""

    name: str
    description: str = ""
    default: str = ""

    @classmethod
    def from_dict(cls, name: str, data: Any) -> "VariableSpec":
        if isinstance(data, str):
            return cls(name=name, default=data)
        if not isinstance(data, dict):
            raise TemplateError(f"variable {name!r} must be a string or object")
        return cls(
            name=name,
            description=data.get("description", ""),
            default=data.get("default", ""),
        )


@dataclass
class TemplateDef:
    """A loaded template definition."""

    id: str
    directory: Path
    display_name: str
    summary: str
    os: str
    flavors: List[str] = field(default_factory=list)
    default_flavor: str = "console"
    languages: List[str] = field(default_factory=list)
    default_language: str = ""
    flavor_languages: Dict[str, List[str]] = field(default_factory=dict)
    variables: Dict[str, VariableSpec] = field(default_factory=dict)
    rules: List[FileRule] = field(default_factory=list)
    requires: Dict[str, List[str]] = field(default_factory=dict)
    notes: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    # -- helpers ---------------------------------------------------------
    @property
    def supports_flavors(self) -> bool:
        return len(self.flavors) > 1

    @property
    def supports_languages(self) -> bool:
        return len(self.languages) > 1

    def flavor(self, requested: Optional[str]) -> str:
        if not self.flavors:
            return requested or ""
        if requested is None:
            return self.default_flavor
        if requested not in self.flavors:
            raise TemplateError(
                f"template {self.id!r} has no flavour {requested!r} "
                f"(choose from: {', '.join(self.flavors)})"
            )
        return requested

    def languages_for(self, flavor: str) -> List[str]:
        """Languages valid for ``flavor`` (falls back to the template-wide list)."""
        allowed = self.flavor_languages.get(flavor)
        if allowed:
            return list(allowed)
        return list(self.languages)

    def language(self, requested: Optional[str], flavor: str = "") -> str:
        """Resolve and validate the language for a flavour."""
        allowed = self.languages_for(flavor)
        if not allowed:
            return requested or ""
        if requested is None:
            if self.default_language in allowed:
                return self.default_language
            return allowed[0]
        if requested not in allowed:
            where = f" flavour {flavor!r}" if flavor and flavor in self.flavor_languages else ""
            raise TemplateError(
                f"template {self.id!r} has no language {requested!r} for{where} "
                f"(choose from: {', '.join(allowed)})"
            )
        return requested

    def requirements(self, flavor: str) -> List[str]:
        if flavor in self.requires:
            return list(self.requires[flavor])
        return list(self.requires.get("*", []))

    def rule_for(self, rel_path: str) -> Optional[FileRule]:
        """First rule whose ``path`` pattern matches ``rel_path``.

        Patterns are exact paths, ``dir/**`` prefixes or shell-style globs
        (``src/main.*`` matches ``src/main.cpp.tmpl``).
        """
        for rule in self.rules:
            if rule_matches(rule.path, rel_path):
                return rule
        return None

    def files(self) -> List[Path]:
        """Every template file, in a stable order."""
        out: List[Path] = []
        for path in sorted(self.directory.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.directory).as_posix()
            if rel == MANIFEST_NAME or rel.startswith("_"):
                continue
            out.append(path)
        return out


class Registry:
    """The set of templates bundled with MakeMaker."""

    def __init__(self, templates: Dict[str, TemplateDef]):
        self._templates = templates

    def __iter__(self):
        return iter(self.sorted())

    def __len__(self) -> int:
        return len(self._templates)

    def sorted(self) -> List[TemplateDef]:
        return [self._templates[key] for key in sorted(self._templates)]

    def ids(self) -> List[str]:
        return sorted(self._templates)

    def get(self, template_id: str) -> TemplateDef:
        try:
            return self._templates[template_id]
        except KeyError:
            raise TemplateError(
                f"unknown target {template_id!r} (available: {', '.join(self.ids())})"
            ) from None

    def __contains__(self, template_id: object) -> bool:
        return template_id in self._templates


def _load_one(directory: Path) -> TemplateDef:
    manifest_path = directory / MANIFEST_NAME
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise TemplateError(f"{directory} has no {MANIFEST_NAME}") from None
    except json.JSONDecodeError as exc:
        raise TemplateError(f"{manifest_path}: invalid JSON ({exc})") from None

    template_id = raw.get("id") or directory.name
    for key in ("display_name", "summary", "os"):
        if key not in raw:
            raise TemplateError(f"{manifest_path}: missing required key {key!r}")

    flavors = list(raw.get("flavors") or [])
    languages = list(raw.get("languages") or [])
    flavor_languages = {
        str(key): list(value) for key, value in (raw.get("flavor_languages") or {}).items()
    }
    for flavor, allowed in flavor_languages.items():
        unknown = [item for item in allowed if item not in languages]
        if unknown:
            raise TemplateError(
                f"{manifest_path}: flavor_languages[{flavor!r}] lists unknown "
                f"language(s) {', '.join(unknown)}"
            )
    default_flavor = raw.get("default_flavor") or (flavors[0] if flavors else "")
    default_language = raw.get("default_language") or (languages[0] if languages else "")
    if flavors and default_flavor not in flavors:
        raise TemplateError(f"{manifest_path}: default_flavor {default_flavor!r} not in flavors")
    if languages and default_language not in languages:
        raise TemplateError(f"{manifest_path}: default_language {default_language!r} not in languages")

    rules: List[FileRule] = []
    for entry in raw.get("rules") or []:
        rule = FileRule.from_dict(entry)
        if rule.when:
            try:
                parse_expression(rule.when)
            except TemplateError as exc:
                raise TemplateError(f"{manifest_path}: bad rule condition {rule.when!r}: {exc}") from None
        rules.append(rule)

    variables = {
        name: VariableSpec.from_dict(name, data) for name, data in (raw.get("variables") or {}).items()
    }

    requires = raw.get("requires") or {}
    if isinstance(requires, list):
        requires = {"*": list(requires)}

    return TemplateDef(
        id=template_id,
        directory=directory,
        display_name=raw["display_name"],
        summary=raw["summary"],
        os=raw["os"],
        flavors=flavors,
        default_flavor=default_flavor,
        languages=languages,
        default_language=default_language,
        flavor_languages=flavor_languages,
        variables=variables,
        rules=rules,
        requires=requires,
        notes=raw.get("notes", ""),
        raw=raw,
    )


def load_registry(root: Optional[Path] = None) -> Registry:
    """Load every template found under ``root``."""
    root = root or template_root()
    templates: Dict[str, TemplateDef] = {}
    if not root.is_dir():  # pragma: no cover - only if the package is damaged
        raise TemplateError(f"template directory not found: {root}")
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if directory.name.startswith(("_", ".")):
            continue
        definition = _load_one(directory)
        if definition.id in templates:
            raise TemplateError(f"duplicate template id {definition.id!r}")
        templates[definition.id] = definition
    if not templates:
        raise TemplateError(f"no templates found in {root}")
    return Registry(templates)


def resolve_variable_defaults(template: TemplateDef, context: Dict[str, Any]) -> Dict[str, Any]:
    """Fill in template-declared variables the user did not set explicitly."""
    resolved: Dict[str, Any] = {}
    for name, spec in template.variables.items():
        if name in context:
            continue
        default = spec.default
        if default and ("{{" in default or "{%" in default):
            default = render(default, context, name=f"<default {name}>")
        resolved[name] = default
    return resolved
