# Copyright 2026 Marimo. All rights reserved.
"""Same-interpreter `multiprocessing.Pool` adapter for Pyodide."""

from __future__ import annotations

import multiprocessing as _multiprocessing
from concurrent import futures as _futures
from typing import TYPE_CHECKING, Any

from marimo._runtime._wasm._concurrency._diagnostics import (
    record_pool_chunksize_ignored,
    record_pool_serialized,
)
from marimo._runtime._wasm._concurrency._futures import (
    AsyncioFuture,
    AsyncioProcessPoolExecutor,
)
from marimo._runtime._wasm._concurrency._wait import (
    UnsupportedWasmConcurrencyError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from typing_extensions import Self


class AsyncPoolResult:
    def __init__(
        self,
        future: AsyncioFuture,
        *,
        callback: Callable[[Any], Any] | None = None,
        error_callback: Callable[[BaseException], Any] | None = None,
    ) -> None:
        self._future = future
        if callback is not None or error_callback is not None:
            future.add_done_callback(
                lambda done: self._dispatch_callback(
                    done, callback, error_callback
                )
            )

    @staticmethod
    def _dispatch_callback(
        future: _futures.Future[Any],
        callback: Callable[[Any], Any] | None,
        error_callback: Callable[[BaseException], Any] | None,
    ) -> None:
        if future.cancelled():
            if error_callback is not None:
                error_callback(_futures.CancelledError())
            return
        exception = future.exception(timeout=0)
        if exception is not None:
            if error_callback is not None:
                error_callback(exception)
            return
        if callback is not None:
            callback(future.result(timeout=0))

    def get(self, timeout: float | None = None) -> Any:
        try:
            return self._future.result(timeout=timeout)
        except _futures.TimeoutError as exc:
            if self._future.done():
                return self._future.result(timeout=0)
            raise _multiprocessing.TimeoutError from exc

    def wait(self, timeout: float | None = None) -> None:
        try:
            self._future.result(timeout=timeout)
        except _futures.TimeoutError:
            return
        except UnsupportedWasmConcurrencyError:
            raise
        except BaseException:
            return

    def ready(self) -> bool:
        return self._future.done()

    def successful(self) -> bool:
        if not self.ready():
            raise ValueError("result is not ready")
        return (
            not self._future.cancelled()
            and self._future.exception(timeout=0) is None
        )


class AsyncPool:
    """Run `multiprocessing.Pool` work through a serialized local executor.

    `processes`, `maxtasksperchild`, and pool context arguments keep the
    constructor shape expected by callers. Submitted work still runs one item at
    a time in the current Pyodide interpreter.
    """

    def __init__(
        self,
        processes: int | None = None,
        initializer: Callable[..., Any] | None = None,
        initargs: tuple[Any, ...] = (),
        maxtasksperchild: int | None = None,
        context: Any | None = None,
    ) -> None:
        if processes is not None and processes <= 0:
            raise ValueError("number of processes must be at least 1")
        if initializer is not None and not callable(initializer):
            raise TypeError("initializer must be a callable")
        if maxtasksperchild is not None and (
            not isinstance(maxtasksperchild, int) or maxtasksperchild <= 0
        ):
            raise ValueError("maxtasksperchild must be a positive int or None")
        record_pool_serialized(
            processes=processes,
            maxtasksperchild=maxtasksperchild,
            context=context,
        )
        self._closed = False
        self._terminated = False
        self._executor = AsyncioProcessPoolExecutor(
            max_workers=processes,
            initializer=initializer,
            initargs=initargs,
        )

    def _check_running(self) -> None:
        if self._closed:
            raise ValueError("Pool not running")
        if self._terminated:
            raise ValueError("Pool has been terminated")

    def apply(
        self,
        func: Callable[..., Any],
        args: Iterable[Any] = (),
        kwds: dict[str, Any] | None = None,
    ) -> Any:
        self._check_running()
        return self._executor.submit(
            func, *tuple(args), **(kwds or {})
        ).result()

    def apply_async(
        self,
        func: Callable[..., Any],
        args: Iterable[Any] = (),
        kwds: dict[str, Any] | None = None,
        callback: Callable[[Any], Any] | None = None,
        error_callback: Callable[[BaseException], Any] | None = None,
    ) -> AsyncPoolResult:
        self._check_running()
        future = self._executor.submit(func, *tuple(args), **(kwds or {}))
        return AsyncPoolResult(
            future, callback=callback, error_callback=error_callback
        )

    def map(
        self,
        func: Callable[[Any], Any],
        iterable: Iterable[Any],
        chunksize: int | None = None,
    ) -> list[Any]:
        self._check_running()
        record_pool_chunksize_ignored(
            api="multiprocessing.Pool.map", chunksize=chunksize
        )
        return list(self._executor.map(func, iterable))

    def map_async(
        self,
        func: Callable[[Any], Any],
        iterable: Iterable[Any],
        chunksize: int | None = None,
        callback: Callable[[Any], Any] | None = None,
        error_callback: Callable[[BaseException], Any] | None = None,
    ) -> AsyncPoolResult:
        self._check_running()
        record_pool_chunksize_ignored(
            api="multiprocessing.Pool.map_async", chunksize=chunksize
        )
        items = list(iterable)
        future = self._executor.submit(lambda: [func(item) for item in items])
        return AsyncPoolResult(
            future,
            callback=callback,
            error_callback=error_callback,
        )

    def starmap(
        self,
        func: Callable[..., Any],
        iterable: Iterable[Iterable[Any]],
        chunksize: int | None = None,
    ) -> list[Any]:
        self._check_running()
        record_pool_chunksize_ignored(
            api="multiprocessing.Pool.starmap", chunksize=chunksize
        )
        return [
            self._executor.submit(func, *tuple(args)).result()
            for args in iterable
        ]

    def starmap_async(
        self,
        func: Callable[..., Any],
        iterable: Iterable[Iterable[Any]],
        chunksize: int | None = None,
        callback: Callable[[Any], Any] | None = None,
        error_callback: Callable[[BaseException], Any] | None = None,
    ) -> AsyncPoolResult:
        self._check_running()
        record_pool_chunksize_ignored(
            api="multiprocessing.Pool.starmap_async", chunksize=chunksize
        )
        items = [tuple(args) for args in iterable]
        future = self._executor.submit(lambda: [func(*args) for args in items])
        return AsyncPoolResult(
            future,
            callback=callback,
            error_callback=error_callback,
        )

    def imap(
        self,
        func: Callable[[Any], Any],
        iterable: Iterable[Any],
        chunksize: int = 1,
    ) -> Iterator[Any]:
        self._check_running()
        _validate_imap_chunksize(chunksize)
        record_pool_chunksize_ignored(
            api="multiprocessing.Pool.imap", chunksize=chunksize
        )

        def results() -> Iterator[Any]:
            for item in iterable:
                self._check_running()
                yield self._executor.submit(func, item).result()

        return results()

    def imap_unordered(
        self,
        func: Callable[[Any], Any],
        iterable: Iterable[Any],
        chunksize: int = 1,
    ) -> Iterator[Any]:
        self._check_running()
        _validate_imap_chunksize(chunksize)
        record_pool_chunksize_ignored(
            api="multiprocessing.Pool.imap_unordered", chunksize=chunksize
        )

        def results() -> Iterator[Any]:
            for item in iterable:
                self._check_running()
                yield self._executor.submit(func, item).result()

        return results()

    def close(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=False)

    def terminate(self) -> None:
        self._terminated = True
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._executor.cancel_running_work()

    def join(self) -> None:
        if not self._closed and not self._terminated:
            raise ValueError("Pool is still running")
        self._executor.shutdown(wait=True)

    def __enter__(self) -> Self:
        self._check_running()
        return self

    def __exit__(self, *exc: Any) -> None:
        del exc
        self.terminate()


def pool_factory(
    _ctx: Any | None = None,
    processes: int | None = None,
    initializer: Callable[..., Any] | None = None,
    initargs: tuple[Any, ...] = (),
    maxtasksperchild: int | None = None,
) -> AsyncPool:
    return AsyncPool(
        processes=processes,
        initializer=initializer,
        initargs=initargs,
        maxtasksperchild=maxtasksperchild,
        context=_ctx,
    )


def direct_pool_factory(
    processes: int | None = None,
    initializer: Callable[..., Any] | None = None,
    initargs: tuple[Any, ...] = (),
    maxtasksperchild: int | None = None,
    context: Any | None = None,
) -> AsyncPool:
    return AsyncPool(
        processes=processes,
        initializer=initializer,
        initargs=initargs,
        maxtasksperchild=maxtasksperchild,
        context=context,
    )


def _validate_imap_chunksize(chunksize: int) -> None:
    if chunksize < 1:
        raise ValueError(f"Chunksize must be 1+, not {chunksize}")
