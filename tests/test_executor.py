"""Executor probing: timeout paths, literal capture, cross-platform fallback."""

import time

import pytest

from unittest_gen import executor


def test_threaded_call_returns_result():
    assert executor._threaded_call(lambda a, b: a + b, [2, 3], {}, False, 5) == 5


def test_threaded_call_ferries_exception():
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        executor._threaded_call(boom, [], {}, False, 5)


def test_threaded_call_times_out():
    def slow():
        time.sleep(60)

    with pytest.raises(TimeoutError):
        executor._threaded_call(slow, [], {}, False, 0.1)


def test_probe_times_out_without_signals(monkeypatch):
    """Windows/non-main-thread path (no SIGALRM) still records timeouts."""
    monkeypatch.setattr(executor, "_signals_available", lambda: False)

    def slow():
        time.sleep(60)

    assert executor.probe(slow, [], {}).status == "timeout"


def test_probe_records_return_literal(monkeypatch):
    monkeypatch.setattr(executor, "_signals_available", lambda: False)
    outcome = executor.probe(lambda a, b: a + b, [2, 3], {})
    assert outcome.status == "return"
    assert outcome.literal == "5"


def test_probe_propagates_keyboard_interrupt():
    """Ctrl+C must abort generation, not be recorded as a function outcome."""
    def interrupted():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        executor.probe(interrupted, [], {})


def test_probe_records_exception():
    outcome = executor.probe(lambda a: 1 / 0, [0], {})
    assert outcome.status == "raise"
    assert outcome.exc_name == "ZeroDivisionError"
