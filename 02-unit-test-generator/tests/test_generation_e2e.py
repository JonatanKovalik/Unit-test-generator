"""End-to-end tests: generate a test file, then run it under pytest."""

import sys
from pathlib import Path

import pytest

from unittest_gen.analyzer import analyze_file
from unittest_gen.generator import generate_test_source
from unittest_gen.infer import plan_all


def _write_source(tmp_path: Path, body: str, name: str = "sample_mod") -> Path:
    src = tmp_path / f"{name}.py"
    src.write_text(body, encoding="utf-8")
    return src


def _generate(src: Path, execute: bool = True):
    functions = analyze_file(src)
    return generate_test_source(src, functions, plan_all(functions), execute=execute)


def _run_generated(tmp_path: Path, test_file: Path) -> "pytest.ExitCode":
    """Run pytest on test_file in-process and return the exit code."""
    return pytest.main(["-x", "-q", str(test_file), "-p", "no:cacheprovider",
                        "--rootdir", str(tmp_path)])


SAMPLE = '''
def add(a: int, b: int) -> int:
    return a + b


def divide(a: float, b: float) -> float:
    return a / b


def describe(name: str, excited: bool = False) -> str:
    suffix = "!" if excited else "."
    return f"Hello, {name}{suffix}"
'''


def test_snapshot_generation_produces_green_tests(tmp_path, monkeypatch):
    src = _write_source(tmp_path, SAMPLE)
    result = _generate(src)
    assert result.executed
    test_file = tmp_path / f"test_{src.stem}.py"
    test_file.write_text(result.test_source)

    monkeypatch.syspath_prepend(str(tmp_path))
    for mod in [m for m in sys.modules if m.startswith("sample_mod") or m.startswith("test_sample_mod")]:
        del sys.modules[mod]
    code = _run_generated(tmp_path, test_file)
    assert code == 0, result.test_source


def test_snapshot_assertions_are_exact(tmp_path):
    src = _write_source(tmp_path, SAMPLE)
    result = _generate(src)
    assert "assert add(2, 10) == 12" in result.test_source
    assert "pytest.raises" in result.test_source          # divide by zero etc.
    assert "expected_exc" in result.test_source


def test_scaffold_mode_runs_without_importing(tmp_path, monkeypatch):
    src = _write_source(tmp_path, "import totally_missing_pkg\ndef f(x: int) -> int: return x\n")
    result = _generate(src, execute=True)  # import fails -> degrades to scaffold
    assert not result.executed
    assert result.warnings
    assert "TODO" in result.test_source

    test_file = tmp_path / f"test_{src.stem}.py"
    test_file.write_text(result.test_source)
    # scaffold output still must be valid Python
    compile(result.test_source, str(test_file), "exec")


def test_no_execute_mode_is_pure_scaffold(tmp_path):
    src = _write_source(tmp_path, SAMPLE)
    result = _generate(src, execute=False)
    assert not result.executed
    assert "TODO" in result.test_source
    compile(result.test_source, "generated", "exec")


def test_private_and_dunder_all_filtered(tmp_path):
    src = _write_source(
        tmp_path,
        '__all__ = ["kept"]\ndef kept(): return 1\ndef dropped(): return 2\ndef _priv(): pass\n',
    )
    result = _generate(src)
    assert "kept" in result.test_source
    assert "dropped" not in result.test_source
    assert "_priv" not in result.test_source


def test_async_function_gets_snapshot(tmp_path, monkeypatch):
    src = _write_source(
        tmp_path,
        "import asyncio\n"
        "async def ping(host: str) -> str:\n"
        "    await asyncio.sleep(0)\n"
        "    return 'pong:' + host\n",
        name="async_mod",
    )
    functions = analyze_file(src)
    result = generate_test_source(src, functions, plan_all(functions), execute=True)
    assert "asyncio.run(ping(" in result.test_source
    assert "import asyncio" in result.test_source

    test_file = tmp_path / f"test_{src.stem}.py"
    test_file.write_text(result.test_source)
    monkeypatch.syspath_prepend(str(tmp_path))
    for mod in [m for m in sys.modules if "async_mod" in m]:
        del sys.modules[mod]
    assert _run_generated(tmp_path, test_file) == 0, result.test_source
