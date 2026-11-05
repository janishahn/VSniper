from __future__ import annotations

import pytest

from vsniper.integrations._retry import retry_transient


def _retryable_error(message: str) -> RuntimeError:
    exc = RuntimeError(message)
    exc.retryable = True  # type: ignore[attr-defined]
    return exc


def test_nested_retry_does_not_amplify_attempts() -> None:
    calls = 0

    def always_fails() -> None:
        nonlocal calls
        calls += 1
        raise _retryable_error("transient")

    def inner() -> None:
        retry_transient(always_fails, label="inner")

    with pytest.raises(RuntimeError):
        retry_transient(inner, label="outer")

    # Inner exhausts its 3 attempts and clears the flag, so the outer sees a terminal error
    # and does not retry — 3 total calls, not 9.
    assert calls == 3


def test_non_retryable_error_is_not_retried() -> None:
    calls = 0

    def fails() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("permanent")

    with pytest.raises(RuntimeError):
        retry_transient(fails, label="label")

    assert calls == 1
