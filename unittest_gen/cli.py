"""Command line interface for unittest-gen.

Usage:
    unittest-gen generate path/to/file.py [--output PATH] [--force] [--no-execute]
    unittest-gen inspect path/to/file.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .analyzer import analyze_file
from .generator import generate_test_source
from .infer import plan_all

_ANSI_OK = None  # lazy cache for _enable_vt_mode()


def _enable_vt_mode() -> bool:
    """Enable ANSI escape processing in Windows consoles (no-op elsewhere)."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _use_color() -> bool:
    global _ANSI_OK
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if _ANSI_OK is None:
        _ANSI_OK = _enable_vt_mode()
    return _ANSI_OK


def _harden_stdio() -> None:
    """Prevent UnicodeEncodeError for ✓/→ on non-UTF-8 streams (e.g. Windows pipes)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        encoding = getattr(stream, "encoding", None)
        if reconfigure and encoding and "utf" not in encoding.lower():
            try:
                reconfigure(errors="replace")
            except Exception:
                pass


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _use_color() else text


def _ok(msg: str) -> None:
    print(_c("32", "  ✓ ") + msg)


def _warn(msg: str) -> None:
    print(_c("33", "  ! ") + msg)


def _err(msg: str) -> None:
    print(_c("31", "  ✗ ") + msg)


def _info(msg: str) -> None:
    print(_c("36", "  → ") + msg)


def _die(msg: str, code: int = 1) -> "NoReturn":
    _err(msg)
    sys.exit(code)


def _check_source(path_str: str) -> Path:
    path = Path(path_str).expanduser()
    if not path.exists():
        _die(f"file not found: {path}")
    if path.is_dir():
        _die(f"{path} is a directory, not a Python file")
    if path.suffix.lower() != ".py":
        _die(f"unsupported file type '{path.suffix}' — only Python source (.py) is supported")
    if not path.stem.isidentifier():
        _die(
            f"module name '{path.stem}' is not a valid Python identifier; "
            "rename the file so generated tests can import it"
        )
    return path


def _analyze_or_die(source: Path):
    try:
        return analyze_file(source)
    except SyntaxError as exc:
        _die(f"could not parse {source.name}: {exc}")
    except (OSError, UnicodeDecodeError) as exc:
        _die(f"could not read {source.name}: {exc}")


def cmd_generate(args: argparse.Namespace) -> int:
    source = _check_source(args.file)
    out = Path(args.output).expanduser() if args.output else source.with_name(f"test_{source.stem}.py")
    if out.resolve() == source.resolve():
        _die("refusing to overwrite the source file itself — choose a different -o path")
    if out.is_dir():
        _die(f"output path {out} is a directory")

    _info(f"parsing {source}")
    functions = _analyze_or_die(source)
    if not functions:
        _die(f"no public functions found in {source.name}")
    _ok(f"found {len(functions)} public function(s): {', '.join(f.qualname for f in functions)}")

    plans = plan_all(functions)

    if out.exists() and not args.force:
        _die(f"{out} already exists — re-run with --force to overwrite")
    if out.exists():
        _warn(f"overwriting existing {out.name} (--force)")

    mode = "snapshot (executing functions)" if not args.no_execute else "scaffold (--no-execute)"
    _info(f"generating tests in {mode} mode")
    result = generate_test_source(
        source, functions, plans, execute=not args.no_execute, version=__version__
    )
    for warning in result.warnings:
        _warn(warning)

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.test_source, encoding="utf-8")
    except OSError as exc:
        _die(f"could not write {out}: {exc}")
    _ok(f"wrote {out} — {result.n_tests} test(s) across {result.n_functions} function(s)")

    print()
    _info("run the generated tests with:")
    print(f"      python -m pytest {out} -v")
    if not result.executed:
        _info("scaffold mode: review TODO markers and tighten assertions")
    else:
        _info("review snapshot assertions — they record current behaviour")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    source = _check_source(args.file)
    functions = _analyze_or_die(source)
    if not functions:
        _warn(f"no public functions found in {source.name}")
        return 0

    plans = plan_all(functions)
    print(f"{source.name}: {len(functions)} public function(s)\n")
    for plan in plans:
        func = plan.func
        flags = "async " if func.is_async else ""
        print(f"  {flags}{func.signature}   (line {func.lineno})")
        for pp in plan.params:
            via = f" [{pp.source}]" if pp.kind != "unknown" else " [unresolved]"
            default = f" = {pp.param.default}" if pp.param.has_default else ""
            req = "optional" if pp.param.has_default else "required"
            print(f"      {pp.param.name}: {pp.kind} ({req}{default}){via}")
        if func.docstring:
            first = func.docstring.splitlines()[0]
            print(f'      doc: "{first[:70]}"')
        print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unittest-gen",
        description="Generate pytest unit tests for a Python source file.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="generate a test file for a source file")
    gen.add_argument("file", help="path to the Python source file to test")
    gen.add_argument("-o", "--output", help="output path (default: test_<name>.py alongside the source)")
    gen.add_argument("-f", "--force", action="store_true", help="overwrite an existing test file")
    gen.add_argument(
        "--no-execute",
        action="store_true",
        help="do not import/run the module; emit contract scaffold assertions",
    )
    gen.set_defaults(func=cmd_generate)

    insp = sub.add_parser("inspect", help="show the parsed signatures without generating")
    insp.add_argument("file", help="path to the Python source file")
    insp.set_defaults(func=cmd_inspect)
    return parser


def main(argv=None) -> int:
    _harden_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _err("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
