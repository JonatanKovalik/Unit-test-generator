import pytest

from unittest_gen.analyzer import analyze_source


def test_extracts_public_functions_with_signatures():
    funcs = analyze_source(
        "def add(a: int, b: int = 3) -> int:\n"
        "    return a + b\n"
        "\n"
        "def _hidden():\n"
        "    pass\n"
    )
    assert [f.name for f in funcs] == ["add"]
    f = funcs[0]
    assert f.returns == "int"
    assert [p.name for p in f.params] == ["a", "b"]
    assert f.params[0].required
    assert not f.params[1].required
    assert f.params[1].default == "3"


def test_respects_dunder_all():
    funcs = analyze_source(
        '__all__ = ["kept"]\n'
        "def kept(): pass\n"
        "def dropped(): pass\n"
    )
    assert [f.name for f in funcs] == ["kept"]


def test_keyword_only_and_variadic_params():
    funcs = analyze_source(
        "def f(a, *args, key: str = 'x', **kwargs): pass\n"
    )
    kinds = {p.name: p.kind for p in funcs[0].params}
    assert kinds == {
        "a": "positional",
        "args": "var_positional",
        "key": "keyword_only",
        "kwargs": "var_keyword",
    }


def test_async_and_staticmethods():
    funcs = analyze_source(
        "import asyncio\n"
        "async def go(x: int): pass\n"
        "\n"
        "class Util:\n"
        "    @staticmethod\n"
        "    def build(name: str): pass\n"
        "    def method(self): pass\n"
    )
    by_name = {f.qualname: f for f in funcs}
    assert set(by_name) == {"go", "Util.build"}
    assert by_name["go"].is_async
    assert not by_name["Util.build"].is_async
    assert by_name["Util.build"].call_target == "Util.build"


def test_syntax_error_propagates():
    with pytest.raises(SyntaxError):
        analyze_source("def broken(:\n")
