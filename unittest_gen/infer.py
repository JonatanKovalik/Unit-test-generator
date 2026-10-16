"""Heuristic type inference and candidate test-value generation.

Each parameter is assigned an *inferred kind* ('int', 'str', 'list', ...)
derived from, in priority order:

1. Type annotations (``int``, ``Optional[str]``, ``List[float]``, ...).
2. Default-value literals.
3. ``isinstance()`` checks inside the function body.
4. Method calls used on the parameter (``x.split()`` -> str, ...).
5. Operators applied to the parameter (``x + 1`` -> int, ...).
6. Naming conventions (``count`` -> int, ``items`` -> list, ...).

Every kind maps to a table of "happy path" values and "edge case" values
(None, empty structures, boundary magnitudes, unicode, ...).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from .analyzer import FunctionInfo, ParamInfo

# ---------------------------------------------------------------------------
# Candidate values
# ---------------------------------------------------------------------------


@dataclass
class ArgValue:
    """A concrete candidate argument: its source expression and the object."""

    expr: str
    value: Any
    label: str = ""


def _v(expr: str, value: Any, label: str = "") -> ArgValue:
    return ArgValue(expr=expr, value=value, label=label)


#: kind -> (happy values, edge values)
_VALUE_TABLE = {
    "int": (
        [_v("2", 2), _v("10", 10)],
        [
            _v("0", 0, "zero"),
            _v("-1", -1, "negative"),
            _v("1", 1, "one"),
            _v("2**63 - 1", 2**63 - 1, "max int64"),
            _v("-2**63", -(2**63), "min int64"),
            _v("None", None, "None"),
        ],
    ),
    "float": (
        [_v("2.5", 2.5), _v("0.5", 0.5)],
        [
            _v("0.0", 0.0, "zero"),
            _v("-1.0", -1.0, "negative"),
            _v("1e308", 1e308, "near max float"),
            _v("None", None, "None"),
        ],
    ),
    "str": (
        [_v("'hello'", "hello"), _v("'alice'", "alice")],
        [
            _v("''", "", "empty string"),
            _v("' '", " ", "whitespace"),
            _v("'x' * 100", "x" * 100, "long string"),
            _v("'héllo 🌍'", "héllo 🌍", "unicode"),
            _v("None", None, "None"),
        ],
    ),
    "bool": (
        [_v("True", True), _v("False", False)],
        [_v("None", None, "None")],
    ),
    "list": (
        [_v("[1, 2, 3]", [1, 2, 3]), _v("['a', 'b']", ["a", "b"])],
        [
            _v("[]", [], "empty list"),
            _v("[0]", [0], "single element"),
            _v("[None]", [None], "None element"),
            _v("list(range(100))", list(range(100)), "large list"),
            _v("None", None, "None"),
        ],
    ),
    "dict": (
        [_v("{'a': 1, 'b': 2}", {"a": 1, "b": 2})],
        [
            _v("{}", {}, "empty dict"),
            _v("{'key': None}", {"key": None}, "None value"),
            _v("None", None, "None"),
        ],
    ),
    "tuple": (
        [_v("(1, 2)", (1, 2))],
        [_v("()", (), "empty tuple"), _v("None", None, "None")],
    ),
    "set": (
        [_v("{1, 2, 3}", {1, 2, 3})],
        [_v("set()", set(), "empty set"), _v("None", None, "None")],
    ),
    "bytes": (
        [_v("b'data'", b"data")],
        [_v("b''", b"", "empty bytes"), _v("None", None, "None")],
    ),
    "unknown": (
        [_v("1", 1), _v("'test'", "test"), _v("[1, 2]", [1, 2])],
        [_v("None", None, "None"), _v("0", 0, "zero"), _v("''", "", "empty string")],
    ),
}

# ---------------------------------------------------------------------------
# Name heuristics
# ---------------------------------------------------------------------------

_NAME_RULES = [
    (
        "bool",
        ("is_", "has_", "can_", "should_", "flag", "enabled", "active", "verbose",
         "debug", "strict", "recursive", "force", "dry_run", "ascending"),
    ),
    (
        "int",
        ("num", "count", "index", "idx", "size", "len", "amount", "qty",
         "quantity", "age", "year", "port", "width", "height", "depth", "level",
         "retries", "timeout", "limit", "offset", "total", "n"),
    ),
    (
        "float",
        ("rate", "ratio", "factor", "score", "percent", "threshold", "weight",
         "latitude", "longitude", "lat", "lng", "price", "balance", "temperature"),
    ),
    (
        "str",
        ("name", "text", "str", "email", "url", "uri", "path", "file", "filename",
         "key", "word", "msg", "message", "title", "label", "slug", "username",
         "password", "token", "host", "domain", "pattern", "query", "format",
         "encoding", "sep", "separator", "delimiter", "prefix", "suffix"),
    ),
    (
        "list",
        ("items", "list", "values", "array", "seq", "sequence", "elements",
         "names", "numbers", "records", "rows", "keys", "entries", "collection",
         "iterable", "args_list", "queue", "stack", "batch"),
    ),
    (
        "dict",
        ("dict", "map", "mapping", "data", "config", "options", "params",
         "settings", "meta", "metadata", "payload", "headers", "context",
         "attributes", "props", "lookup", "table", "cache"),
    ),
]

_STR_METHODS = {
    "split", "rsplit", "splitlines", "strip", "lstrip", "rstrip", "lower",
    "upper", "title", "capitalize", "casefold", "swapcase", "replace",
    "startswith", "endswith", "join", "format", "find", "rfind", "encode",
    "decode", "isnumeric", "isalpha", "isdigit", "isalnum", "isspace",
    "zfill", "center", "ljust", "rjust", "partition", "rpartition",
    "removeprefix", "removesuffix", "format_map", "maketrans",
}
_LIST_METHODS = {
    "append", "extend", "insert", "remove", "sort", "reverse",
}
_DICT_METHODS = {
    "get", "keys", "values", "items", "setdefault", "update", "popitem",
    "fromkeys",
}
_SET_METHODS = {
    "add", "discard", "union", "intersection", "difference", "issubset",
    "issuperset", "symmetric_difference", "isdisjoint",
}
# Methods shared across types give no signal (pop, clear, copy, count, index).

_BUILTIN_TYPE_NAMES = {
    "int": "int", "float": "float", "str": "str", "bool": "bool",
    "list": "list", "dict": "dict", "tuple": "tuple", "set": "set",
    "bytes": "bytes",
}

# ---------------------------------------------------------------------------
# Annotation / default resolution
# ---------------------------------------------------------------------------


def _strip_wrappers(annotation: str) -> Tuple[str, bool]:
    """Return (core type name, is_optional)."""
    text = annotation.strip().strip('"').strip("'")
    optional = False
    for wrapper in ("Optional", "typing.Optional"):
        if text.startswith(wrapper + "[") and text.endswith("]"):
            text = text[len(wrapper) + 1 : -1]
            optional = True
    if " | " in text:
        parts = [p.strip() for p in text.split(" | ")]
        non_none = [p for p in parts if p not in ("None", "NoneType")]
        if len(non_none) != len(parts):
            optional = True
        text = non_none[0] if len(non_none) == 1 else ""
    if text.startswith("Union[") or text.startswith("typing.Union["):
        inner = text[text.index("[") + 1 : -1]
        parts = [p.strip() for p in inner.split(",")]
        non_none = [p for p in parts if p not in ("None", "NoneType")]
        if len(non_none) != len(parts):
            optional = True
        text = non_none[0] if len(non_none) == 1 else ""
    return text, optional


def _kind_from_annotation(annotation: str) -> Tuple[str, bool]:
    text, optional = _strip_wrappers(annotation)
    lowered = text.lower()
    for container in ("list", "sequence", "iterable", "mutablesequence"):
        if lowered.startswith(container):
            return "list", optional
    for container in ("dict", "mapping", "mutablemapping"):
        if lowered.startswith(container):
            return "dict", optional
    for container in ("tuple",):
        if lowered.startswith(container):
            return "tuple", optional
    for container in ("set", "frozenset", "abstractset"):
        if lowered.startswith(container):
            return "set", optional
    base = lowered.split("[")[0].split(".")[-1]
    return _BUILTIN_TYPE_NAMES.get(base, "unknown"), optional


def _kind_from_default(param: ParamInfo) -> Optional[str]:
    if param.default_node is None:
        return None
    try:
        value = ast.literal_eval(param.default_node)
    except (ValueError, SyntaxError):
        return None
    if value is None:
        return None
    for builtin, kind in _BUILTIN_TYPE_NAMES.items():
        if type(value).__name__ == builtin:
            return kind
    return None


# ---------------------------------------------------------------------------
# Body-usage analysis
# ---------------------------------------------------------------------------


class _UsageVisitor(ast.NodeVisitor):
    """Collect usage evidence for each parameter name inside a function body."""

    def __init__(self, param_names):
        self.names = set(param_names)
        self.methods = {n: set() for n in self.names}
        self.isinstance_kinds = {n: set() for n in self.names}
        self.numeric = set()
        self.stringy = set()
        self.iterated = set()
        self.subscripted_str = set()

    def _name_of(self, node):
        return node.id if isinstance(node, ast.Name) and node.id in self.names else None

    def visit_Call(self, node):
        # param.method(...)
        if isinstance(node.func, ast.Attribute):
            name = self._name_of(node.func.value)
            if name:
                self.methods[name].add(node.func.attr)
        # isinstance(param, T)
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and len(node.args) == 2
        ):
            name = self._name_of(node.args[0])
            if name:
                target = node.args[1]
                targets = target.elts if isinstance(target, ast.Tuple) else [target]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id in _BUILTIN_TYPE_NAMES:
                        self.isinstance_kinds[name].add(_BUILTIN_TYPE_NAMES[t.id])
        # len(param)
        if isinstance(node.func, ast.Name) and node.func.id == "len" and node.args:
            name = self._name_of(node.args[0])
            if name:
                self.iterated.add(name)
        self.generic_visit(node)

    def visit_For(self, node):
        name = self._name_of(node.iter)
        if name:
            self.iterated.add(name)
        self.generic_visit(node)

    def visit_Subscript(self, node):
        name = self._name_of(node.value)
        if name:
            slice_node = node.slice
            if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                self.subscripted_str.add(name)
            else:
                self.iterated.add(name)
        self.generic_visit(node)

    def visit_BinOp(self, node):
        for side in (node.left, node.right):
            name = self._name_of(side)
            other = node.right if side is node.left else node.left
            if name and isinstance(other, ast.Constant):
                if isinstance(other.value, bool):
                    continue
                if isinstance(other.value, (int, float)):
                    self.numeric.add(name)
                elif isinstance(other.value, str):
                    self.stringy.add(name)
        self.generic_visit(node)


def _kind_from_usage(func: FunctionInfo, param: ParamInfo, visitor: _UsageVisitor) -> Optional[str]:
    name = param.name
    kinds = visitor.isinstance_kinds.get(name, set())
    if len(kinds) == 1:
        return next(iter(kinds))
    methods = visitor.methods.get(name, set())
    if methods:
        categories = set()
        if methods & _STR_METHODS:
            categories.add("str")
        if methods & _LIST_METHODS:
            categories.add("list")
        if methods & _DICT_METHODS:
            categories.add("dict")
        if methods & _SET_METHODS:
            categories.add("set")
        if len(categories) == 1:
            return next(iter(categories))
    if name in visitor.subscripted_str:
        return "dict"
    if name in visitor.iterated:
        return "list"
    if name in visitor.stringy:
        return "str"
    if name in visitor.numeric:
        return "int"
    return None


def _kind_from_name(name: str) -> Optional[str]:
    lowered = name.lower()
    for kind, tokens in _NAME_RULES:
        for token in tokens:
            if token.endswith("_"):
                if lowered.startswith(token):
                    return kind
            elif token in lowered.split("_") or lowered == token:
                return kind
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class ParamPlan:
    """Inference result for one parameter."""

    param: ParamInfo
    kind: str
    optional: bool
    happy: List[ArgValue]
    edges: List[ArgValue]
    source: str  # how the kind was inferred, for diagnostics


@dataclass
class FunctionPlan:
    func: FunctionInfo
    params: List[ParamPlan]


def infer_param(param: ParamInfo, func: FunctionInfo, usage: _UsageVisitor) -> ParamPlan:
    kind, optional, source = "unknown", False, "fallback"
    if param.annotation:
        kind, optional = _kind_from_annotation(param.annotation)
        if kind != "unknown":
            source = "annotation"
    if kind == "unknown":
        from_default = _kind_from_default(param)
        if from_default:
            kind, source = from_default, "default value"
            optional = param.default in ("None",)
    if kind == "unknown" and param.annotation:
        optional = optional or _strip_wrappers(param.annotation)[1]
    if kind == "unknown":
        from_usage = _kind_from_usage(func, param, usage)
        if from_usage:
            kind, source = from_usage, "body usage"
    if kind == "unknown":
        from_name = _kind_from_name(param.name)
        if from_name:
            kind, source = from_name, "name heuristic"

    happy, edges = _VALUE_TABLE.get(kind, _VALUE_TABLE["unknown"])
    happy = [ArgValue(v.expr, v.value, v.label) for v in happy]
    edge_list = [ArgValue(v.expr, v.value, v.label) for v in edges]
    if optional and not any(e.value is None for e in edge_list):
        edge_list.append(ArgValue("None", None, "None"))
    if param.has_default:
        edge_list = [e for e in edge_list if e.value is not None or optional]
    return ParamPlan(param=param, kind=kind, optional=optional, happy=happy, edges=edge_list, source=source)


def plan_function(func: FunctionInfo) -> FunctionPlan:
    names = [p.name for p in func.params]
    visitor = _UsageVisitor(names)
    if func.node is not None:
        visitor.visit(func.node)
    plans = []
    for param in func.params:
        if param.kind in ("var_positional", "var_keyword"):
            continue  # variadic params are left empty
        plans.append(infer_param(param, func, visitor))
    return FunctionPlan(func=func, params=plans)


def plan_all(functions: List[FunctionInfo]) -> List[FunctionPlan]:
    return [plan_function(f) for f in functions]
