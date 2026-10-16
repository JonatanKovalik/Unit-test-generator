"""Loads a target module and probes its functions with candidate inputs.

Probing records the real behaviour of each function — the returned value or
the raised exception — so the generated tests assert *actual* contracts
(characterisation tests) instead of guesses.  Calls run under a per-call
timeout and with stdout/stderr suppressed.

Functions that cannot be imported or take unknown-typed parameters are still
covered: for an ``unknown`` parameter the prober tries several candidate
values and keeps whichever type the function accepts.
"""

from __future__ import annotations

import ast
import asyncio
import builtins
import contextlib
import hashlib
import importlib.util
import inspect
import io
import signal
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional

CALL_TIMEOUT_SECONDS = 2.0


class ModuleLoadError(Exception):
    """Raised when the target module cannot be imported for probing."""


@dataclass
class Outcome:
    """The observed result of one probed call."""

    status: str  # 'return' | 'raise' | 'timeout' | 'error'
    literal: Optional[str] = None  # repr() when it round-trips via literal_eval
    class_name: Optional[str] = None  # type name of the result or the exception
    exc_name: Optional[str] = None  # exception class name usable in the test file
    exc_qualname: Optional[str] = None  # e.g. 'mymod.CustomError' for comments
    deterministic: bool = True

    @property
    def raised(self) -> bool:
        return self.status == "raise"


@contextlib.contextmanager
def _time_limit(seconds: float):
    """Best-effort SIGALRM timeout (Unix main thread only)."""
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _handler(signum, frame):
        raise TimeoutError(f"call exceeded {seconds}s")

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def load_module(path: Path) -> ModuleType:
    """Import *path* as a module, with its directory on ``sys.path``."""
    path = path.resolve()
    tag = hashlib.sha1(str(path).encode()).hexdigest()[:10]
    module_name = f"utg_target_{path.stem}_{tag}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ModuleLoadError(f"cannot create import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - report any import-time failure
        raise ModuleLoadError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        try:
            sys.path.remove(str(path.parent))
        except ValueError:
            pass
    sys.modules[module_name] = module
    return module


def resolve_callable(module: ModuleType, qualname: str):
    """Resolve 'name' or 'Class.name' to a callable on *module*."""
    target: Any = module
    for part in qualname.split("."):
        target = getattr(target, part, None)
        if target is None:
            return None
    return target if callable(target) else None


def _literal_of(value: Any) -> Optional[str]:
    """Return a source literal for *value* if it round-trips through ast."""
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001
        return None
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None
    try:
        equal = parsed == value
    except Exception:  # noqa: BLE001
        return None
    return text if equal is True else None


def _exception_name(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if name in dir(builtins) else "Exception"


def _run_once(func, args, kwargs, is_async) -> Outcome:
    try:
        with _time_limit(CALL_TIMEOUT_SECONDS):
            if is_async:
                result = asyncio.run(func(*args, **kwargs))
            else:
                result = func(*args, **kwargs)
    except TimeoutError:
        return Outcome(status="timeout")
    except BaseException as exc:  # noqa: BLE001 - observe any failure mode
        return Outcome(
            status="raise",
            class_name=type(exc).__name__,
            exc_name=_exception_name(exc),
            exc_qualname=f"{type(exc).__module__}.{type(exc).__name__}",
        )
    return Outcome(
        status="return",
        literal=_literal_of(result),
        class_name=type(result).__name__,
    )


def probe(func, args: List[Any], kwargs: Dict[str, Any], is_async: bool = False) -> Outcome:
    """Call *func* once (twice for determinism) with muted stdio."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        first = _run_once(func, args, kwargs, is_async)
        if first.status != "return" or first.literal is None:
            return first
        second = _run_once(func, args, kwargs, is_async)
        if second.status != "return" or second.literal != first.literal:
            first.deterministic = False
            first.literal = None
        return first


# ---------------------------------------------------------------------------
# Probe plan building
# ---------------------------------------------------------------------------


@dataclass
class ProbeCase:
    """One call to attempt: bound arguments plus display expressions."""

    args_expr: List[str]
    kwargs_expr: Dict[str, str]
    args: List[Any] = field(default_factory=list)
    kwargs: Dict[str, Any] = field(default_factory=dict)
    label: str = ""
    edge_param: Optional[str] = None
    outcome: Optional[Outcome] = None

    @property
    def call_expr(self) -> str:
        parts = list(self.args_expr)
        parts += [f"{k}={v}" for k, v in self.kwargs_expr.items()]
        return ", ".join(parts)


def bind(plan, overrides: Dict[str, Any], resolver):
    """Bind one ProbeCase: apply *overrides*, else *resolver* for each param."""
    case = ProbeCase(args_expr=[], kwargs_expr={})
    for pp in plan.params:
        chosen = overrides.get(pp.param.name) or resolver(pp)
        if pp.param.kind == "positional":
            case.args_expr.append(chosen.expr)
            case.args.append(chosen.value)
        else:  # keyword_only
            case.kwargs_expr[pp.param.name] = chosen.expr
            case.kwargs[pp.param.name] = chosen.value
    return case


def build_cases(plan, resolver) -> List[ProbeCase]:
    """Build a bounded set of probe cases for one FunctionPlan.

    *resolver* maps a ParamPlan to its happy-path ArgValue; for ``unknown``
    parameters the caller typically probes each candidate and keeps the
    first the function accepts.
    """
    cases = [bind(plan, {}, resolver)]
    cases[0].label = "happy path"
    for pp in plan.params:
        for edge in pp.edges:
            case = bind(plan, {pp.param.name: edge}, resolver)
            case.label = edge.label or edge.expr
            case.edge_param = pp.param.name
            cases.append(case)
    return cases
