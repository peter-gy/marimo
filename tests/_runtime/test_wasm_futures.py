# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import functools
import sys
import threading
from types import ModuleType
from typing import Any, cast

import pytest

from marimo._runtime._wasm._concurrency._futures import AsyncioFuture
from marimo._runtime._wasm._concurrency._install import (
    install_wasm_concurrency_shims,
    install_wasm_process_compatibility_shims,
)
from marimo._runtime._wasm._concurrency._wait import (
    UnsupportedWasmConcurrencyError,
)
from tests.conftest import mock_pyodide


def _identity(value: int) -> int:
    return value


def _forbid_run_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    pyodide_module = ModuleType("pyodide")
    ffi_module = ModuleType("pyodide.ffi")

    def run_sync(_awaitable: object) -> object:
        raise AssertionError(
            "immediate timeout must not call pyodide.ffi.run_sync"
        )

    cast(Any, ffi_module).run_sync = run_sync
    cast(Any, pyodide_module).ffi = ffi_module
    monkeypatch.setitem(sys.modules, "pyodide", pyodide_module)
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi_module)


def _install_run_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    pyodide_module = ModuleType("pyodide")
    ffi_module = ModuleType("pyodide.ffi")

    def run_sync(awaitable: object) -> object:
        return asyncio.run(cast(Any, awaitable))

    cast(Any, ffi_module).run_sync = run_sync
    cast(Any, pyodide_module).ffi = ffi_module
    monkeypatch.setitem(sys.modules, "pyodide", pyodide_module)
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi_module)


def _forbid_asyncio_wait_for(monkeypatch: pytest.MonkeyPatch) -> None:
    def wait_for(
        _awaitable: object, *args: object, **kwargs: object
    ) -> object:
        del args
        del kwargs
        raise AssertionError("timed WASM waits must not use asyncio.wait_for")

    monkeypatch.setattr(asyncio, "wait_for", wait_for)


async def _wait_until_done(
    future: concurrent.futures.Future[Any],
    *,
    timeout: float = 1,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not future.done():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("future did not finish before timeout")
        await asyncio.sleep(0)


def test_wasm_thread_pool_map_returns_ordered_results() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=2
            ) as executor:
                assert list(
                    executor.map(lambda value: value + 1, [1, 2, 3])
                ) == [2, 3, 4]
                assert list(
                    executor.map(
                        lambda value: value + 1,
                        [1, 2, 3],
                        chunksize=0,
                    )
                ) == [2, 3, 4]

                consumed: list[int] = []

                def values() -> Any:
                    consumed.append(1)
                    yield 1
                    consumed.append(2)
                    yield 2

                iterator = executor.map(
                    lambda value: value,
                    values(),
                    buffersize=1,
                )
                assert consumed == [1]
                assert next(iterator) == 1
                assert consumed == [1, 2]
                assert list(iterator) == [2]
        finally:
            unpatch()


def test_wasm_thread_pool_initializer_state_persists_across_tasks() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            local = threading.local()

            def initialize() -> None:
                local.ready = True
                local.count = 0

            def work() -> tuple[str, bool, int]:
                local.count += 1
                return (
                    threading.current_thread().name,
                    local.ready,
                    local.count,
                )

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix="lane",
                initializer=initialize,
            ) as executor:
                first = executor.submit(work).result()
                second = executor.submit(work).result()

            assert first[0] == second[0]
            assert first[1:] == (True, 1)
            assert second[1:] == (True, 2)
        finally:
            unpatch()


def test_wasm_thread_pool_initializer_failure_breaks_executor() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:

            def initialize() -> None:
                raise RuntimeError("initializer failed")

            with concurrent.futures.ThreadPoolExecutor(
                initializer=initialize,
            ) as executor:
                future = executor.submit(lambda: "unreachable")
                with pytest.raises(RuntimeError, match="initializer failed"):
                    future.result()
                with pytest.raises(RuntimeError, match="initializer failed"):
                    executor.submit(lambda: "later")
        finally:
            unpatch()


def test_wasm_thread_pool_rejects_noncallable_initializer() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            with pytest.raises(TypeError, match="initializer"):
                concurrent.futures.ThreadPoolExecutor(initializer=object())
        finally:
            unpatch()


def test_wasm_thread_pool_map_validates_buffersize() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                for buffersize in (0, -1):
                    with pytest.raises(ValueError, match="buffersize"):
                        executor.map(
                            lambda value: value,
                            [1],
                            buffersize=buffersize,
                        )
                with pytest.raises(TypeError, match="buffersize"):
                    executor.map(
                        lambda value: value,
                        [1],
                        buffersize=object(),  # type: ignore[arg-type]
                    )
        finally:
            unpatch()


