# Copyright 2026 Marimo. All rights reserved.
"""Process-shaped start-method and synchronization factories.

The factories keep `multiprocessing.get_context("spawn")` and context-owned
lock or event constructors callable in Pyodide. The returned primitives are
same-interpreter synchronization objects. They are not backed by OS process
state.
"""

from __future__ import annotations

import threading as _threading
from typing import TYPE_CHECKING, Any, NoReturn

from marimo._runtime._wasm._concurrency._threading import (
    AsyncCondition,
    AsyncEvent,
)
from marimo._runtime._wasm._concurrency._wait import (
    UnsupportedWasmConcurrencyError,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from multiprocessing.context import BaseContext


class MultiprocessingSyncAdapter:
    """Expose multiprocessing-shaped acquire semantics for local primitives."""

    def __init__(
        self,
        primitive: Any,
        *,
        release_error: type[Exception] | None = None,
    ) -> None:
        self._primitive = primitive
        self._release_error = release_error

    def acquire(
        self, block: bool = True, timeout: float | None = None
    ) -> bool:
        if not block:
            return bool(self._primitive.acquire(blocking=False))
        if timeout is None:
            return bool(self._primitive.acquire())
        timeout = max(timeout, 0)
        return bool(self._primitive.acquire(timeout=timeout))

    def release(self) -> None:
        try:
            self._primitive.release()
        except RuntimeError as exc:
            if self._release_error is not None:
                raise self._release_error(str(exc)) from None
            raise

    def __enter__(self) -> Any:
        return self.acquire()

    def __exit__(self, *exc: Any) -> None:
        del exc
        self.release()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._primitive, name)


def cpu_count() -> int:
    return 1


def get_all_start_methods() -> list[str]:
    return ["spawn"]


def validate_start_method(method: str | None) -> None:
    if method not in (None, "spawn"):
        raise ValueError("WASM multiprocessing shim only supports 'spawn'")


def get_start_method(allow_none: bool = False) -> str:
    del allow_none
    return "spawn"


def set_start_method(method: str | None, force: bool = False) -> None:
    del force
    validate_start_method(method)


def get_context_factory(original: Callable[..., Any]) -> Callable[..., Any]:
    def _get_context(method: str | None = None) -> Any:
        validate_start_method(method)
        return original("spawn")

    return _get_context


def freeze_support() -> None:
    return None


def unsupported_factory(api: str) -> Callable[..., NoReturn]:
    def _unsupported(*args: Any, **kwargs: Any) -> NoReturn:
        del args, kwargs
        raise UnsupportedWasmConcurrencyError(
            f"{api} is not supported by the Pyodide WASM process "
            "compatibility shim"
        )

    return _unsupported


def direct_event_factory() -> AsyncEvent:
    return AsyncEvent()


def event_factory(_ctx: BaseContext | None = None) -> AsyncEvent:
    del _ctx
    return AsyncEvent()


def direct_lock_factory() -> Any:
    return MultiprocessingSyncAdapter(
        _threading.Lock(), release_error=ValueError
    )


def lock_factory(_ctx: BaseContext | None = None) -> Any:
    del _ctx
    return direct_lock_factory()


def direct_rlock_factory() -> Any:
    return MultiprocessingSyncAdapter(
        _threading.RLock(), release_error=AssertionError
    )


def rlock_factory(_ctx: BaseContext | None = None) -> Any:
    del _ctx
    return direct_rlock_factory()


def direct_semaphore_factory(value: int = 1) -> Any:
    return MultiprocessingSyncAdapter(_threading.Semaphore(value))


def semaphore_factory(_ctx: BaseContext | None = None, value: int = 1) -> Any:
    del _ctx
    return direct_semaphore_factory(value)


def direct_bounded_semaphore_factory(value: int = 1) -> Any:
    return MultiprocessingSyncAdapter(_threading.BoundedSemaphore(value))


def bounded_semaphore_factory(
    _ctx: BaseContext | None = None, value: int = 1
) -> Any:
    del _ctx
    return direct_bounded_semaphore_factory(value)


def direct_condition_factory(lock: Any | None = None) -> AsyncCondition:
    return AsyncCondition(lock if lock is not None else direct_rlock_factory())


def condition_factory(
    _ctx: BaseContext | None = None, lock: Any | None = None
) -> AsyncCondition:
    del _ctx
    return direct_condition_factory(lock)


def direct_barrier_factory(
    parties: int,
    action: Callable[[], Any] | None = None,
    timeout: float | None = None,
) -> Any:
    return _threading.Barrier(parties, action=action, timeout=timeout)


def barrier_factory(
    _ctx: BaseContext | None,
    parties: int,
    action: Callable[[], Any] | None = None,
    timeout: float | None = None,
) -> Any:
    del _ctx
    return _threading.Barrier(parties, action=action, timeout=timeout)
