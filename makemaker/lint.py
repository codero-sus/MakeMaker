"""Static checks for bundled templates.

These run without generating anything, so a template author finds out about a
mistake immediately instead of when a user generates a broken project. Every
check here exists because the bug it catches was either hit during development
or is one step away:

* a ``.gitignore`` that contains ``{{ ... }}`` but is not a ``.tmpl`` is copied
  verbatim, leaking template syntax into the generated project;
* a rule whose ``path`` matches nothing is a typo that silently stops applying;
* a misspelled key in ``template.json`` is ignored rather than reported;
* two template files that render onto the same output path clobber each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from .engine import Template, TemplateError, compile_template
from .generator import render_relative_path, rule_applies
from .registry import MANIFEST_NAME, TemplateDef, load_registry, rule_matches
from .variables import ValidationError, build_variables

__all__ = ["Issue", "validate_template", "validate_registry", "manifest_summary"]

KNOWN_MANIFEST_KEYS: Set[str] = {
    "id", "display_name", "summary", "os", "flavors", "default_flavor",
    "languages", "default_language", "flavor_languages", "variables",
    "requires", "rules", "notes",
}

KNOWN_RULE_KEYS: Set[str] = {"path", "when", "executable", "mode", "dest"}

#: Markers that must never survive into a generated file.
SYNTAX_MARKERS = ("{{", "{%", "{#")


@dataclass
class Issue:
    """One problem found in a template."""

    template: str
    message: str
    path: str = ""

    def format(self) -> str:
        where = f"{self.template}/{self.path}" if self.path else self.template
        return f"{where}: {self.message}"


def sample_context(template: TemplateDef, flavor: str, language: str) -> Dict[str, Any]:
    """A throwaway context good enough to plan and render a template."""
    context = build_variables(
        "Validate App", target=template.os, flavor=flavor, language=language
    )
    for name, spec in template.variables.items():
        default = spec.default
        if default and ("{{" in default or "{%" in default):
            default = compile_template(default, cache=False).render(context)
        context[name] = default
    return context


def _check_manifest(template: TemplateDef) -> List[Issue]:
    issues: List[Issue] = []
    for key in template.raw:
        if key not in KNOWN_MANIFEST_KEYS:
            issues.append(Issue(
                template.id,
                f"unknown key {key!r} in {MANIFEST_NAME} "
                f"(known: {', '.join(sorted(KNOWN_MANIFEST_KEYS))})",
                MANIFEST_NAME,
            ))
    for index, entry in enumerate(template.raw.get("rules") or []):
        if not isinstance(entry, dict):
            continue
        for key in entry:
            if key not in KNOWN_RULE_KEYS:
                issues.append(Issue(
                    template.id,
                    f"rule {index} has unknown key {key!r} "
                    f"(known: {', '.join(sorted(KNOWN_RULE_KEYS))})",
                    MANIFEST_NAME,
                ))
    return issues


def _check_verbatim_files(
    template: TemplateDef, files: List[Any], relative: List[str]
) -> List[Issue]:
    """Files copied verbatim must not contain template syntax."""
    issues: List[Issue] = []
    for path, rel in zip(files, relative):
        if rel.endswith(".tmpl") or rel == MANIFEST_NAME:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary asset, copied verbatim by design
        for marker in SYNTAX_MARKERS:
            if marker in text:
                issues.append(Issue(
                    template.id,
                    f"contains {marker} but is not a .tmpl, so it is copied "
                    "verbatim; rename it to end in .tmpl",
                    rel,
                ))
                break
    return issues


def _check_templates_parse(
    template: TemplateDef, files: List[Any], relative: List[str]
) -> List[Issue]:
    issues: List[Issue] = []
    for path, rel in zip(files, relative):
        if not rel.endswith(".tmpl"):
            continue
        try:
            Template(path.read_text(encoding="utf-8"), name=rel)
        except TemplateError as exc:
            issues.append(Issue(template.id, f"does not parse: {exc}", rel))
    return issues


def _check_rules_reach_something(template: TemplateDef, relative: List[str]) -> List[Issue]:
    issues: List[Issue] = []
    for rule in template.rules:
        if not any(rule_matches(rule.path, rel) for rel in relative):
            issues.append(Issue(
                template.id,
                f"rule path {rule.path!r} matches no template file",
                MANIFEST_NAME,
            ))
    return issues


def _check_shadowed_rules(template: TemplateDef, relative: List[str]) -> List[Issue]:
    """Flag rules that can never fire because an earlier rule already matched.

    ``TemplateDef.rule_for`` is first-match-wins, so a rule placed after a
    broader pattern is dead code that looks like it is doing something.
    """
    issues: List[Issue] = []
    for index, rule in enumerate(template.rules):
        matched = [rel for rel in relative if rule_matches(rule.path, rel)]
        if not matched:
            continue  # reported by _check_rules_reach_something
        earlier = template.rules[:index]
        if earlier and all(
            any(rule_matches(other.path, rel) for other in earlier) for rel in matched
        ):
            blocker = next(
                (other for other in earlier
                 if all(rule_matches(other.path, rel) for rel in matched)),
                earlier[0],
            )
            issues.append(Issue(
                template.id,
                f"rule {index} ({rule.path!r}) is shadowed by the earlier rule "
                f"{blocker.path!r}; rules are first-match-wins, so this one never applies",
                MANIFEST_NAME,
            ))
    return issues


def _check_combinations(template: TemplateDef) -> List[Issue]:
    """Plan and render every reachable flavour/language combination."""
    issues: List[Issue] = []
    files = template.files()
    relative = [path.relative_to(template.directory).as_posix() for path in files]

    for flavor in template.flavors or [""]:
        for language in template.languages_for(flavor) or [""]:
            try:
                context = sample_context(template, flavor, language)
            except (ValidationError, TemplateError) as exc:
                issues.append(Issue(
                    template.id, f"cannot build a variable context: {exc}",
                    f"{flavor}/{language}",
                ))
                continue

            seen: Set[str] = set()
            for path, rel in zip(files, relative):
                rule = template.rule_for(rel)
                if rule is not None and rule.when:
                    try:
                        keep = rule_applies(rule, context, f"{template.id}:{rel}")
                    except TemplateError as exc:
                        issues.append(Issue(
                            template.id, f"rule condition failed: {exc}", rel
                        ))
                        continue
                    if not keep:
                        continue

                try:
                    rendered_rel = render_relative_path(rel, context, f"{template.id}:{rel}")
                except TemplateError as exc:
                    issues.append(Issue(template.id, f"cannot render path: {exc}", rel))
                    continue

                if rendered_rel in seen:
                    issues.append(Issue(
                        template.id,
                        f"two template files map onto {rendered_rel!r}; add a "
                        "rule with a 'when' condition",
                        rel,
                    ))
                seen.add(rendered_rel)

                if not rel.endswith(".tmpl"):
                    continue
                try:
                    output = compile_template(
                        path.read_text(encoding="utf-8"), name=rel, cache=False
                    ).render(context)
                except TemplateError as exc:
                    issues.append(Issue(template.id, f"render failed: {exc}", rel))
                    continue
                for marker in ("{{", "{%"):
                    if marker in output:
                        issues.append(Issue(
                            template.id,
                            f"unresolved {marker} survives into {rendered_rel}",
                            rel,
                        ))
                        break
    return issues


def validate_template(template: TemplateDef) -> List[Issue]:
    """Run every static check against one template.

    A file-level problem (a template that will not parse, say) is the same
    problem in every flavour, so duplicates are collapsed and reported once.
    """
    files = template.files()
    relative = [path.relative_to(template.directory).as_posix() for path in files]

    issues = _check_manifest(template)
    issues += _check_verbatim_files(template, files, relative)
    issues += _check_templates_parse(template, files, relative)
    issues += _check_rules_reach_something(template, relative)
    issues += _check_shadowed_rules(template, relative)
    issues += _check_combinations(template)

    unique: List[Issue] = []
    seen: Set[tuple] = set()
    for issue in issues:
        key = (issue.template, issue.path, issue.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


def validate_registry(registry: Optional[Any] = None) -> List[Issue]:
    """Validate every bundled template."""
    registry = registry or load_registry()
    issues: List[Issue] = []
    for template in registry.sorted():
        issues.extend(validate_template(template))
    return issues


def manifest_summary(template: TemplateDef) -> Dict[str, Any]:
    """A small JSON-able summary, used by ``--json`` output."""
    return {
        "id": template.id,
        "flavors": template.flavors,
        "languages": template.languages,
        "files": len(template.files()),
        "rules": len(template.rules),
        "variables": sorted(template.variables),
    }
