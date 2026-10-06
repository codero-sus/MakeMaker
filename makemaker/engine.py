"""A tiny, dependency-free template engine.

MakeMaker cannot rely on Jinja2 (or anything else outside the standard
library), so this module implements just enough of a familiar template
language to render source trees:

    {{ name }}                     substitution
    {{ name | upper }}             substitution with a filter
    {{ a or 'fallback' }}          expressions
    {% if lang == 'cpp' %} ... {% else %} ... {% endif %}
    {% for src in sources %} ... {% endfor %}
    {# a comment #}

Whitespace control works the way you would expect from a code generator:

* A ``{% ... %}`` tag that sits alone on a line does not leave a blank line
  behind (leading indent and the trailing newline are removed).
* ``{%-`` / ``-%}`` (and ``{{-`` / ``-}}``) strip whitespace explicitly.

Anything that looks like a tag but does not parse is an error, so typos in a
template never silently leak into generated code.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "TemplateError",
    "Context",
    "Template",
    "render",
    "FILTERS",
    "case_pascal",
    "case_camel",
    "case_snake",
    "case_kebab",
    "case_screaming",
    "stable_id",
    "stable_guid",
]


class TemplateError(Exception):
    """Raised for malformed templates or undefined variables."""

    def __init__(self, message: str, source: Optional[str] = None, pos: int = 0):
        self.source = source
        self.pos = pos
        if source is not None:
            line = source.count("\n", 0, pos) + 1
            column = pos - (source.rfind("\n", 0, pos) + 1) + 1
            message = f"{message} (line {line}, column {column})"
        super().__init__(message)


# --------------------------------------------------------------------------
# Case helpers (used both by the engine's filters and by variable derivation)
# --------------------------------------------------------------------------

_WORD_SPLIT = re.compile(r"[^0-9a-zA-Z]+")


def _words(value: Any) -> List[str]:
    """Split a value into lowercase words, honouring camelCase boundaries."""
    text = str(value)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    parts: List[str] = []
    for chunk in _WORD_SPLIT.split(text):
        if chunk:
            parts.append(chunk)
    return [p.lower() for p in parts]


def case_pascal(value: Any) -> str:
    """``hello world`` -> ``HelloWorld``"""
    return "".join(w[:1].upper() + w[1:] for w in _words(value))


def case_camel(value: Any) -> str:
    """``hello world`` -> ``helloWorld``"""
    pascal = case_pascal(value)
    return pascal[:1].lower() + pascal[1:]


def case_snake(value: Any) -> str:
    """``Hello World`` -> ``hello_world``"""
    return "_".join(_words(value))


def case_kebab(value: Any) -> str:
    """``Hello World`` -> ``hello-world``"""
    return "-".join(_words(value))


def case_screaming(value: Any) -> str:
    """``Hello World`` -> ``HELLO_WORLD``"""
    return "_".join(_words(value)).upper()


def case_dot(value: Any) -> str:
    """``Hello World`` -> ``hello.world``"""
    return ".".join(_words(value))


_STABLE_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def stable_id(key: Any, length: int = 24) -> str:
    """Deterministic uppercase hex identifier.

    Xcode projects and Visual Studio solutions identify their objects with
    opaque hex strings.  Deriving them from a hash keeps generated projects
    byte-for-byte reproducible while still being unique inside the file.
    """
    digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
    return digest[: max(1, min(length, 40))].upper()


def stable_guid(key: Any) -> str:
    """Deterministic UUID string (used for C#/VS project identifiers)."""
    return str(uuid.uuid5(_STABLE_NAMESPACE, str(key)))


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------


def _f_replace(value: Any, old: str, new: str) -> str:
    return str(value).replace(old, new)


def _f_default(value: Any, fallback: Any) -> Any:
    if value is None or isinstance(value, Undefined):
        return fallback
    return value if value not in ("", [], {}) else fallback


def _f_identifier(value: Any) -> str:
    """Sanitise into something safe for a package/namespace segment."""
    text = case_snake(value)
    text = re.sub(r"[^0-9a-z_]", "", text)
    if not text:
        text = "app"
    if text[0].isdigit():
        text = "a" + text
    return text


def _f_trim_end(value: Any, suffix: str) -> str:
    text = str(value)
    return text[: -len(suffix)] if suffix and text.endswith(suffix) else text


FILTERS: Dict[str, Callable[..., Any]] = {
    "upper": lambda v: str(v).upper(),
    "lower": lambda v: str(v).lower(),
    "capitalize": lambda v: str(v)[:1].upper() + str(v)[1:],
    "title": lambda v: str(v).title(),
    "trim": lambda v: str(v).strip(),
    "pascal": case_pascal,
    "class": case_pascal,
    "camel": case_camel,
    "snake": case_snake,
    "kebab": case_kebab,
    "slug": case_kebab,
    "screaming": case_screaming,
    "dot": case_dot,
    "identifier": _f_identifier,
    "quote": lambda v: shlex.quote(str(v)),
    "json": lambda v: json.dumps(v),
    "replace": _f_replace,
    "default": _f_default,
    "trim_end": _f_trim_end,
    "length": lambda v: len(v),
    "id": stable_id,
    "guid": stable_guid,
}


# --------------------------------------------------------------------------
# Expression lexer / parser
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<number>\d+\.\d+|\d+)
      | (?P<string>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
      | (?P<op><=|>=|==|!=|//|[+\-*/%<>()|,:.])
      | (?P<name>[A-Za-z_][A-Za-z_0-9]*)
    )
    """,
    re.VERBOSE,
)

_KEYWORDS = {"and", "or", "not", "in", "True", "False", "None"}


@dataclass
class _Tok:
    kind: str  # number | string | op | name | eof
    value: Any
    pos: int


def _lex(expr: str, base_pos: int = 0) -> List[_Tok]:
    tokens: List[_Tok] = []
    i = 0
    while True:
        match = _TOKEN_RE.match(expr, i)
        if match is None:
            rest = expr[i:].lstrip()
            if rest:
                raise TemplateError(
                    f"unexpected character {rest[0]!r} in expression", expr, base_pos + i
                )
            break
        kind = match.lastgroup or ""
        text = match.group(kind)
        pos = base_pos + match.start(kind)
        if kind == "number":
            tokens.append(_Tok("number", float(text) if "." in text else int(text), pos))
        elif kind == "string":
            tokens.append(_Tok("string", text[1:-1], pos))
        elif kind == "op":
            tokens.append(_Tok("op", text, pos))
        else:
            tokens.append(_Tok("name", text, pos))
        i = match.end()
    tokens.append(_Tok("eof", None, base_pos + len(expr)))
    return tokens


@dataclass
class _Node:
    kind: str
    value: Any = None
    children: List[Any] = field(default_factory=list)
    pos: int = 0


class _Parser:
    def __init__(self, tokens: List[_Tok]):
        self.tokens = tokens
        self.i = 0

    # -- helpers ---------------------------------------------------------
    def peek(self) -> _Tok:
        return self.tokens[self.i]

    def next(self) -> _Tok:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def at_op(self, *values: str) -> bool:
        tok = self.peek()
        return tok.kind == "op" and tok.value in values

    def at_name(self, *values: str) -> bool:
        tok = self.peek()
        return tok.kind == "name" and tok.value in values

    def expect_op(self, value: str) -> _Tok:
        if not self.at_op(value):
            tok = self.peek()
            raise TemplateError(f"expected {value!r} in expression", None, tok.pos)
        return self.next()

    # -- grammar ---------------------------------------------------------
    def parse(self) -> _Node:
        node = self.parse_or()
        if self.peek().kind != "eof":
            tok = self.peek()
            raise TemplateError(
                f"unexpected {tok.value!r} in expression", None, tok.pos
            )
        return node

    def parse_or(self) -> _Node:
        node = self.parse_and()
        while self.at_name("or"):
            pos = self.next().pos
            node = _Node("or", None, [node, self.parse_and()], pos)
        return node

    def parse_and(self) -> _Node:
        node = self.parse_not()
        while self.at_name("and"):
            pos = self.next().pos
            node = _Node("and", None, [node, self.parse_not()], pos)
        return node

    def parse_not(self) -> _Node:
        if self.at_name("not") and not self._not_in():
            pos = self.next().pos
            return _Node("not", None, [self.parse_not()], pos)
        return self.parse_compare()

    def _not_in(self) -> bool:
        """True when `not` belongs to a `not in` comparison further along."""
        j = self.i + 1
        return j < len(self.tokens) and self.tokens[j].kind == "name" and self.tokens[j].value == "in"

    def parse_compare(self) -> _Node:
        node = self.parse_additive()
        while True:
            tok = self.peek()
            if tok.kind == "op" and tok.value in ("<", "<=", ">", ">=", "==", "!="):
                self.next()
                node = _Node("cmp", tok.value, [node, self.parse_additive()], tok.pos)
            elif tok.kind == "name" and tok.value == "in":
                self.next()
                node = _Node("cmp", "in", [node, self.parse_additive()], tok.pos)
            elif tok.kind == "name" and tok.value == "not" and self._not_in():
                self.next()
                self.next()
                node = _Node("cmp", "not in", [node, self.parse_additive()], tok.pos)
            else:
                break
        return node

    def parse_additive(self) -> _Node:
        node = self.parse_multiplicative()
        while self.at_op("+", "-"):
            op = self.next()
            node = _Node("bin", op.value, [node, self.parse_multiplicative()], op.pos)
        return node

    def parse_multiplicative(self) -> _Node:
        node = self.parse_unary()
        while self.at_op("*", "/", "//", "%"):
            op = self.next()
            node = _Node("bin", op.value, [node, self.parse_unary()], op.pos)
        return node

    def parse_unary(self) -> _Node:
        if self.at_op("-"):
            op = self.next()
            return _Node("neg", None, [self.parse_unary()], op.pos)
        return self.parse_primary()

    def parse_primary(self) -> _Node:
        tok = self.peek()
        if tok.kind == "number":
            self.next()
            return self.parse_filter_chain(_Node("const", tok.value, [], tok.pos))
        if tok.kind == "string":
            self.next()
            return self.parse_filter_chain(_Node("const", tok.value, [], tok.pos))
        if tok.kind == "name" and tok.value in ("True", "False", "None"):
            self.next()
            literal = {"True": True, "False": False, "None": None}[tok.value]
            return self.parse_filter_chain(_Node("const", literal, [], tok.pos))
        if self.at_op("("):
            self.next()
            node = self.parse_or()
            self.expect_op(")")
            return self.parse_filter_chain(node)
        if tok.kind == "name":
            self.next()
            path = [tok.value]
            while self.at_op("."):
                self.next()
                attr = self.peek()
                if attr.kind != "name":
                    raise TemplateError("expected attribute name after '.'", None, attr.pos)
                self.next()
                path.append(attr.value)
            return self.parse_filter_chain(_Node("name", path, [], tok.pos))
        raise TemplateError(f"unexpected {tok.value!r} in expression", None, tok.pos)

    def parse_filter_chain(self, node: _Node) -> _Node:
        while self.at_op("|"):
            pipe = self.next()
            fname = self.peek()
            if fname.kind != "name":
                raise TemplateError("expected a filter name after '|'", None, fname.pos)
            self.next()
            args: List[_Node] = []
            if self.at_op("("):
                self.next()
                if not self.at_op(")"):
                    while True:
                        args.append(self.parse_or())
                        if self.at_op(","):
                            self.next()
                            continue
                        break
                self.expect_op(")")
            node = _Node("filter", fname.value, [node, *args], pipe.pos)
        return node


def parse_expression(expr: str, base_pos: int = 0) -> _Node:
    return _Parser(_lex(expr, base_pos)).parse()


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------

class Undefined:
    """A missing variable.

    It is falsy, so the common ``{{ value or 'fallback' }}`` and
    ``{% if not value %}`` idioms work.  Using it for anything else --
    printing, comparing, arithmetic, looping -- is an error, which keeps
    typos in templates from silently producing broken generated code.
    """

    __slots__ = ("name", "pos")

    def __init__(self, name: str, pos: int = 0):
        self.name = name
        self.pos = pos

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<undefined {self.name}>"

    def fail(self) -> "TemplateError":
        return TemplateError(f"undefined variable {self.name!r}", None, self.pos)

    def _reject(self) -> Any:
        raise self.fail()

    __str__ = _reject
    __eq__ = _reject
    __ne__ = _reject
    __lt__ = _reject
    __le__ = _reject
    __gt__ = _reject
    __ge__ = _reject
    __add__ = _reject
    __radd__ = _reject
    __sub__ = _reject
    __mul__ = _reject
    __truediv__ = _reject
    __mod__ = _reject
    __neg__ = _reject
    __iter__ = _reject
    __len__ = _reject


def _check_defined(value: Any) -> Any:
    if isinstance(value, Undefined):
        raise value.fail()
    return value


_MISSING = object()


class Context:
    """A stack of scopes used while rendering."""

    def __init__(self, values: Optional[Dict[str, Any]] = None):
        self._scopes: List[Dict[str, Any]] = [dict(values or {})]

    def push(self, values: Optional[Dict[str, Any]] = None) -> None:
        self._scopes.append(dict(values or {}))

    def pop(self) -> None:
        if len(self._scopes) > 1:
            self._scopes.pop()

    def set(self, name: str, value: Any) -> None:
        self._scopes[-1][name] = value

    def get(self, path: Sequence[str]) -> Any:
        name = path[0]
        for scope in reversed(self._scopes):
            if name in scope:
                value = scope[name]
                break
        else:
            raise KeyError(name)
        for part in path[1:]:
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif hasattr(value, part):
                value = getattr(value, part)
            else:
                raise KeyError(".".join(path))
        return value

    def has(self, name: str) -> bool:
        return any(name in scope for scope in self._scopes)


def _truthy(value: Any) -> bool:
    return bool(value)


def evaluate(node: _Node, ctx: Context) -> Any:
    kind = node.kind
    if kind == "const":
        return node.value
    if kind == "name":
        try:
            return ctx.get(node.value)
        except KeyError:
            return Undefined(".".join(node.value), node.pos)
    if kind == "filter":
        fname = node.value
        if fname not in FILTERS:
            raise TemplateError(f"unknown filter {fname!r}", None, node.pos)
        args = [evaluate(child, ctx) for child in node.children]
        if fname != "default":
            for arg in args:
                _check_defined(arg)
        try:
            return FILTERS[fname](*args)
        except TypeError as exc:
            raise TemplateError(f"bad arguments for filter {fname!r}: {exc}", None, node.pos) from None
    if kind == "neg":
        return -_check_defined(evaluate(node.children[0], ctx))
    if kind == "not":
        return not _truthy(evaluate(node.children[0], ctx))
    if kind == "and":
        left = evaluate(node.children[0], ctx)
        return evaluate(node.children[1], ctx) if _truthy(left) else left
    if kind == "or":
        left = evaluate(node.children[0], ctx)
        return left if _truthy(left) else evaluate(node.children[1], ctx)
    if kind == "cmp":
        left = _check_defined(evaluate(node.children[0], ctx))
        right = _check_defined(evaluate(node.children[1], ctx))
        op = node.value
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == "in":
            return left in right
        if op == "not in":
            return left not in right
        try:
            if op == "<":
                return left < right
            if op == "<=":
                return left <= right
            if op == ">":
                return left > right
            return left >= right
        except TypeError:
            raise TemplateError(f"cannot compare {left!r} with {right!r}", None, node.pos) from None
    if kind == "bin":
        left = _check_defined(evaluate(node.children[0], ctx))
        right = _check_defined(evaluate(node.children[1], ctx))
        op = node.value
        try:
            if op == "+":
                return left + right
            if op == "-":
                return left - right
            if op == "*":
                return left * right
            if op == "/":
                return left / right
            if op == "//":
                return left // right
            return left % right
        except ZeroDivisionError:
            raise TemplateError("division by zero in expression", None, node.pos) from None
        except TypeError:
            raise TemplateError(
                f"cannot apply {op!r} to {left!r} and {right!r}", None, node.pos
            ) from None
    raise TemplateError(f"internal error: unknown node {kind!r}", None, node.pos)


# --------------------------------------------------------------------------
# Template tokenizer / parser
# --------------------------------------------------------------------------

_TAG_OPEN = ("{{", "{%", "{#")


@dataclass
class _TNode:
    kind: str  # text | expr | if | for
    text: str = ""
    node: Optional[_Node] = None
    target: str = ""
    branches: List[Tuple[Optional[_Node], List["_TNode"]]] = field(default_factory=list)
    body: List["_TNode"] = field(default_factory=list)
    pos: int = 0


_BLOCK_END = {"endif", "endfor", "else", "elif"}


def _find_tag(source: str, start: int) -> Tuple[int, str]:
    best = (-1, "")
    for opener in _TAG_OPEN:
        idx = source.find(opener, start)
        if idx != -1 and (best[0] == -1 or idx < best[0]):
            best = (idx, opener)
    return best


def _parse_block(source: str, body: str, pos: int) -> Tuple[str, str]:
    """Return (keyword, rest) for the contents of a ``{% ... %}`` tag."""
    text = body.strip()
    if not text:
        raise TemplateError("empty block tag", source, pos)
    parts = text.split(None, 1)
    keyword = parts[0]
    return keyword, parts[1].strip() if len(parts) > 1 else ""


def _tokenize(source: str) -> List[_TNode]:
    """Turn template source into a *flat* tag stream.

    Whitespace control is applied here; nesting is resolved later by
    :func:`_flatten`.
    """
    nodes: List[_TNode] = []
    pending_newline_strip = False  # a block tag just ended -> drop one leading \n
    i = 0
    length = len(source)
    while i < length:
        idx, opener = _find_tag(source, i)
        if idx == -1:
            _append_text(nodes, source[i:], pending_newline_strip, False)
            pending_newline_strip = False
            break

        raw_text = source[i:idx]
        if opener == "{#":
            end = source.find("#}", idx + 2)
            if end == -1:
                raise TemplateError("unterminated comment (missing '#}')", source, idx)
            _append_text(nodes, raw_text, pending_newline_strip, False)
            pending_newline_strip = False
            i = end + 2
            continue

        closer = {"{{": "}}", "{%": "%}"}[opener]
        end = source.find(closer, idx + 2)
        if end == -1:
            raise TemplateError(f"unterminated tag (missing {closer!r})", source, idx)
        body = source[idx + 2 : end]
        strip_left = body.startswith("-")
        strip_right = body.endswith("-")
        if strip_left:
            body = body[1:]
        if strip_right:
            body = body[:-1]
        body_start = idx + 3 if strip_left else idx + 2

        _append_text(
            nodes,
            raw_text,
            pending_newline_strip,
            strip_left,
            lstrip_line=(opener == "{%" and not strip_left),
        )
        pending_newline_strip = False

        if opener == "{{":
            expr = parse_expression(body, body_start)
            nodes.append(_TNode("expr", node=expr, pos=idx))
            i = end + 2
            if strip_right:
                i = _skip_spaces(source, i)
            continue

        keyword, rest = _parse_block(source, body, idx)
        if keyword == "set":
            match = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.+)$", rest, re.S)
            if not match:
                raise TemplateError(
                    "malformed set tag (expected {% set name = expression %})", source, idx
                )
            nodes.append(
                _TNode(
                    "set",
                    text=match.group(1),
                    node=parse_expression(match.group(2), idx),
                    pos=idx,
                )
            )
            i = end + 2
            if strip_right:
                i = _skip_spaces(source, i)
            pending_newline_strip = True
            continue
        if keyword in ("if", "elif", "else", "endif", "for", "endfor"):
            nodes.append(_TNode(keyword, text=rest, pos=idx))
            i = end + 2
            if strip_right:
                i = _skip_spaces(source, i)
            pending_newline_strip = True
            continue
        raise TemplateError(f"unknown block tag {keyword!r}", source, idx)
    return nodes


def _skip_spaces(source: str, i: int) -> int:
    while i < len(source) and source[i] in " \t":
        i += 1
    return i


def _append_text(
    nodes: List[_TNode],
    text: str,
    strip_leading_newline: bool,
    strip_trailing: bool,
    lstrip_line: bool = False,
) -> None:
    """Append a text run, applying the whitespace-control rules.

    ``lstrip_line`` implements the classic *lstrip_blocks* behaviour: when a
    block tag starts a line, the indentation in front of it is dropped so
    generated code keeps its own indentation.
    """
    if strip_leading_newline and text.startswith("\n"):
        text = text[1:]
    if lstrip_line and text:
        newline = text.rfind("\n")
        if newline == -1:
            if not nodes and not text.strip():
                text = ""
        elif not text[newline + 1 :].strip():
            text = text[: newline + 1]
    if strip_trailing:
        text = text.rstrip()
    if text:
        if nodes and nodes[-1].kind == "text":
            nodes[-1].text += text
        else:
            nodes.append(_TNode("text", text=text))


def _flatten(nodes: List[_TNode], source: str) -> List[_TNode]:
    """Turn the flat tag stream into a tree of text / expr / if / for nodes."""
    out: List[_TNode] = []
    i = 0
    while i < len(nodes):
        node = nodes[i]
        if node.kind in ("text", "expr", "set"):
            out.append(node)
            i += 1
            continue
        if node.kind == "if":
            block, i = _collect(nodes, i, source, "if")
            out.append(block)
            continue
        if node.kind == "for":
            block, i = _collect(nodes, i, source, "for")
            out.append(block)
            continue
        raise TemplateError(
            f"unexpected {{% {node.kind} %}} outside of a block", source, node.pos
        )
    return out


def _collect(nodes: List[_TNode], start: int, source: str, kind: str) -> Tuple[_TNode, int]:
    opener = nodes[start]
    depth = 1
    i = start + 1
    if kind == "for":
        match = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)\s+in\s+(.+)$", opener.text, re.S)
        if not match:
            raise TemplateError(
                "malformed for tag (expected {% for x in items %})", source, opener.pos
            )
        block = _TNode("for", target=match.group(1), pos=opener.pos)
        block.node = parse_expression(match.group(2), opener.pos)
        current: List[_TNode] = block.body
        while i < len(nodes):
            node = nodes[i]
            if node.kind == "for":
                child, i = _collect(nodes, i, source, "for")
                current.append(child)
                continue
            if node.kind == "if":
                child, i = _collect(nodes, i, source, "if")
                current.append(child)
                continue
            if node.kind == "endfor":
                if depth != 1:
                    depth -= 1
                i += 1
                return block, i
            if node.kind == "endif":
                raise TemplateError("unexpected {% endif %} inside {% for %}", source, node.pos)
            current.append(node)
            i += 1
        raise TemplateError("unterminated {% for %} (missing {% endfor %})", source, opener.pos)

    # if / elif / else
    block = _TNode("if", pos=opener.pos)
    branch_expr = parse_expression(opener.text, opener.pos) if opener.text else None
    if opener.text == "":
        raise TemplateError("{% if %} requires a condition", source, opener.pos)
    body: List[_TNode] = []
    block.branches.append((branch_expr, body))
    while i < len(nodes):
        node = nodes[i]
        if node.kind == "if":
            child, i = _collect(nodes, i, source, "if")
            body.append(child)
            continue
        if node.kind == "for":
            child, i = _collect(nodes, i, source, "for")
            body.append(child)
            continue
        if node.kind == "else":
            if len(block.branches) > 1 and block.branches[-1][0] is None:
                raise TemplateError("duplicate {% else %}", source, node.pos)
            i += 1
            body = []
            block.branches.append((None, body))
            continue
        if node.kind == "elif":
            i += 1
            body = []
            block.branches.append((parse_expression(node.text, node.pos), body))
            continue
        if node.kind == "endif":
            i += 1
            return block, i
        body.append(node)
        i += 1
    raise TemplateError("unterminated {% if %} (missing {% endif %})", source, opener.pos)


def _render_nodes(nodes: List[_TNode], ctx: Context, out: List[str], source: str) -> None:
    for node in nodes:
        if node.kind == "text":
            out.append(node.text)
        elif node.kind == "set":
            try:
                value = evaluate(node.node, ctx)
            except TemplateError as exc:
                raise TemplateError(str(exc), source, node.pos) from None
            if isinstance(value, Undefined):
                raise TemplateError(str(value.fail()), source, node.pos) from None
            ctx.set(node.text, value)
        elif node.kind == "expr":
            try:
                value = evaluate(node.node, ctx)
            except TemplateError as exc:
                raise TemplateError(str(exc), source, node.pos) from None
            if isinstance(value, Undefined):
                raise TemplateError(str(value.fail()), source, node.pos) from None
            out.append("" if value is None else str(value))
        elif node.kind == "if":
            for expr, body in node.branches:
                if expr is None:
                    _render_nodes(body, ctx, out, source)
                    break
                try:
                    result = evaluate(expr, ctx)
                except TemplateError as exc:
                    raise TemplateError(str(exc), source, node.pos) from None
                if _truthy(result):
                    _render_nodes(body, ctx, out, source)
                    break
        elif node.kind == "for":
            try:
                iterable = evaluate(node.node, ctx)
            except TemplateError as exc:
                raise TemplateError(str(exc), source, node.pos) from None
            if isinstance(iterable, Undefined):
                raise TemplateError(str(iterable.fail()), source, node.pos) from None
            if iterable is None:
                iterable = []
            if isinstance(iterable, (str, bytes)) or not isinstance(iterable, Iterable):
                raise TemplateError("cannot loop over a non-sequence value", source, node.pos)
            items = list(iterable)
            for index, item in enumerate(items):
                ctx.push(
                    {
                        node.target: item,
                        "loop": {
                            "index": index + 1,
                            "index0": index,
                            "first": index == 0,
                            "last": index == len(items) - 1,
                            "length": len(items),
                        },
                    }
                )
                try:
                    _render_nodes(node.body, ctx, out, source)
                finally:
                    ctx.pop()
        else:  # pragma: no cover - parser never emits other kinds at top level
            raise TemplateError(f"internal error: cannot render {node.kind!r}", source, node.pos)


class Template:
    """A compiled template."""

    def __init__(self, source: str, name: str = "<template>"):
        self.source = source
        self.name = name
        self.nodes = _flatten(_tokenize(source), source)

    def render(self, values: Optional[Dict[str, Any]] = None, ctx: Optional[Context] = None) -> str:
        context = ctx if ctx is not None else Context(values)
        out: List[str] = []
        _render_nodes(self.nodes, context, out, self.source)
        return "".join(out)


_CACHE: Dict[Tuple[str, str], Template] = {}


def compile_template(source: str, name: str = "<template>", *, cache: bool = True) -> Template:
    """Compile (and optionally memoise) a template."""
    key = (name, source)
    if cache and key in _CACHE:
        return _CACHE[key]
    template = Template(source, name)
    if cache:
        _CACHE[key] = template
    return template


def render(source: str, values: Optional[Dict[str, Any]] = None, name: str = "<template>") -> str:
    """One-shot render of ``source`` with ``values``."""
    return compile_template(source, name, cache=False).render(values)


def used_variables(source: str) -> List[str]:
    """Return the sorted set of top-level variable names a template references."""
    found: set[str] = set()

    def walk(node: _Node) -> None:
        if node.kind == "name" and node.value:
            found.add(node.value[0])
        for child in node.children:
            if isinstance(child, _Node):
                walk(child)

    for tnode in _flatten(_tokenize(source), source):
        _collect_exprs(tnode, walk)
    return sorted(found)


def _collect_exprs(node: _TNode, walk: Callable[[_Node], None]) -> None:
    if node.node is not None:
        walk(node.node)
    for child in node.body:
        _collect_exprs(child, walk)
    for _expr, branch in node.branches:
        if _expr is not None:
            walk(_expr)
        for child in branch:
            _collect_exprs(child, walk)
