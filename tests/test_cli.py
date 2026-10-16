from pathlib import Path

import pytest

from unittest_gen.cli import main


def _src(tmp_path: Path, body: str = "def add(a: int, b: int) -> int: return a + b\n") -> Path:
    src = tmp_path / "mymath.py"
    src.write_text(body)
    return src


def test_generate_writes_test_file(tmp_path, capsys):
    src = _src(tmp_path)
    assert main(["generate", str(src)]) == 0
    out = tmp_path / "test_mymath.py"
    assert out.exists()
    text = out.read_text()
    assert "import pytest" in text
    assert "from mymath import add" in text
    assert "python3 -m pytest" in capsys.readouterr().out


def test_generate_refuses_to_overwrite(tmp_path):
    src = _src(tmp_path)
    out = tmp_path / "test_mymath.py"
    out.write_text("# custom\n")
    with pytest.raises(SystemExit) as exc:
        main(["generate", str(src)])
    assert exc.value.code == 1
    assert out.read_text() == "# custom\n"


def test_generate_force_overwrites(tmp_path):
    src = _src(tmp_path)
    out = tmp_path / "test_mymath.py"
    out.write_text("# custom\n")
    assert main(["generate", str(src), "--force"]) == 0
    assert "import pytest" in out.read_text()


def test_generate_missing_file_errors(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["generate", str(tmp_path / "nope.py")])
    assert exc.value.code == 1


def test_generate_non_python_errors(tmp_path):
    js = tmp_path / "x.js"
    js.write_text("function f() {}\n")
    with pytest.raises(SystemExit) as exc:
        main(["generate", str(js)])
    assert exc.value.code == 1


def test_inspect_lists_signatures(tmp_path, capsys):
    src = _src(tmp_path, "def greet(name: str, times: int = 1) -> str:\n    return name * times\n")
    assert main(["inspect", str(src)]) == 0
    out = capsys.readouterr().out
    assert "greet(name: str, times: int = 1) -> str" in out
    assert "[annotation]" in out


def test_no_execute_flag(tmp_path):
    src = _src(tmp_path)
    assert main(["generate", str(src), "--no-execute"]) == 0
    assert "TODO" in (tmp_path / "test_mymath.py").read_text()
