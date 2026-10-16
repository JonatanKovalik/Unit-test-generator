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
    assert "python -m pytest" in capsys.readouterr().out


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


def test_output_same_as_source_refused(tmp_path):
    src = _src(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["generate", str(src), "-o", str(src)])
    assert exc.value.code == 1
    assert "def add" in src.read_text()  # source untouched


def test_output_directory_refused(tmp_path):
    src = _src(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["generate", str(src), "-o", str(tmp_path)])
    assert exc.value.code == 1


def test_output_creates_parent_dirs(tmp_path):
    src = _src(tmp_path)
    out = tmp_path / "newdir" / "nested" / "test_mymath.py"
    assert main(["generate", str(src), "-o", str(out)]) == 0
    assert out.exists()


def test_non_utf8_source_errors(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_bytes(b"\xff\xfe\x00def f():\n    pass\n")
    with pytest.raises(SystemExit) as exc:
        main(["generate", str(bad)])
    assert exc.value.code == 1


def test_directory_named_py_errors(tmp_path):
    d = tmp_path / "pkg.py"
    d.mkdir()
    with pytest.raises(SystemExit) as exc:
        main(["generate", str(d)])
    assert exc.value.code == 1


def test_uppercase_py_suffix_accepted(tmp_path):
    src = tmp_path / "UPPER.PY"
    src.write_text("def f(x: int) -> int: return x\n")
    assert main(["generate", str(src)]) == 0
    assert (tmp_path / "test_UPPER.py").exists()


def test_only_private_functions_errors(tmp_path):
    src = _src(tmp_path, "def _hidden():\n    pass\n")
    with pytest.raises(SystemExit) as exc:
        main(["generate", str(src)])
    assert exc.value.code == 1


def test_main_survives_stringio_stdout(tmp_path):
    """main() must not crash when stdout lacks .encoding (e.g. StringIO)."""
    import contextlib
    import io

    src = _src(tmp_path)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert main(["inspect", str(src)]) == 0
    assert "add" in buf.getvalue()


def test_inspect_empty_file_warns(tmp_path, capsys):
    src = _src(tmp_path, "x = 1\n")
    assert main(["inspect", str(src)]) == 0
    assert "no public functions" in capsys.readouterr().out