def test_wasm_thread_pool_preserves_awaitable_return_values() -> None:
    async def returned() -> str:
        return "awaited elsewhere"

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                result = executor.submit(lambda: returned()).result()

            assert asyncio.iscoroutine(result)
            result.close()
        finally:
            unpatch()


def test_wasm_executor_current_thread_has_thread_surface() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:

            def worker() -> tuple[bool, bool, bool]:
                current = threading.current_thread()
                return (
                    isinstance(current, threading.Thread),
                    current.ident is not None,
                    current.is_alive(),
                )

            with concurrent.futures.ThreadPoolExecutor() as executor:
                assert executor.submit(worker).result() == (
                    True,
                    True,
                    True,
                )
        finally:
            unpatch()


def test_wasm_thread_pool_result_exception_and_callbacks() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            callback_results: list[tuple[str, int | str]] = []
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=2
            ) as executor:
                success = executor.submit(lambda: 7)
                success.add_done_callback(
                    lambda future: callback_results.append(
                        ("success", future.result())
                    )
                )
                assert success.result() == 7

                failure = executor.submit(
                    lambda: (_ for _ in ()).throw(ValueError("boom"))
                )
                failure.add_done_callback(
                    lambda future: callback_results.append(
                        ("error", type(future.exception()).__name__)
                    )
                )
                with pytest.raises(ValueError, match="boom"):
                    failure.result()

            assert callback_results == [
                ("success", 7),
                ("error", "ValueError"),
            ]
        finally:
            unpatch()


def test_wasm_thread_pool_wait_returns_done_futures() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=2
            ) as executor:
                futures = [
                    executor.submit(_identity, value) for value in range(3)
                ]
                done, not_done = concurrent.futures.wait(futures)
                assert done == set(futures)
                assert not not_done
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_wasm_thread_pool_map_zero_timeout_is_immediate_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        _forbid_run_sync(monkeypatch)
        executor = concurrent.futures.ThreadPoolExecutor()
        try:
            iterator = executor.map(lambda value: value, [1], timeout=0)

            with pytest.raises(concurrent.futures.TimeoutError):
                next(iterator)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            await asyncio.sleep(0)
            unpatch()


def test_wasm_wait_first_exception_returns_all_done_without_exception() -> (
    None
):
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = [
                    executor.submit(_identity, value) for value in range(3)
                ]

                done, not_done = concurrent.futures.wait(
                    futures,
                    return_when=concurrent.futures.FIRST_EXCEPTION,
                )

            assert done == set(futures)
            assert not not_done
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_wasm_thread_pool_shutdown_wait_false_keeps_queued_work() -> (
    None
):
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        executor = concurrent.futures.ThreadPoolExecutor()
        try:
            future = executor.submit(lambda: "done")
            executor.shutdown(wait=False, cancel_futures=False)

            with pytest.raises(RuntimeError, match="cannot schedule"):
                executor.submit(lambda: "later")

            await _wait_until_done(future)
            assert future.result(timeout=0) == "done"
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            await asyncio.sleep(0)
            unpatch()


def test_wasm_wait_rejects_invalid_return_when() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(lambda: "done")
                with pytest.raises(
                    ValueError, match="Invalid return condition"
                ):
                    concurrent.futures.wait(
                        [future],
                        return_when="not-a-return-condition",
                    )
        finally:
            unpatch()


def test_wasm_future_timed_waits_avoid_pyodide_absolute_deadlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        _install_run_sync(monkeypatch)
        _forbid_asyncio_wait_for(monkeypatch)
        try:
            future = AsyncioFuture()

            with pytest.raises(TimeoutError):
                future.result(timeout=0.001)
            assert not future.done()

            done, not_done = concurrent.futures.wait([future], timeout=0.001)
            assert done == set()
            assert not_done == {future}
        finally:
            future.cancel()
            unpatch()


def test_wasm_thread_pool_as_completed_yields_finished_futures() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=2
            ) as executor:
                futures = [
                    executor.submit(_identity, value) for value in range(3)
                ]
                assert sorted(
                    future.result()
                    for future in concurrent.futures.as_completed(futures)
                ) == [0, 1, 2]
        finally:
            unpatch()


def test_wasm_as_completed_deduplicates_input_futures() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(lambda: "done")

                assert list(
                    concurrent.futures.as_completed(
                        [future, future], timeout=1
                    )
                ) == [future]
        finally:
            unpatch()


