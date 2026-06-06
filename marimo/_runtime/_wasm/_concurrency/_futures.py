# Copyright 2026 Marimo. All rights reserved.
"""Run `concurrent.futures` work on one Pyodide event-loop lane.

`serialized` executor work is queued and drained one item at a time in the
current Pyodide interpreter. The adapter preserves future creation, result,
exception, callback, cancellation, and wait behavior where the tests cover it.
Requested worker counts are accepted for API shape, but they do not create
parallel workers.

`ThreadPoolExecutor` is part of the default WASM install. `ProcessPoolExecutor`
uses the same executor core and is installed only by the process compatibility
group because Pyodide cannot create child processes, isolate memory, or provide
a pickle-copy IPC boundary.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import time
from collections import deque
from concurrent import futures as _futures
from concurrent.futures import _base as _futures_base
from dataclasses import dataclass
from itertools import islice
from typing import TYPE_CHECKING, Any, cast

from marimo._runtime._wasm._concurrency import _state
from marimo._runtime._wasm._concurrency._diagnostics import (
    record_executor_chunksize_ignored,
    record_executor_serialized,
    record_executor_worker_count_ignored,
    record_process_executor_serialized,
)
from marimo._runtime._wasm._concurrency._state import (
    create_task_in_empty_wasm_context,
    current_thread_var,
    get_event_loop,
    live_threads,
    new_ident,
    notify_thread_start,
    run_until_complete_in_empty_wasm_context,
)
from marimo._runtime._wasm._concurrency._thread_locals import (
    clear_thread_local_state,
)
from marimo._runtime._wasm._concurrency._threading import AsyncioThread
from marimo._runtime._wasm._concurrency._wait import (
    UnsupportedWasmConcurrencyError,
    cooperative_wait,
    wait_with_timeout,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator


class AsyncioFuture(_futures.Future[Any]):
    def __init__(self) -> None:
        super().__init__()
        self._async_done: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    def _done_event(self) -> asyncio.Event:
        if self._async_done is None:
            self._async_done = asyncio.Event()
        if self.done():
            self._async_done.set()
        return self._async_done

    def set_result(self, result: Any) -> None:
        super().set_result(result)
        if self._async_done is not None:
            self._async_done.set()

    def set_exception(self, exception: BaseException | None) -> None:
        if exception is None:
            raise TypeError("exception must be a BaseException")
        super().set_exception(exception)
        if self._async_done is not None:
            self._async_done.set()

    def cancel(self) -> bool:
        cancelled = super().cancel()
        if cancelled:
            if self._task is not None:
                self._task.cancel()
            if self._async_done is not None:
                self._async_done.set()
        return cancelled

    async def _wait_done(self, timeout: float | None) -> bool:
        if self.done():
            return True
        event = self._done_event()
        return await wait_with_timeout(event.wait(), timeout)

    def result(self, timeout: float | None = None) -> Any:
        if not self.done():
            if timeout is not None and timeout <= 0:
                return super().result(timeout=0)
            cooperative_wait(self._wait_done(timeout))
        return super().result(timeout=0)

    def exception(self, timeout: float | None = None) -> BaseException | None:
        if not self.done():
            if timeout is not None and timeout <= 0:
                return super().exception(timeout=0)
            cooperative_wait(self._wait_done(timeout))
        return super().exception(timeout=0)


class ExecutorThread(AsyncioThread):
    def __init__(self, name: str) -> None:
        super().__init__(target=None, name=name, daemon=True)
        self._ident = new_ident()
        self._native_id = self._ident
        self._started = True
        self._finished = False

    def is_alive(self) -> bool:
        return self._started and not self._finished


@dataclass
class WorkItem:
    future: AsyncioFuture
    fn: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


async def wait_for_wasm_futures(
    futures: set[AsyncioFuture],
    timeout: float | None,
    return_when: str,
) -> None:
    end_time = None if timeout is None else time.monotonic() + timeout

    while True:
        done = {future for future in futures if future.done()}
        if wait_condition_met(done, futures, return_when):
            return

        remaining = None
        if end_time is not None:
            remaining = end_time - time.monotonic()
            if remaining <= 0:
                return

        events = [
            asyncio.create_task(future._done_event().wait())
            for future in futures
            if not future.done()
        ]
        if not events:
            return
        timeout_task = (
            None
            if remaining is None
            else asyncio.create_task(asyncio.sleep(remaining))
        )
        tasks: list[asyncio.Task[Any]] = events.copy()
        if timeout_task is not None:
            tasks.append(timeout_task)
        try:
            await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def wait_condition_met(
    done: Collection[_futures.Future[Any]],
    futures: Collection[_futures.Future[Any]],
    return_when: str,
) -> bool:
    if return_when == _futures.FIRST_COMPLETED:
        return bool(done)
    if return_when == _futures.FIRST_EXCEPTION:
        return any(
            future.done()
            and not future.cancelled()
            and future.exception(timeout=0) is not None
            for future in done
        ) or len(done) == len(futures)
    return len(done) == len(futures)


def validate_return_when(return_when: str) -> None:
    if return_when not in {
        _futures.FIRST_COMPLETED,
        _futures.FIRST_EXCEPTION,
        _futures.ALL_COMPLETED,
    }:
        raise ValueError(f"Invalid return condition: {return_when!r}")


def wasm_wait(
    futures: Iterable[_futures.Future[Any]],
    timeout: float | None = None,
    return_when: str = _futures.ALL_COMPLETED,
) -> _futures_base.DoneAndNotDoneFutures[Any]:
    validate_return_when(return_when)
    future_set = set(futures)
    if not future_set:
        return _futures_base.DoneAndNotDoneFutures(set(), set())
    if not any(isinstance(future, AsyncioFuture) for future in future_set):
        return ORIGINAL_WAIT(
            future_set, timeout=timeout, return_when=return_when
        )
    if not all(isinstance(future, AsyncioFuture) for future in future_set):
        return wasm_mixed_wait(
            future_set, timeout=timeout, return_when=return_when
        )
    wasm_future_set = cast(set[AsyncioFuture], future_set)

    done = {future for future in future_set if future.done()}
    if not wait_condition_met(done, future_set, return_when) and not (
        timeout is not None and timeout <= 0
    ):
        cooperative_wait(
            wait_for_wasm_futures(
                wasm_future_set, timeout=timeout, return_when=return_when
            )
        )

    done = {future for future in future_set if future.done()}
    return _futures_base.DoneAndNotDoneFutures(done, future_set - done)


def wasm_mixed_wait(
    futures: set[_futures.Future[Any]],
    timeout: float | None,
    return_when: str,
) -> _futures_base.DoneAndNotDoneFutures[Any]:
    shim_futures = {
        future for future in futures if isinstance(future, AsyncioFuture)
    }
    foreign_futures = futures - shim_futures
    foreign_done, foreign_not_done = ORIGINAL_WAIT(
        foreign_futures, timeout=0, return_when=return_when
    )
    done = set(foreign_done) | {
        future for future in shim_futures if future.done()
    }
    if wait_condition_met(done, futures, return_when) or (
        timeout is not None and timeout <= 0
    ):
        return _futures_base.DoneAndNotDoneFutures(done, futures - done)
    if foreign_not_done:
        raise UnsupportedWasmConcurrencyError(
            "mixed pending concurrent.futures.wait inputs cannot block the "
            "Pyodide event-loop lane"
        )
    cooperative_wait(
        wait_for_wasm_futures(
            shim_futures,
            timeout=timeout,
            return_when=return_when,
        )
    )
    done = set(foreign_done) | {
        future for future in shim_futures if future.done()
    }
    return _futures_base.DoneAndNotDoneFutures(done, futures - done)


def wasm_as_completed(
    futures: Iterable[_futures.Future[Any]],
    timeout: float | None = None,
) -> Iterator[_futures.Future[Any]]:
    future_set = set(futures)
    if not any(isinstance(future, AsyncioFuture) for future in future_set):
        yield from ORIGINAL_AS_COMPLETED(future_set, timeout=timeout)
        return
    if not all(isinstance(future, AsyncioFuture) for future in future_set):
        foreign = [
            future
            for future in future_set
            if not isinstance(future, AsyncioFuture)
        ]
        foreign_done, foreign_not_done = ORIGINAL_WAIT(foreign, timeout=0)
        immediate_timeout = timeout is not None and timeout <= 0
        if foreign_not_done and not immediate_timeout:
            raise UnsupportedWasmConcurrencyError(
                "mixed pending concurrent.futures.as_completed inputs cannot "
                "block the Pyodide event-loop lane"
            )
        for future in foreign_done:
            yield future
        future_set = {
            future
            for future in future_set
            if isinstance(future, AsyncioFuture)
        }
        if immediate_timeout:
            for future in list(future_set):
                if future.done():
                    yield future
            unfinished = (
                len(foreign_not_done)
                + len(future_set)
                - sum(1 for future in future_set if future.done())
            )
            if unfinished:
                raise _futures.TimeoutError(f"{unfinished} futures unfinished")
            return

    end_time = None if timeout is None else time.monotonic() + timeout
    yielded: set[_futures.Future[Any]] = set()
    while len(yielded) < len(future_set):
        pending = [future for future in future_set if future not in yielded]
        already_done = [future for future in pending if future.done()]
        if already_done:
            for future in already_done:
                yielded.add(future)
                yield future
            continue

        remaining = None
        if end_time is not None:
            remaining = end_time - time.monotonic()
            if remaining <= 0:
                raise _futures.TimeoutError(
                    f"{len(future_set) - len(yielded)} futures unfinished"
                )

        done, _not_done = wasm_wait(
            pending,
            timeout=remaining,
            return_when=_futures.FIRST_COMPLETED,
        )
        if not done:
            raise _futures.TimeoutError(
                f"{len(future_set) - len(yielded)} futures unfinished"
            )
        for future in done:
            if future not in yielded:
                yielded.add(future)
                yield future


ORIGINAL_WAIT = _futures.wait
ORIGINAL_AS_COMPLETED = _futures.as_completed


class SerializedWasmExecutor(_futures.Executor):
    """Queue executor work onto one synthetic Pyodide worker lane.

    The lane gives callbacks and `current_thread()` a stable worker identity.
    It does not imply parallel execution.
    """

    def __init__(
        self,
        max_workers: int | None = None,
        thread_name_prefix: str = "",
        initializer: Callable[..., Any] | None = None,
        initargs: tuple[Any, ...] = (),
        *,
        api_name: str = "concurrent.futures.Executor",
        process_compatibility: bool = False,
    ) -> None:
        if max_workers is not None and max_workers <= 0:
            raise ValueError("max_workers must be greater than 0")
        if initializer is not None and not callable(initializer):
            raise TypeError("initializer must be a callable")
        self._max_workers = max_workers or 1
        self._api_name = api_name
        self._task_registry = _state.executor_task_registry(
            process_compatibility=process_compatibility
        )
        self._thread_name_prefix = thread_name_prefix or "WasmExecutor"
        self._initializer = initializer
        self._initargs = initargs
        self._shutdown = False
        self._initialized = False
        self._worker: ExecutorThread | None = None
        self._futures: set[AsyncioFuture] = set()
        self._queue: deque[WorkItem] = deque()
        self._runner_task: asyncio.Task[None] | None = None
        self._broken_initializer: BaseException | None = None
        _state.register_executor(
            self, process_compatibility=process_compatibility
        )

    def submit(
        self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any
    ) -> AsyncioFuture:
        if self._shutdown:
            raise RuntimeError("cannot schedule new futures after shutdown")
        if self._broken_initializer is not None:
            raise RuntimeError(
                f"{self._api_name} initializer failed"
            ) from self._broken_initializer
        loop = get_event_loop()
        future = AsyncioFuture()
        self._futures.add(future)
        self._queue.append(
            WorkItem(
                future=future,
                fn=fn,
                args=args,
                kwargs=kwargs,
            )
        )
        self._start_runner(loop)
        if not loop.is_running() and not future.done():
            run_until_complete_in_empty_wasm_context(loop, self._drain_queue())
        return future

    def _start_runner(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._runner_task is not None and not self._runner_task.done():
            return
        if loop.is_running():
            self._runner_task = create_task_in_empty_wasm_context(
                loop, self._drain_queue()
            )
            self._task_registry.add(self._runner_task)
            self._runner_task.add_done_callback(self._task_registry.discard)

    async def _drain_queue(self) -> None:
        while self._queue:
            item = self._queue.popleft()
            future = item.future
            if not future.set_running_or_notify_cancel():
                self._futures.discard(future)
                continue

            worker = self._worker_for_lane()
            token = current_thread_var.set(worker)
            try:
                if not self._initialized and self._initializer is not None:
                    try:
                        self._initializer(*self._initargs)
                    except BaseException as exc:
                        self._broken_initializer = exc
                        future.set_exception(exc)
                        self._fail_queued_work()
                        continue
                self._initialized = True
                fn, args, kwargs = _wrap_context_run_with_worker_identity(
                    item.fn,
                    item.args,
                    item.kwargs,
                    worker,
                )
                result = fn(*args, **kwargs)
                future.set_result(result)
            except BaseException as exc:
                future.set_exception(exc)
            finally:
                current_thread_var.reset(token)
                self._futures.discard(future)
        if self._shutdown:
            self._finish_worker_lane()
            if not self._queue and not self._futures:
                _state.unregister_executor(self)

    def _fail_queued_work(self) -> None:
        while self._queue:
            item = self._queue.popleft()
            if not item.future.cancelled():
                item.future.set_exception(
                    RuntimeError(f"{self._api_name} initializer failed")
                )
            self._futures.discard(item.future)

    def _worker_for_lane(self) -> ExecutorThread:
        if self._worker is None or not self._worker.is_alive():
            self._worker = ExecutorThread(
                f"{self._thread_name_prefix}_{new_ident()}"
            )
            live_threads.add(self._worker)
            notify_thread_start(self._worker)
        return self._worker

    def _finish_worker_lane(self) -> None:
        if self._worker is None:
            return
        worker = self._worker
        worker._finished = True
        if worker.ident is not None:
            clear_thread_local_state(worker.ident)
        live_threads.discard(worker)
        self._worker = None

    def map(
        self,
        fn: Callable[..., Any],
        *iterables: Iterable[Any],
        timeout: float | None = None,
        chunksize: int = 1,
        buffersize: int | None = None,
    ) -> Iterator[Any]:
        _validate_map_buffersize(buffersize)
        record_executor_chunksize_ignored(
            api=f"{self._api_name}.map", chunksize=chunksize
        )
        end_time = None if timeout is None else time.monotonic() + timeout

        def remaining_timeout() -> float | None:
            if end_time is None:
                return None
            return max(end_time - time.monotonic(), 0)

        args_iterator = zip(*iterables, strict=False)
        if buffersize is None:
            futures = [self.submit(fn, *args) for args in args_iterator]

            def eager_result_iterator() -> Iterator[Any]:
                try:
                    for future in futures:
                        yield future.result(timeout=remaining_timeout())
                finally:
                    for future in futures:
                        future.cancel()

            return eager_result_iterator()

        pending = deque(
            self.submit(fn, *args)
            for args in islice(args_iterator, buffersize)
        )

        def buffered_result_iterator() -> Iterator[Any]:
            try:
                while pending:
                    future = pending.popleft()
                    result = future.result(timeout=remaining_timeout())
                    try:
                        args = next(args_iterator)
                    except StopIteration:
                        pass
                    else:
                        pending.append(self.submit(fn, *args))
                    yield result
            finally:
                for future in pending:
                    future.cancel()

        return buffered_result_iterator()

    def shutdown(
        self, wait: bool = True, *, cancel_futures: bool = False
    ) -> None:
        self._shutdown = True
        if cancel_futures:
            while self._queue:
                item = self._queue.pop()
                item.future.cancel()
                self._futures.discard(item.future)
        if wait:
            for future in list(self._futures):
                if not future.done():
                    try:
                        future.result()
                    except UnsupportedWasmConcurrencyError:
                        raise
                    except BaseException:
                        pass
        if wait or self._runner_task is None or self._runner_task.done():
            self._finish_worker_lane()
        if self._worker is None and not self._queue and not self._futures:
            _state.unregister_executor(self)

    def cancel_running_work(self) -> None:
        if self._runner_task is not None and not self._runner_task.done():
            self._runner_task.cancel()

    def shutdown_for_wasm_teardown(self) -> None:
        self.shutdown(wait=False, cancel_futures=True)

    def is_idle_for_wasm_teardown(self) -> bool:
        return (
            self._shutdown
            and self._worker is None
            and not self._queue
            and not self._futures
        )


class AsyncioThreadPoolExecutor(SerializedWasmExecutor):
    """`ThreadPoolExecutor` adapter with serialized Pyodide execution."""

    def __init__(
        self,
        max_workers: int | None = None,
        thread_name_prefix: str = "",
        initializer: Callable[..., Any] | None = None,
        initargs: tuple[Any, ...] = (),
    ) -> None:
        if max_workers is not None and max_workers <= 0:
            raise ValueError("max_workers must be greater than 0")
        record_executor_serialized(
            api="concurrent.futures.ThreadPoolExecutor",
            requested_workers=max_workers,
        )
        record_executor_worker_count_ignored(
            api="concurrent.futures.ThreadPoolExecutor",
            requested_workers=max_workers,
        )
        super().__init__(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix or "WasmThreadPool",
            initializer=initializer,
            initargs=initargs,
            api_name="concurrent.futures.ThreadPoolExecutor",
        )


def _wrap_context_run_with_worker_identity(
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    worker: ExecutorThread,
) -> tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]:
    context_run = fn.func if isinstance(fn, functools.partial) else fn
    context = getattr(context_run, "__self__", None)
    if not (
        isinstance(context, contextvars.Context)
        and getattr(context_run, "__name__", None) == "run"
    ):
        return fn, args, kwargs

    partial_args = fn.args if isinstance(fn, functools.partial) else ()
    partial_kwargs = fn.keywords if isinstance(fn, functools.partial) else None
    run_args = (*partial_args, *args)
    if not run_args:
        return fn, args, kwargs

    target = run_args[0]
    target_args = run_args[1:]
    target_kwargs = {**(partial_kwargs or {}), **kwargs}
    return (
        _run_context_target_with_worker_identity,
        (context, target, target_args, target_kwargs, worker),
        {},
    )


def _run_context_target_with_worker_identity(
    context: contextvars.Context,
    target: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    worker: ExecutorThread,
) -> Any:
    def call_target() -> Any:
        token = current_thread_var.set(worker)
        try:
            return target(*args, **kwargs)
        finally:
            current_thread_var.reset(token)

    return context.run(call_target)


def _validate_map_buffersize(buffersize: int | None) -> None:
    if buffersize is None:
        return
    if not isinstance(buffersize, int):
        raise TypeError("buffersize must be an integer or None")
    if buffersize < 1:
        raise ValueError("buffersize must be None or > 0")


class AsyncioProcessPoolExecutor(SerializedWasmExecutor):
    """Process-shaped executor adapter with serialized Pyodide execution.

    The adapter keeps the `ProcessPoolExecutor` constructor, submitted future
    behavior, callbacks, and wait methods available for WASM validation. It
    runs all work in the current interpreter and creates no child processes,
    memory isolation, or pickle-copy boundary.
    """

    def __init__(
        self,
        max_workers: int | None = None,
        mp_context: Any | None = None,
        initializer: Callable[..., Any] | None = None,
        initargs: tuple[Any, ...] = (),
        *,
        max_tasks_per_child: int | None = None,
    ) -> None:
        if max_workers is not None and max_workers <= 0:
            raise ValueError("max_workers must be greater than 0")
        if max_tasks_per_child is not None and max_tasks_per_child <= 0:
            raise ValueError("max_tasks_per_child must be greater than 0")
        record_process_executor_serialized(
            max_workers=max_workers,
            mp_context=mp_context,
            max_tasks_per_child=max_tasks_per_child,
        )
        super().__init__(
            max_workers=max_workers,
            thread_name_prefix="WasmProcessPool",
            initializer=initializer,
            initargs=initargs,
            api_name="concurrent.futures.ProcessPoolExecutor",
            process_compatibility=True,
        )

    def map(
        self,
        fn: Callable[..., Any],
        *iterables: Iterable[Any],
        timeout: float | None = None,
        chunksize: int = 1,
        buffersize: int | None = None,
    ) -> Iterator[Any]:
        if chunksize < 1:
            raise ValueError("chunksize must be >= 1")
        return super().map(
            fn,
            *iterables,
            timeout=timeout,
            chunksize=chunksize,
            buffersize=buffersize,
        )
