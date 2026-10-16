"""Renders a pytest test file from inferred plans and probed outcomes.

Two rendering modes:

* **Snapshot mode** (default): the target module was imported and every
  candidate call was executed.  Assertions encode the observed behaviour —
  exact return literals where ``repr()`` round-trips, ``isinstance`` checks
  otherwise, and ``pytest.raises`` for rejections.
* **Scaffold mode** (``--no-execute`` or import failure): the same structure
  is emitted, but assertions describe the *expected* contract (a non-None
  result, or a common validation error) with TODO markers for review.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass, field
from typing import Dict, List, Set

from .analyzer import FunctionInfo
from .executor import (
    ModuleLoadError,
    ProbeCase,
    bind,
    build_cases,
    load_module,
    probe,
    resolve_callable,
)
from .infer import FunctionPlan

MAX_EDGE_ROWS = 8


@dataclass
class GeneratedFile:
    """Result of a generation run."""

    test_source: str
    n_functions: int
    n_tests: int
    executed: bool
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _camel(name: str) -> str:
    return "".join(part.capitalize() or "_" for part in name.replace(".", "_").split("_"))


def _expected_col(param_names: List[str]) -> str:
    col = "expected"
    while col in param_names:
        col = "_" + col
    return col


def _param_names(plan: FunctionPlan) -> List[str]:
    return [p.param.name for p in plan.params]


def _case_id(case: ProbeCase) -> str:
    if case.edge_param:
        return f"{case.edge_param}={case.label}"
    return case.label or case.call_expr


def _parametrize(names: List[str], rows: List[str], indent: str) -> List[str]:
    """Render a @pytest.mark.parametrize block."""
    cols = ", ".join(names)
    lines = [f'{indent}@pytest.mark.parametrize(']
    lines.append(f'{indent}    "{cols}",')
    lines.append(f"{indent}    [")
    lines.extend(f"{indent}        {row}" for row in rows)
    lines.append(f"{indent}    ],")
    lines.append(f"{indent})")
    return lines


# ---------------------------------------------------------------------------
# Snapshot-mode rendering
# ---------------------------------------------------------------------------


def _snapshot_assertion(call: str, outcome, indent: str, extra_imports: Set[str],
                        importable: Set[str]) -> List[str]:
    """Assertion lines for one observed outcome."""
    if outcome is None:
        return [f"{indent}pytest.skip(\"call could not be probed\")",
                f"{indent}{call}"]
    if outcome.status == "raise":
        note = ""
        if outcome.exc_name == "Exception" and outcome.exc_qualname:
            note = f"  # observed {outcome.exc_qualname}"
        return [f"{indent}with pytest.raises({outcome.exc_name}):{note}",
                f"{indent}    {call}"]
    if outcome.literal is not None:
        return [f"{indent}assert {call} == {outcome.literal}"]
    cls = outcome.class_name
    if cls and (cls in dir(builtins) or cls in importable):
        if cls in importable:
            extra_imports.add(cls)
        note = ""
        if not outcome.deterministic:
            note = "  # non-deterministic result; asserting type only"
        return [f"{indent}result = {call}",
                f"{indent}assert isinstance(result, {cls}){note}"]
    return [f"{indent}assert {call} is not None  # opaque result; refine manually"]


def _render_snapshot(plan: FunctionPlan, cases: List[ProbeCase], importable: Set[str],
                     extra_imports: Set[str]) -> List[str]:
    ind = "    "  # one level inside the Test class
    blocks: List[str] = []
    names = _param_names(plan)
    call_prefix = plan.func.call_target
    is_async = plan.func.is_async

    def invoke(expr: str) -> str:
        return f"asyncio.run({expr})" if is_async else expr

    def call_of(case: ProbeCase) -> str:
        return invoke(f"{call_prefix}({case.call_expr})")

    happy, raises, returns_lit, returns_typed, skipped = [], [], [], [], []
    for case in cases:
        out = case.outcome
        if case is cases[0]:
            happy.append(case)
        elif out is None or out.status in ("timeout", "error"):
            skipped.append(case)
        elif out.status == "raise":
            raises.append(case)
        elif out.literal is not None and out.deterministic:
            returns_lit.append(case)
        else:
            returns_typed.append(case)

    # --- happy path ---
    h = happy[0]
    lines = [f"{ind}def test_happy_path(self):"]
    if h.outcome is None:
        desc = "?"
    elif h.outcome.status == "raise":
        desc = f"raises {h.outcome.exc_name}"
    else:
        desc = h.outcome.literal or h.outcome.class_name or h.outcome.status
    lines.append(f'{ind}    """{call_of(h)} -> {desc}"""')
    lines += _snapshot_assertion(call_of(h), h.outcome, ind + "    ", extra_imports, importable)
    blocks.append("\n".join(lines))

    # --- rejecting edge inputs ---
    if raises:
        exc_col = _expected_col(names) + "_exc"
        rows = []
        for case in raises[:MAX_EDGE_ROWS]:
            exc_expr = case.outcome.exc_name
            note = ""
            if exc_expr == "Exception" and case.outcome.exc_qualname:
                note = f"  # {case.outcome.exc_qualname}"
            rows.append(f"pytest.param({case.call_expr}, {exc_expr}, id=\"{_case_id(case)}\"),{note}")
        lines = _parametrize(names + [exc_col], rows, ind)
        sig = ", ".join(["self"] + names + [exc_col])
        lines.append(f"{ind}def test_rejects_edge_inputs({sig}):")
        lines.append(f'{ind}    """Rejected boundary/invalid inputs raise a clear error."""')
        lines.append(f"{ind}    with pytest.raises({exc_col}):")
        args = ", ".join(names)
        lines.append(f"{ind}        {invoke(f'{call_prefix}({args})')}")
        blocks.append("\n".join(lines))

    # --- accepted edge inputs with literal results ---
    if returns_lit:
        exp_col = _expected_col(names)
        rows = [
            f'pytest.param({case.call_expr}, {case.outcome.literal}, id="{_case_id(case)}"),'
            for case in returns_lit[:MAX_EDGE_ROWS]
        ]
        lines = _parametrize(names + [exp_col], rows, ind)
        sig = ", ".join(["self"] + names + [exp_col])
        lines.append(f"{ind}def test_accepts_edge_inputs({sig}):")
        lines.append(f'{ind}    """Boundary inputs produce defined results."""')
        args = ", ".join(names)
        lines.append(f"{ind}    assert {invoke(f'{call_prefix}({args})')} == {exp_col}")
        blocks.append("\n".join(lines))

    # --- accepted edge inputs asserted by type (opaque/non-deterministic) ---
    if returns_typed:
        type_col = _expected_col(names) + "_type"
        rows = []
        for case in returns_typed[:MAX_EDGE_ROWS]:
            cls = case.outcome.class_name or "object"
            if cls not in dir(builtins):
                cls = "object"
            rows.append(
                f'pytest.param({case.call_expr}, {cls}, id="{_case_id(case)}"),'
            )
        lines = _parametrize(names + [type_col], rows, ind)
        sig = ", ".join(["self"] + names + [type_col])
        lines.append(f"{ind}def test_accepts_edge_inputs_returns_typed({sig}):")
        lines.append(f'{ind}    """Boundary inputs return a usable result (type-checked)."""')
        args = ", ".join(names)
        lines.append(f"{ind}    assert isinstance({invoke(f'{call_prefix}({args})')}, {type_col})")
        blocks.append("\n".join(lines))

    # --- inputs that could not be probed (timeout / internal error) ---
    if skipped:
        rows = [f'pytest.param({case.call_expr}, id="{_case_id(case)}"),'
                for case in skipped[:MAX_EDGE_ROWS]]
        lines = _parametrize(names, rows, ind)
        sig = ", ".join(["self"] + names)
        lines.append(f"{ind}@pytest.mark.skip(reason=\"call timed out or errored during generation\")")
        lines.append(f"{ind}def test_edge_inputs_unprobed({sig}):")
        args = ", ".join(names)
        lines.append(f"{ind}    {invoke(f'{call_prefix}({args})')}")
        blocks.append("\n".join(lines))

    return blocks


# ---------------------------------------------------------------------------
# Scaffold-mode rendering
# ---------------------------------------------------------------------------


def _render_scaffold(plan: FunctionPlan, cases: List[ProbeCase]) -> List[str]:
    ind = "    "  # one level inside the Test class
    names = _param_names(plan)
    call_prefix = plan.func.call_target
    is_async = plan.func.is_async
    blocks: List[str] = []

    def invoke(expr: str) -> str:
        return f"asyncio.run({expr})" if is_async else expr

    h = cases[0]
    returns_none = plan.func.returns in ("None", "-> None")
    lines = [f"{ind}def test_happy_path(self):"]
    lines.append(f'{ind}    """TODO: pin the expected result for {call_prefix}({h.call_expr})."""')
    lines.append(f"{ind}    result = {invoke(f'{call_prefix}({h.call_expr})')}")
    if returns_none:
        lines.append(f"{ind}    assert result is None")
    else:
        lines.append(f"{ind}    # TODO: pin the expected result, e.g. assert result == <expected>")
    blocks.append("\n".join(lines))

    edge_cases = [c for c in cases[1:] if c.edge_param]
    if edge_cases:
        seen = set()
        rows = []
        for case in edge_cases:
            if case.call_expr in seen:
                continue
            seen.add(case.call_expr)
            rows.append(f'pytest.param({case.call_expr}, id="{_case_id(case)}"),')
            if len(rows) >= MAX_EDGE_ROWS:
                break
        if rows:
            lines = _parametrize(names, rows, ind)
            sig = ", ".join(["self"] + names)
            lines.append(f"{ind}def test_edge_inputs({sig}):")
            lines.append(f'{ind}    """Boundary and invalid inputs (smoke test).')
            lines.append("")
            lines.append(f"{ind}    Exercises the call and tolerates any rejection. Review the")
            lines.append(f'{ind}    outcomes and pin the expected contract for your spec.')
            lines.append(f'{ind}    """')
            args = ", ".join(names)
            lines.append(f"{ind}    try:")
            lines.append(f"{ind}        result = {invoke(f'{call_prefix}({args})')}")
            lines.append(f"{ind}    except Exception:")
            lines.append(f"{ind}        return  # input rejected — pin the expected exception here")
            lines.append(f"{ind}    # TODO: pin the expected result, e.g. assert result == <expected>")
            blocks.append("\n".join(lines))
    return blocks