def test_wasm_as_completed_timeout_zero_yields_done_futures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(lambda: "done")
                assert future.result(timeout=1) == "done"
                _forbid_run_sync(monkeypatch)

                assert list(
                    concurrent.futures.as_completed([future], timeout=0)
                ) == [future]
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_wasm_future_negative_timeouts_are_immediate_polls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        future: concurrent.futures.Future[str] = AsyncioFuture()
        _forbid_run_sync(monkeypatch)
        try:
            assert not future.done()
            with pytest.raises(concurrent.futures.TimeoutError):
                future.result(timeout=-1)
            with pytest.raises(concurrent.futures.TimeoutError):
                future.exception(timeout=-1)

            done, not_done = concurrent.futures.wait([future], timeout=-1)
            assert done == set()
            assert not_done == {future}

            with pytest.raises(concurrent.futures.TimeoutError):
                next(concurrent.futures.as_completed([future], timeout=-1))
        finally:
            future.cancel()
            unpatch()


@pytest.mark.asyncio
async def test_wasm_mixed_as_completed_immediate_timeout_yields_done_futures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        _forbid_run_sync(monkeypatch)
        try:
            foreign_done: concurrent.futures.Future[str] = (
                concurrent.futures.Future()
            )
            foreign_done.set_result("foreign")
            foreign_pending: concurrent.futures.Future[str] = (
                concurrent.futures.Future()
            )
            shim_pending = AsyncioFuture()
            iterator = concurrent.futures.as_completed(
                [foreign_done, shim_pending, foreign_pending],
                timeout=0,
            )
            assert next(iterator) is foreign_done
            with pytest.raises(concurrent.futures.TimeoutError):
                next(iterator)

            shim_pending.set_result("shim")
            iterator = concurrent.futures.as_completed(
                [foreign_done, shim_pending, foreign_pending],
                timeout=-1,
            )
            assert {next(iterator), next(iterator)} == {
                foreign_done,
                shim_pending,
            }
            with pytest.raises(concurrent.futures.TimeoutError):
                next(iterator)
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_wasm_mixed_as_completed_immediate_timeout_with_no_done_futures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        _forbid_run_sync(monkeypatch)
        try:
            foreign_pending: concurrent.futures.Future[str] = (
                concurrent.futures.Future()
            )
            shim_pending = AsyncioFuture()
            try:
                with pytest.raises(concurrent.futures.TimeoutError):
                    next(
                        concurrent.futures.as_completed(
                            [shim_pending, foreign_pending], timeout=-1
                        )
                    )
            finally:
                shim_pending.cancel()
        finally:
            unpatch()


def test_wasm_thread_pool_reuses_worker_local_state_until_shutdown() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            local = threading.local()

            def write_value() -> str:
                local.value = "worker"
                return local.value

            def read_value() -> bool:
                return hasattr(local, "value")

            with concurrent.futures.ThreadPoolExecutor() as executor:
                assert executor.submit(write_value).result() == "worker"
                assert executor.submit(read_value).result() is True
            assert not hasattr(local, "value")
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_wasm_thread_pool_does_not_inherit_ambient_contextvars() -> None:
    ambient = contextvars.ContextVar("ambient", default="unset")
    ambient.set("parent")

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(lambda: ambient.get())
                await _wait_until_done(future)

                assert future.result(timeout=0) == "unset"
                assert ambient.get() == "parent"
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_asyncio_to_thread_keeps_its_contextvars_contract() -> None:
    ambient = contextvars.ContextVar("ambient", default="unset")
    ambient.set("parent")

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            assert await asyncio.to_thread(lambda: ambient.get()) == "parent"
            assert ambient.get() == "parent"
        finally:
            await asyncio.get_running_loop().shutdown_default_executor()
            unpatch()


@pytest.mark.asyncio
async def test_asyncio_to_thread_uses_worker_thread_local_identity() -> None:
    ambient = contextvars.ContextVar("ambient", default="unset")
    ambient.set("parent")

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            local = threading.local()
            local.value = "parent"

            def worker() -> tuple[str, bool, str]:
                return (
                    ambient.get(),
                    hasattr(local, "value"),
                    threading.current_thread().name,
                )

            (
                ambient_value,
                saw_parent_local,
                thread_name,
            ) = await asyncio.to_thread(worker)

            assert ambient_value == "parent"
            assert saw_parent_local is False
            assert thread_name != "MainThread"
            assert local.value == "parent"
        finally:
            await asyncio.get_running_loop().shutdown_default_executor()
            unpatch()


def test_context_run_executor_keeps_worker_identity_temporary() -> None:
    ambient = contextvars.ContextVar("ambient", default="unset")
    ambient.set("parent")
    context = contextvars.copy_context()

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            local = threading.local()
            local.value = "parent"

            def worker() -> tuple[str, bool, str]:
                return (
                    ambient.get(),
                    hasattr(local, "value"),
                    threading.current_thread().name,
                )

            with concurrent.futures.ThreadPoolExecutor() as executor:
                ambient_value, saw_parent_local, thread_name = executor.submit(
                    context.run, worker
                ).result()

            assert ambient_value == "parent"
            assert saw_parent_local is False
            assert thread_name != "MainThread"
            assert context.run(lambda: threading.current_thread().name) == (
                "MainThread"
            )
            assert local.value == "parent"
        finally:
            unpatch()


