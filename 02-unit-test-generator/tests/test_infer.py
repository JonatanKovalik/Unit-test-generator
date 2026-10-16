from unittest_gen.analyzer import analyze_source
from unittest_gen.infer import plan_all


def _plan(source, name):
    funcs = analyze_source(source)
    plans = {p.func.name: p for p in plan_all(funcs)}
    return plans[name]


def test_annotation_wins():
    plan = _plan("def f(count: int, label: str) -> bool: pass\n", "f")
    kinds = {p.param.name: p.kind for p in plan.params}
    assert kinds == {"count": "int", "label": "str"}


def test_optional_annotation():
    plan = _plan("from typing import Optional\ndef f(x: Optional[int]): pass\n", "f")
    p = plan.params[0]
    assert p.kind == "int"
    assert p.optional
    assert any(e.value is None for e in p.edges)


def test_default_value_infers_kind():
    plan = _plan("def f(rate=0.5, extra=None): pass\n", "f")
    kinds = {p.param.name: p.kind for p in plan.params}
    assert kinds["rate"] == "float"
    assert kinds["extra"] == "unknown"


def test_isinstance_hint():
    plan = _plan(
        "def f(data):\n"
        "    if not isinstance(data, dict):\n"
        "        raise TypeError\n"
        "    return data\n",
        "f",
    )
    assert plan.params[0].kind == "dict"
    assert plan.params[0].source == "body usage"


def test_method_usage_hint():
    plan = _plan("def f(s):\n    return s.strip().lower()\n", "f")
    assert plan.params[0].kind == "str"


def test_numeric_usage_hint():
    plan = _plan("def f(x):\n    return x * 2 + 1\n", "f")
    assert plan.params[0].kind == "int"


def test_name_heuristic():
    plan = _plan("def f(user_name, is_active, values): pass\n", "f")
    kinds = {p.param.name: p.kind for p in plan.params}
    assert kinds == {"user_name": "str", "is_active": "bool", "values": "list"}


def test_edge_values_cover_spec_categories():
    plan = _plan("def f(text: str, xs: list, n: int): pass\n", "f")
    by_name = {p.param.name: p for p in plan.params}
    assert any(e.value is None for e in by_name["text"].edges)          # null input
    assert any(e.value == "" for e in by_name["text"].edges)            # empty structure
    assert any(e.value == [] for e in by_name["xs"].edges)              # empty structure
    assert any(e.value == 0 for e in by_name["n"].edges)                # boundary
    assert any(e.value == 2**63 - 1 for e in by_name["n"].edges)        # boundary