# ---------------------------------------------------------------------------
# Unknown-type resolution (executed mode)
# ---------------------------------------------------------------------------


def _positioned_resolver(plan: FunctionPlan):
    """Happy values varied by parameter position for input diversity."""
    def resolver(pp):
        idx = plan.params.index(pp)
        return pp.happy[idx % len(pp.happy)]
    return resolver


def _make_resolver(plan: FunctionPlan, func, is_async: bool):
    """Pick a working happy value per parameter by trial calls."""
    resolved: Dict[str, object] = {}
    positioned = _positioned_resolver(plan)

    def resolver(pp):
        return resolved.get(pp.param.name, positioned(pp))

    for pp in plan.params:
        if pp.kind != "unknown":
            resolved[pp.param.name] = positioned(pp)
            continue
        best = pp.happy[0]
        for cand in pp.happy:
            case = bind(plan, {pp.param.name: cand}, resolver)
            outcome = probe(func, case.args, case.kwargs, is_async)
            if outcome.status == "return" or (
                outcome.status == "raise" and outcome.exc_name != "TypeError"
            ):
                best = cand
                break
        resolved[pp.param.name] = best
    return resolver


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate_test_source(
    source_path,
    functions: List[FunctionInfo],
    plans: List[FunctionPlan],
    execute: bool = True,
    version: str = "dev",
) -> GeneratedFile:
    """Render the test module for *source_path*.

    When *execute* is true the module is imported and every candidate call is
    probed; on failure the renderer degrades to scaffold mode with a warning.
    """
    module_name = source_path.stem
    warnings: List[str] = []
    module = None
    importable: Set[str] = set()
    if execute:
        try:
            module = load_module(source_path)
            importable = set(dir(module))
        except ModuleLoadError as exc:
            warnings.append(
                f"could not import {source_path.name} for probing ({exc}); "
                "emitting scaffold assertions instead"
            )
            execute = False

    classes: List[str] = []
    extra_imports: Set[str] = set()
    n_tests = 0
    for plan in plans:
        func = plan.func
        cases: List[ProbeCase] = []
        if execute:
            target = resolve_callable(module, func.call_target)
            if target is None:
                warnings.append(f"{func.call_target} not found on module; scaffold emitted")
                blocks = _render_scaffold(plan, build_cases(plan, _positioned_resolver(plan)))
            else:
                resolver = _make_resolver(plan, target, func.is_async)
                cases = build_cases(plan, resolver)
                for case in cases:
                    case.outcome = probe(target, case.args, case.kwargs, func.is_async)
                blocks = _render_snapshot(plan, cases, importable, extra_imports)
        else:
            cases = build_cases(plan, _positioned_resolver(plan))
            blocks = _render_scaffold(plan, cases)

        n_tests += len(blocks)
        body = f'\n{" " * 4}"""Tests for `{func.signature}` (line {func.lineno})."""\n\n'
        body += "\n\n".join(blocks)
        classes.append(f"class Test{_camel(func.qualname)}:\n{body}")

    imports = sorted({f.call_target.split(".")[0] for f in functions} | extra_imports)
    mode = "snapshot" if execute else "scaffold"
    header = (
        f'"""Auto-generated tests for {module_name}.py.\n\n'
        f"Generated by unittest-gen {version} ({mode} mode).\n\n"
    )
    if execute:
        header += (
            "Assertions encode behaviour observed at generation time. Review\n"
            "them before committing — a snapshot records what the code *does*,\n"
            "which is not always what it *should* do.\n"
        )
    else:
        header += (
            "Assertions encode the expected contract, not observed behaviour.\n"
            "Review each TODO and tighten them to your specification.\n"
        )
    header += (
        "\nRegenerate with:\n"
        f"    unittest-gen generate {source_path.name} --force\n"
        f'"""\n\n'
    )
    if any(f.is_async for f in functions):
        header += "import asyncio\n\n"
    header += f'import pytest\n\nfrom {module_name} import {", ".join(imports)}\n\n\n'
    return GeneratedFile(
        test_source=header + "\n\n\n".join(classes) + "\n",
        n_functions=len(functions),
        n_tests=n_tests,
        executed=execute,
        warnings=warnings,
    )