def test_partial_context_run_executor_keeps_worker_identity_temporary() -> (
    None
):
    ambient = contextvars.ContextVar("ambient", default="unset")
    ambient.set("parent")
    context = contextvars.copy_context()

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            local = threading.local()
            local.value = "parent"

            def worker() -> tuple[str, bool, str]:
                return (
                    ambient.get(),
                    hasattr(local, "value"),
                    threading.current_thread().name,
                )

            with concurrent.futures.ThreadPoolExecutor() as executor:
                ambient_value, saw_parent_local, thread_name = executor.submit(
                    functools.partial(context.run, worker)
                ).result()

            assert ambient_value == "parent"
            assert saw_parent_local is False
            assert thread_name != "MainThread"
            assert context.run(lambda: threading.current_thread().name) == (
                "MainThread"
            )
            assert local.value == "parent"
        finally:
            unpatch()


def test_wasm_process_pool_executor_is_serialized() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            with concurrent.futures.ProcessPoolExecutor(
                max_workers=2
            ) as executor:
                assert executor.submit(lambda: 42).result() == 42
                assert list(
                    executor.map(lambda value: value * 2, [1, 2, 3])
                ) == [2, 4, 6]
                with pytest.raises(RuntimeError, match="process boom"):
                    executor.submit(
                        lambda: (_ for _ in ()).throw(
                            RuntimeError("process boom")
                        )
                    ).result()
        finally:
            unpatch()


def test_wasm_process_pool_executor_validates_process_parameters() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            with pytest.raises(ValueError, match="max_tasks_per_child"):
                concurrent.futures.ProcessPoolExecutor(
                    max_tasks_per_child=0,
                )
            with concurrent.futures.ProcessPoolExecutor() as executor:
                assert list(
                    executor.map(
                        lambda value: value,
                        [1, 2],
                        buffersize=1,
                    )
                ) == [1, 2]
                with pytest.raises(ValueError, match="chunksize"):
                    list(executor.map(lambda value: value, [1], chunksize=0))
                with pytest.raises(ValueError, match="buffersize"):
                    list(
                        executor.map(
                            lambda value: value,
                            [1],
                            buffersize=0,
                        )
                    )
        finally:
            unpatch()


def test_wasm_process_pool_initializer_state_persists_across_tasks() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            local = threading.local()

            def initialize() -> None:
                local.ready = True
                local.count = 0

            def work() -> tuple[str, bool, int]:
                local.count += 1
                return (
                    threading.current_thread().name,
                    local.ready,
                    local.count,
                )

            with concurrent.futures.ProcessPoolExecutor(
                max_workers=2,
                initializer=initialize,
            ) as executor:
                first = executor.submit(work).result()
                second = executor.submit(work).result()

            assert first[0] == second[0]
            assert first[1:] == (True, 1)
            assert second[1:] == (True, 2)
        finally:
            unpatch()


def test_wasm_process_pool_initializer_failure_breaks_executor() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:

            def initialize() -> None:
                raise RuntimeError("process initializer failed")

            with concurrent.futures.ProcessPoolExecutor(
                initializer=initialize,
            ) as executor:
                future = executor.submit(lambda: "unreachable")
                with pytest.raises(
                    RuntimeError, match="process initializer failed"
                ):
                    future.result()
                with pytest.raises(RuntimeError, match="initializer failed"):
                    executor.submit(lambda: "later")
        finally:
            unpatch()


def test_wasm_process_pool_rejects_noncallable_initializer() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            with pytest.raises(TypeError, match="initializer"):
                concurrent.futures.ProcessPoolExecutor(initializer=object())
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_wasm_process_pool_does_not_inherit_ambient_contextvars() -> (
    None
):
    ambient = contextvars.ContextVar("ambient", default="unset")
    ambient.set("parent")

    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            with concurrent.futures.ProcessPoolExecutor() as executor:
                future = executor.submit(lambda: ambient.get())
                await _wait_until_done(future)

                assert future.result(timeout=0) == "unset"
                assert ambient.get() == "parent"
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_wasm_wait_rejects_mixed_pending_foreign_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        _forbid_run_sync(monkeypatch)
        shim_future = AsyncioFuture()
        try:
            foreign_future: concurrent.futures.Future[str] = (
                concurrent.futures.Future()
            )
            with pytest.raises(
                UnsupportedWasmConcurrencyError,
                match="mixed pending",
            ):
                concurrent.futures.wait([shim_future, foreign_future])
            with pytest.raises(concurrent.futures.TimeoutError):
                shim_future.result(timeout=0)
        finally:
            shim_future.set_result("shim")
            assert shim_future.result(timeout=0) == "shim"
            unpatch()
