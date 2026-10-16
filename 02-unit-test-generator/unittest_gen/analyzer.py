"""Static analysis of Python source files.

Extracts public (exported) function signatures using the ``ast`` module:
parameter names, kinds, defaults, annotations, return annotations and
docstrings.  Class ``@staticmethod`` members are included because they can be
invoked without constructing an instance.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class ParamInfo:
    """One parameter of an exported function."""

    name: str
    kind: str  # 'positional' | 'keyword_only' | 'var_positional' | 'var_keyword'
    annotation: Optional[str] = None
    default: Optional[str] = None
    default_node: Optional[ast.expr] = field(default=None, repr=False)

    @property
    def has_default(self) -> bool:
        return self.default is not None

    @property
    def required(self) -> bool:
        return self.kind in ("positional", "keyword_only") and not self.has_default


@dataclass
class FunctionInfo:
    """An exported, testable function."""

    name: str
    qualname: str  # 'name' or 'Class.name' for staticmethods
    lineno: int
    params: List[ParamInfo]
    returns: Optional[str] = None
    is_async: bool = False
    docstring: Optional[str] = None
    call_target: str = ""  # expression used to call it, e.g. 'add' or 'Calc.add'
    node: ast.AST = field(default=None, repr=False)

    @property
    def signature(self) -> str:
        parts = []
        for p in self.params:
            if p.kind == "var_positional":
                parts.append(f"*{p.name}")
            elif p.kind == "var_keyword":
                parts.append(f"**{p.name}")
            else:
                text = p.name
                if p.annotation:
                    text += f": {p.annotation}"
                if p.has_default:
                    text += f" = {p.default}"
                parts.append(text)
        rendered = f"{self.qualname}({', '.join(parts)})"
        if self.returns:
            rendered += f" -> {self.returns}"
        return rendered


def _unparse(node: Optional[ast.expr]) -> Optional[str]:
    return ast.unparse(node) if node is not None else None


def _extract_params(args: ast.arguments) -> List[ParamInfo]:
    params: List[ParamInfo] = []
    positional = list(args.posonlyargs) + list(args.args)
    defaults: List[Optional[ast.expr]] = [None] * (len(positional) - len(args.defaults))
    defaults += list(args.defaults)
    for arg, default in zip(positional, defaults):
        params.append(
            ParamInfo(
                name=arg.arg,
                kind="positional",
                annotation=_unparse(arg.annotation),
                default=_unparse(default),
                default_node=default,
            )
        )
    if args.vararg is not None:
        params.append(ParamInfo(name=args.vararg.arg, kind="var_positional"))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        params.append(
            ParamInfo(
                name=arg.arg,
                kind="keyword_only",
                annotation=_unparse(arg.annotation),
                default=_unparse(default),
                default_node=default,
            )
        )
    if args.kwarg is not None:
        params.append(ParamInfo(name=args.kwarg.arg, kind="var_keyword"))
    return params


def _declared_exports(tree: ast.Module) -> Optional[set]:
    """Return the names in ``__all__`` when it is a simple literal, else None."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            return None
        if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
            return set(value)
    return None


def _is_public(name: str, exports: Optional[set]) -> bool:
    if exports is not None:
        return name in exports
    return not name.startswith("_")


def _make_function(node, qualname: str, exports: Optional[set]) -> FunctionInfo:
    return FunctionInfo(
        name=node.name,
        qualname=qualname,
        lineno=node.lineno,
        params=_extract_params(node.args),
        returns=_unparse(node.returns),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        docstring=ast.get_docstring(node),
        call_target=qualname,
        node=node,
    )


def analyze_source(source: str, filename: str = "<string>") -> List[FunctionInfo]:
    """Parse *source* and return its public functions in declaration order."""
    tree = ast.parse(source, filename=filename)
    exports = _declared_exports(tree)
    functions: List[FunctionInfo] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_public(node.name, exports):
                functions.append(_make_function(node, node.name, exports))
        elif isinstance(node, ast.ClassDef) and _is_public(node.name, exports):
            for child in node.body:
                if not isinstance(child, ast.FunctionDef):
                    continue
                if not child.name.startswith("_") and any(
                    isinstance(d, ast.Name) and d.id == "staticmethod"
                    for d in child.decorator_list
                ):
                    qualname = f"{node.name}.{child.name}"
                    functions.append(_make_function(child, qualname, exports))
    return functions


def analyze_file(path: Path) -> List[FunctionInfo]:
    return analyze_source(path.read_text(encoding="utf-8"), filename=str(path))
