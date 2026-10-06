"""Deriving the variable context handed to every template.

Everything here is deterministic: the same project name always produces the
same package names, bundle identifiers, class names and GUIDs.  That makes
generated projects reproducible and keeps the test suite meaningful.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, Iterable, List, Optional

from .engine import case_camel, case_kebab, case_pascal, case_snake, case_screaming, stable_guid

__all__ = [
    "ValidationError",
    "validate_name",
    "java_package",
    "java_segment",
    "bundle_id",
    "as_identifier",
    "build_variables",
]


class ValidationError(ValueError):
    """Raised when user-supplied input cannot be used to build a project."""


_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,63}$")


def validate_name(name: str) -> str:
    """Check a project name and return it trimmed."""
    name = (name or "").strip()
    if not name:
        raise ValidationError("project name must not be empty")
    if not _NAME_OK.match(name):
        raise ValidationError(
            f"invalid project name {name!r}: use 1-64 letters, digits, spaces, "
            "'.', '_', '+' or '-' and start with a letter or digit"
        )
    return name


def _segments(value: str) -> List[str]:
    return [part for part in case_snake(value).split("_") if part]


def java_package(*parts: Iterable[str]) -> str:
    """Build a valid Java/Kotlin package name from arbitrary text.

    Rules enforced: lowercase, dot separated, each segment a valid Java
    identifier (no leading digit, no keywords).
    """
    out: List[str] = []
    for part in parts:
        for word in _segments(str(part)):
            cleaned = re.sub(r"[^0-9a-z_]", "", word)
            if not cleaned:
                continue
            if cleaned[0].isdigit():
                cleaned = "a" + cleaned
            if cleaned in _JAVA_KEYWORDS:
                cleaned = cleaned + "_"
            out.append(cleaned)
    if not out:
        out = ["app"]
    return ".".join(out)


_JAVA_KEYWORDS = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class",
    "const", "continue", "default", "do", "double", "else", "enum", "extends", "final",
    "finally", "float", "for", "goto", "if", "implements", "import", "instanceof", "int",
    "interface", "long", "native", "new", "package", "private", "protected", "public",
    "return", "short", "static", "strictfp", "super", "switch", "synchronized", "this",
    "throw", "throws", "transient", "try", "void", "volatile", "while",
}


def bundle_id(*parts: Iterable[str]) -> str:
    """Reverse-DNS identifier (iOS bundle id, Android applicationId, ...)."""
    return java_package(*parts)


_SOURCE_EXT = {
    "c": "c",
    "cpp": "cpp",
    "objc": "m",
    "swift": "swift",
    "kotlin": "kt",
    "java": "java",
}


def as_identifier(value: Any) -> str:
    """Make a derived name safe to use as an identifier in C/Swift/Kotlin/Java.

    Only the leading-digit case needs fixing: every other illegal character is
    already stripped by the case helpers.  ``"2Fast"`` -> ``"App2Fast"``.
    """
    text = str(value)
    if text and text[0].isdigit():
        return "App" + text
    return text


def _date_values(now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return {
        "year": str(now.year),
        "date": now.strftime("%Y-%m-%d"),
        "datetime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "copyright_year": str(now.year),
    }


def build_variables(
    name: str,
    *,
    target: str,
    flavor: str = "console",
    language: str = "cpp",
    description: str = "",
    author: str = "",
    email: str = "",
    organization: str = "",
    domain: str = "example.com",
    version: str = "0.1.0",
    min_sdk: int = 24,
    target_sdk: int = 34,
    compile_sdk: int = 34,
    ios_deployment_target: str = "16.0",
    macos_deployment_target: str = "12.0",
    windows_sdk: str = "10.0",
    c_standard: str = "c11",
    cpp_standard: str = "c++17",
    extra: Optional[Dict[str, Any]] = None,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """Build the full variable context for a generation run."""
    name = validate_name(name)
    slug = case_kebab(name)
    identifier = as_identifier(case_snake(name))
    class_name = as_identifier(case_pascal(name))
    organization = (organization or "").strip()
    domain = (domain or "example.com").strip().lower()
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
        raise ValidationError(f"invalid domain {domain!r} (expected something like example.com)")

    org_segment = java_package(organization) if organization else "example"
    # Reverse the domain so com.example.myapp reads correctly.
    reversed_domain = ".".join(reversed([s for s in domain.split(".") if s]))
    app_id = java_package_from_dotted(reversed_domain, slug)

    values: Dict[str, Any] = {
        # identity
        "project_name": name,
        "name": name,
        "slug": slug,
        "identifier": identifier,
        "class_name": class_name,
        "camel_name": as_identifier(case_camel(name)),
        "screaming_name": as_identifier(case_screaming(name)),
        "bin_name": slug,
        "exe_name": slug,
        "description": description or f"{name} - generated by MakeMaker",
        "summary": description or f"{name} - generated by MakeMaker",
        "author": author or "MakeMaker",
        "email": email or "you@example.com",
        "organization": organization or class_name,
        "org_segment": org_segment,
        "domain": domain,
        "domain_url": f"https://{domain}",
        "version": version,
        # platform selection
        "target": target,
        "platform": target,
        "flavor": flavor,
        "language": language,
        "is_gui": flavor == "gui",
        "is_console": flavor != "gui",
        "is_c": language == "c",
        "is_cpp": language == "cpp",
        "is_objc": language == "objc",
        "is_swift": language == "swift",
        "is_kotlin": language == "kotlin",
        "is_c_family": language in ("c", "cpp", "objc"),
        "source_ext": _SOURCE_EXT.get(language, "c"),
        "header_ext": "h",
        # toolchain knobs
        "c_standard": c_standard,
        "cpp_standard": cpp_standard,
        # mobile
        "package": app_id,
        "application_id": app_id,
        "bundle_id": app_id,
        "package_path": app_id.replace(".", "/"),
        "min_sdk": min_sdk,
        "target_sdk": target_sdk,
        "compile_sdk": compile_sdk,
        "ios_deployment_target": ios_deployment_target,
        "macos_deployment_target": macos_deployment_target,
        "windows_sdk": windows_sdk,
        # identifiers that must be stable across runs
        "project_guid": stable_guid(f"{app_id}:{slug}"),
        "upgrade_guid": stable_guid(f"upgrade:{app_id}:{slug}"),
        # generator provenance
        "makemaker_url": "https://github.com/codero-sus/MakeMaker",
        **_date_values(now),
    }
    if extra:
        for key, value in extra.items():
            values[key] = value
    return values


def java_segment(value: Any) -> str:
    """Collapse free-form text into ONE valid Java identifier segment.

    ``"Todo List"`` -> ``todolist``.  App names should not fan out into extra
    package segments, so ``com.example.todolist`` and not
    ``com.example.todo.list``.
    """
    words = _segments(str(value))
    joined = "".join(words)
    cleaned = re.sub(r"[^0-9a-z_]", "", joined)
    if not cleaned:
        return "app"
    if cleaned[0].isdigit():
        cleaned = "a" + cleaned
    if cleaned in _JAVA_KEYWORDS:
        cleaned += "_"
    return cleaned


def java_package_from_dotted(prefix: str, *parts: Iterable[str]) -> str:
    """Combine an already-dotted prefix with free-form trailing segments."""
    cleaned_prefix = ".".join(
        java_package(segment) for segment in prefix.split(".") if segment.strip()
    )
    tail = java_segment(" ".join(str(part) for part in parts))
    return f"{cleaned_prefix}.{tail}" if cleaned_prefix else tail
