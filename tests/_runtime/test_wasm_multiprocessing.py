# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import multiprocessing
import queue
import sys
import threading
from multiprocessing.context import BaseContext
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast

import pytest

from marimo._runtime._wasm._concurrency._futures import AsyncioFuture
from marimo._runtime._wasm._concurrency._install import (
    install_wasm_process_compatibility_shims,
)
from marimo._runtime._wasm._concurrency._mp_pool import AsyncPoolResult
from marimo._runtime._wasm._concurrency._wait import (
    UnsupportedWasmConcurrencyError,
)
from tests.conftest import mock_pyodide

if TYPE_CHECKING:
    from collections.abc import Callable


def _forbid_run_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    pyodide_module = ModuleType("pyodide")
    ffi_module = ModuleType("pyodide.ffi")

    def run_sync(_awaitable: object) -> object:
        raise AssertionError("timeout=0 must not call pyodide.ffi.run_sync")

    cast(Any, ffi_module).run_sync = run_sync
    cast(Any, pyodide_module).ffi = ffi_module
    monkeypatch.setitem(sys.modules, "pyodide", pyodide_module)
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi_module)


def _forbid_jspi_promising_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    pyodide_module = ModuleType("pyodide")
    ffi_module = ModuleType("pyodide.ffi")

    def run_sync(_awaitable: object) -> object:
        raise AssertionError("can_run_sync=False should avoid run_sync")

    cast(Any, ffi_module).run_sync = run_sync
    cast(Any, ffi_module).can_run_sync = lambda: False
    cast(Any, pyodide_module).ffi = ffi_module
    monkeypatch.setitem(sys.modules, "pyodide", pyodide_module)
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi_module)


async def _wait_until(predicate: Any, *, timeout: float = 1) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition was not reached before timeout")
        await asyncio.sleep(0)


def test_wasm_process_and_queue_run_with_explicit_process_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=2
            ) as executor:
                assert executor.submit(lambda: 42).result() == 42

            values: Any = multiprocessing.Queue()

            def worker(output: Any) -> None:
                output.put("ok")

            process = multiprocessing.Process(target=worker, args=(values,))
            process.start()
            process.join(timeout=1)

            assert not process.is_alive()
            assert process.exitcode == 0
            assert values.get(block=False) == "ok"
            assert values.empty()

            bounded: Any = multiprocessing.Queue(maxsize=1)
            bounded.put_nowait("first")
            assert bounded.full()
            with pytest.raises(queue.Full):
                bounded.put_nowait("second")
            _forbid_run_sync(monkeypatch)
            with pytest.raises(queue.Full):
                bounded.put("second", timeout=0)
            empty_values: Any = multiprocessing.Queue()
            with pytest.raises(queue.Empty):
                empty_values.get(timeout=0)
            with pytest.raises(queue.Empty):
                empty_values.get(timeout=-1)
            with pytest.raises(queue.Full):
                bounded.put("negative", timeout=-1)
            assert bounded.get_nowait() == "first"
            bounded.put("second")
            assert bounded.get(timeout=-1) == "second"
            bounded.put("second")
            assert bounded.get(timeout=0) == "second"
            with pytest.raises(TypeError):
                cast(Any, multiprocessing.SimpleQueue)(1)
            simple_queue: Any = multiprocessing.SimpleQueue()
            simple_queue.put("simple")
            assert simple_queue.get() == "simple"
            for unsupported_attr in (
                "qsize",
                "full",
                "put_nowait",
                "get_nowait",
            ):
                assert not hasattr(simple_queue, unsupported_attr)
            with pytest.raises(TypeError):
                cast(Any, simple_queue).put("blocked", block=False)
            with pytest.raises(TypeError):
                cast(Any, simple_queue).get(block=False)
        finally:
            unpatch()


def test_wasm_process_unpatch_cleans_lazily_imported_pool_module() -> None:
    missing = object()
    module_names = ("multiprocessing.managers", "multiprocessing.pool")
    saved_modules = {
        name: sys.modules.get(name, missing) for name in module_names
    }
    saved_attrs = {
        name: getattr(multiprocessing, name, missing)
        for name in ("managers", "pool")
    }

    try:
        for name in module_names:
            sys.modules.pop(name, None)
        for name in ("managers", "pool"):
            if hasattr(multiprocessing, name):
                delattr(multiprocessing, name)

        with mock_pyodide():
            unpatch = install_wasm_process_compatibility_shims()
            try:
                assert "multiprocessing.pool" in sys.modules
            finally:
                unpatch()

        assert "multiprocessing.pool" not in sys.modules
        assert not hasattr(multiprocessing, "pool")

        from multiprocessing import managers, pool

        assert managers.SyncManager._registry["Pool"][0] is pool.Pool
    finally:
        for name in module_names:
            sys.modules.pop(name, None)
            saved_module = saved_modules[name]
            if saved_module is not missing:
                sys.modules[name] = saved_module  # type: ignore[assignment]
        for name in ("managers", "pool"):
            if hasattr(multiprocessing, name):
                delattr(multiprocessing, name)
            saved_attr = saved_attrs[name]
            if saved_attr is not missing:
                setattr(multiprocessing, name, saved_attr)


def test_wasm_multiprocessing_start_method_helpers() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            assert multiprocessing.cpu_count() == 1
            assert multiprocessing.get_all_start_methods() == ["spawn"]
            assert multiprocessing.get_start_method() == "spawn"
            multiprocessing.set_start_method("spawn")
            with pytest.raises(ValueError, match="spawn"):
                multiprocessing.set_start_method("fork")

            current = multiprocessing.current_process()
            assert current.name == "MainProcess"
            assert current.is_alive()
            assert multiprocessing.parent_process() is None
        finally:
            unpatch()


def test_wasm_multiprocessing_blocks_process_only_factories() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            unsupported_calls: list[Callable[[], object]] = [
                lambda: multiprocessing.Pipe(),
                lambda: multiprocessing.Manager(),
                lambda: multiprocessing.JoinableQueue(),
                lambda: multiprocessing.Value("i", 1),
                lambda: multiprocessing.Array("i", [1]),
                lambda: multiprocessing.RawValue("i", 1),
                lambda: multiprocessing.RawArray("i", [1]),
            ]
            for call in unsupported_calls:
                with pytest.raises(
                    UnsupportedWasmConcurrencyError,
                    match="not supported",
                ):
                    call()

            ctx = multiprocessing.get_context("spawn")
            context_calls: list[Callable[[], object]] = [
                lambda: ctx.Pipe(),
                lambda: ctx.Manager(),
                lambda: ctx.JoinableQueue(),
                lambda: ctx.Value("i", 1),
                lambda: ctx.Array("i", [1]),
                lambda: ctx.RawValue("i", 1),
                lambda: ctx.RawArray("i", [1]),
            ]
            for call in context_calls:
                with pytest.raises(
                    UnsupportedWasmConcurrencyError,
                    match="not supported",
                ):
                    call()
        finally:
            unpatch()


def test_wasm_multiprocessing_context_queue_factory() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            ctx = multiprocessing.get_context("spawn")
            assert isinstance(ctx, BaseContext)

            values = ctx.Queue()
            values.put("context")
            assert values.get(block=False) == "context"
            with pytest.raises(TypeError):
                cast(Any, ctx.SimpleQueue)(1)
            simple_values = ctx.SimpleQueue()
            simple_values.put("context-simple")
            assert simple_values.get() == "context-simple"
            assert not hasattr(simple_values, "put_nowait")
            with pytest.raises(TypeError):
                cast(Any, simple_values).put("blocked", block=False)
        finally:
            unpatch()


def test_wasm_multiprocessing_context_process_metadata() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            ctx = multiprocessing.get_context("spawn")
            values = ctx.Queue()

            def context_worker(output: Any) -> None:
                current = multiprocessing.current_process()
                parent = multiprocessing.parent_process()
                output.put(
                    {
                        "name": current.name,
                        "pid": current.pid,
                        "parent": None if parent is None else parent.name,
                    }
                )

            process = ctx.Process(
                target=context_worker,
                args=(values,),
                name="context-child",
            )
            process.start()
            process.join(timeout=1)
            assert not process.is_alive()
            assert process.exitcode == 0
            assert values.get(block=False) == {
                "name": "context-child",
                "pid": process.pid,
                "parent": "MainProcess",
            }
        finally:
            unpatch()


def test_wasm_multiprocessing_context_pool_apply() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            ctx = multiprocessing.get_context("spawn")
            with ctx.Pool(2) as pool:
                assert pool.apply(lambda value: value + 1, (1,)) == 2
        finally:
            unpatch()


def test_wasm_process_lifecycle_rejects_repeated_start() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            process = multiprocessing.Process(target=lambda: None)
            assert process.pid is None
            assert process.exitcode is None
            assert not process.is_alive()
            assert process.exitcode is None

            process.start()
            with pytest.raises(RuntimeError, match="started once"):
                process.start()
            process.join(timeout=1)
            assert not process.is_alive()
            assert process.exitcode == 0

            process.kill()

            assert process.exitcode == 0
        finally:
            unpatch()


def test_wasm_process_kill_and_terminate_before_start_raise() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            process = multiprocessing.Process(target=lambda: None)

            with pytest.raises(ValueError, match="not started"):
                process.kill()
            with pytest.raises(ValueError, match="not started"):
                process.terminate()
        finally:
            unpatch()


def test_wasm_process_system_exit_sets_exitcode() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        failures: list[tuple[str, str]] = []
        old_excepthook = threading.excepthook

        def capture_excepthook(args: threading.ExceptHookArgs) -> None:
            failures.append((args.exc_type.__name__, str(args.exc_value)))

        threading.excepthook = capture_excepthook
        try:
            process = multiprocessing.Process(target=lambda: sys.exit(5))
            process.start()
            process.join(timeout=1)

            assert not process.is_alive()
            assert process.exitcode == 5
            assert failures == []
        finally:
            threading.excepthook = old_excepthook
            unpatch()


def test_wasm_process_system_exit_none_is_success() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            process = multiprocessing.Process(target=lambda: sys.exit())
            process.start()
            process.join(timeout=1)

            assert not process.is_alive()
            assert process.exitcode == 0
        finally:
            unpatch()


def test_wasm_process_subclass_run_is_called() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            values: Any = multiprocessing.Queue()

            class CustomProcess(multiprocessing.Process):
                def run(self) -> None:
                    values.put(("run-called", self.name))

            process = CustomProcess(name="custom-process")
            process.start()
            process.join(timeout=1)

            assert not process.is_alive()
            assert process.exitcode == 0
            assert values.get(block=False) == ("run-called", "custom-process")
        finally:
            unpatch()


def test_wasm_process_blocks_os_handle_properties() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            process = multiprocessing.Process(target=lambda: None)
            process.start()
            process.join(timeout=1)

            with pytest.raises(
                UnsupportedWasmConcurrencyError,
                match="authkey",
            ):
                _authkey = process.authkey
            with pytest.raises(
                UnsupportedWasmConcurrencyError,
                match="sentinel",
            ):
                _sentinel = process.sentinel
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_wasm_process_close_lifecycle() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:

            async def wait_forever() -> None:
                await asyncio.sleep(10)

            process = multiprocessing.Process(target=wait_forever)
            process.start()
            await asyncio.sleep(0)
            assert process.is_alive()
            with pytest.raises(ValueError, match="running process"):
                process.close()
            process.kill()
            await _wait_until(lambda: not process.is_alive())
            assert not process.is_alive()
            process.close()
            with pytest.raises(ValueError, match="closed"):
                process.is_alive()
            with pytest.raises(ValueError, match="closed"):
                process.join(timeout=0)

            fresh = multiprocessing.Process(target=lambda: None)
            fresh.close()
            with pytest.raises(ValueError, match="closed"):
                fresh.start()
        finally:
            unpatch()


def test_wasm_process_cancelled_error_gets_failure_exitcode() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        failures: list[tuple[str, str]] = []
        old_excepthook = threading.excepthook

        def capture_excepthook(args: threading.ExceptHookArgs) -> None:
            failures.append((args.exc_type.__name__, str(args.exc_value)))

        threading.excepthook = capture_excepthook
        try:

            async def worker() -> None:
                raise asyncio.CancelledError("process cancel")

            process = multiprocessing.Process(target=worker)
            process.start()
            process.join(timeout=1)

            assert not process.is_alive()
            assert process.exitcode == 1
            assert failures == [("CancelledError", "process cancel")]
        finally:
            threading.excepthook = old_excepthook
            unpatch()


def test_wasm_process_daemon_cannot_change_after_start() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            process = multiprocessing.Process(target=lambda: None)
            process.daemon = True
            process.start()
            process.join(timeout=1)

            assert not process.is_alive()
            with pytest.raises(RuntimeError, match="daemon status"):
                process.daemon = False
        finally:
            unpatch()


def test_wasm_multiprocessing_pool_serialized_methods() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            with multiprocessing.Pool(2) as pool:
                assert pool.apply(lambda value: value + 1, (1,)) == 2
                assert pool.map(lambda value: value * 2, [1, 2, 3]) == [
                    2,
                    4,
                    6,
                ]
                assert pool.starmap(lambda a, b: a + b, [(1, 2), (3, 4)]) == [
                    3,
                    7,
                ]
                assert list(pool.imap(lambda value: value + 10, [1, 2])) == [
                    11,
                    12,
                ]
                assert sorted(
                    pool.imap_unordered(lambda value: value + 20, [1, 2])
                ) == [21, 22]
                assert pool.apply_async(lambda: "async").get() == "async"
        finally:
            unpatch()


def test_wasm_multiprocessing_pool_validates_constructor_parameters() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            with pytest.raises(ValueError, match="number of processes"):
                multiprocessing.Pool(0)
            with pytest.raises(TypeError, match="initializer"):
                multiprocessing.Pool(1, initializer=object())
            for maxtasksperchild in (0, -1, 1.5, "bad"):
                with pytest.raises(ValueError, match="maxtasksperchild"):
                    multiprocessing.Pool(1, maxtasksperchild=maxtasksperchild)
        finally:
            unpatch()


def test_wasm_pool_imap_rejects_invalid_chunksize() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            with multiprocessing.Pool(1) as pool:
                with pytest.raises(ValueError, match="Chunksize must be 1"):
                    pool.imap(lambda value: value, [1], chunksize=0)
                with pytest.raises(ValueError, match="Chunksize must be 1"):
                    pool.imap_unordered(lambda value: value, [1], chunksize=0)
        finally:
            unpatch()


def test_wasm_pool_imap_is_lazy() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            consumed: list[int] = []

            def values() -> Any:
                consumed.append(1)
                yield 1
                consumed.append(2)
                yield 2

            with multiprocessing.Pool(1) as pool:
                results = pool.imap(lambda value: value + 1, values())

                assert consumed == []
                assert next(results) == 2
                assert consumed == [1]
                assert next(results) == 3
                assert consumed == [1, 2]
        finally:
            unpatch()


def test_wasm_pool_rejects_closed_work_before_consuming_iterables() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        pool = multiprocessing.Pool(1)
        try:
            pool.close()
            consumed: list[str] = []

            def values(label: str) -> Any:
                consumed.append(label)
                yield 1

            with pytest.raises(ValueError, match="Pool not running"):
                pool.map_async(lambda value: value, values("map"))
            with pytest.raises(ValueError, match="Pool not running"):
                pool.starmap_async(
                    lambda value: value,
                    ((value,) for value in values("starmap")),
                )
            with pytest.raises(ValueError, match="Pool not running"):
                list(pool.imap(lambda value: value, [1], chunksize=0))

            assert consumed == []
        finally:
            pool.join()
            unpatch()


def test_wasm_multiprocessing_pool_callbacks() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            callbacks: list[tuple[str, int | str]] = []
            with multiprocessing.Pool(1) as pool:
                result = pool.apply_async(
                    lambda: 10,
                    callback=lambda value: callbacks.append(("ok", value)),
                )
                assert result.get() == 10
                assert result.ready()
                assert result.successful()

                failed = pool.apply_async(
                    lambda: (_ for _ in ()).throw(ValueError("bad")),
                    error_callback=lambda exc: callbacks.append(
                        ("error", type(exc).__name__)
                    ),
                )
                with pytest.raises(ValueError, match="bad"):
                    failed.get()
                assert failed.ready()
                assert not failed.successful()

                user_timeout = pool.apply_async(
                    lambda: (_ for _ in ()).throw(TimeoutError("user timeout"))
                )
                with pytest.raises(TimeoutError, match="user timeout"):
                    user_timeout.get()

            assert callbacks == [("ok", 10), ("error", "ValueError")]
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_wasm_pool_async_get_timeout_uses_multiprocessing_error() -> (
    None
):
    future = AsyncioFuture()
    result = AsyncPoolResult(future)

    with pytest.raises(multiprocessing.TimeoutError):
        result.get(timeout=0)

    future.set_result("released")
    assert result.get(timeout=0) == "released"


@pytest.mark.asyncio
async def test_wasm_pool_async_wait_reports_missing_jspi_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    future = AsyncioFuture()
    result = AsyncPoolResult(future)
    _forbid_jspi_promising_frame(monkeypatch)

    with pytest.raises(
        UnsupportedWasmConcurrencyError,
        match="JSPI promising frame",
    ):
        result.wait(timeout=1)

    future.set_result("released")
    assert result.get(timeout=0) == "released"


@pytest.mark.asyncio
async def test_wasm_pool_terminate_cancels_queued_work() -> None:
    native_event = threading.Event
    native_thread = threading.Thread
    pool: Any = None
    result: Any = None
    blocker_release = native_event()
    blocker_started = native_event()
    terminated = native_event()
    failures: list[BaseException] = []

    def terminate_pool() -> None:
        try:
            if pool is None or result is None:
                raise AssertionError("pool was not initialized")
            if not blocker_started.wait(1):
                raise AssertionError("pool blocker did not start")
            pool.terminate()
            assert result.ready()
            with pytest.raises(concurrent.futures.CancelledError):
                result.get(timeout=0)
        except BaseException as exc:
            failures.append(exc)
        finally:
            blocker_release.set()
            terminated.set()

    controller = native_thread(target=terminate_pool)

    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        pool = multiprocessing.Pool(1)
        try:
            pool.apply_async(
                lambda: (
                    blocker_started.set(),
                    blocker_release.wait(),
                    "blocker",
                )[-1]
            )

            result = pool.apply_async(lambda: "queued")

            controller.start()
            await _wait_until(terminated.is_set, timeout=2)
            controller.join(timeout=1)
            if failures:
                raise failures[0]
            pool.join()
        finally:
            blocker_release.set()
            pool.terminate()
            await asyncio.sleep(0)
            pool.join()
            unpatch()


def test_wasm_multiprocessing_sync_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        _forbid_run_sync(monkeypatch)
        try:
            event = multiprocessing.Event()
            assert not event.is_set()
            event.set()
            assert event.wait(0)

            lock = multiprocessing.Lock()
            assert lock.acquire(block=False)
            assert not lock.acquire(block=False, timeout=1)
            lock.release()
            with pytest.raises(ValueError):
                lock.release()
            assert lock.acquire(timeout=None)
            assert not lock.acquire(timeout=-1)
            lock.release()

            rlock = multiprocessing.RLock()
            assert rlock.acquire(block=False)
            assert rlock.acquire(timeout=None)
            rlock.release()
            rlock.release()
            with pytest.raises(AssertionError):
                rlock.release()
            with rlock:
                with rlock:
                    assert True

            semaphore = multiprocessing.Semaphore(0)
            assert not semaphore.acquire(block=False)
            assert not semaphore.acquire(block=False, timeout=1)
            assert not semaphore.acquire(timeout=-1)
            assert not semaphore.acquire(timeout=0)
            semaphore.release()
            assert semaphore.acquire(timeout=None)

            bounded = multiprocessing.BoundedSemaphore(1)
            assert bounded.acquire(block=False)
            bounded.release()
            assert bounded.acquire(timeout=0)
            bounded.release()

            condition = multiprocessing.Condition()
            assert condition.acquire(block=False)
            condition.release()
            with condition:
                assert condition.wait(timeout=0) is False

            barrier = multiprocessing.Barrier(1)
            assert barrier.wait(timeout=0) == 0
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_wasm_active_children_tracks_process_lifecycle() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            blocker = asyncio.Event()

            async def wait_forever() -> None:
                await blocker.wait()

            process = multiprocessing.Process(target=wait_forever)
            process.start()
            await asyncio.sleep(0)
            assert process in multiprocessing.active_children()

            process.kill()
            await asyncio.sleep(0)
            assert process not in multiprocessing.active_children()
            assert process.exitcode == -1
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_wasm_process_current_process_survives_await() -> None:
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            values: Any = multiprocessing.Queue()

            async def worker(output: Any) -> None:
                before = multiprocessing.current_process()
                await asyncio.sleep(0)
                after = multiprocessing.current_process()
                parent = multiprocessing.parent_process()
                output.put(
                    {
                        "before": before.name,
                        "after": after.name,
                        "same": before is after,
                        "parent": None if parent is None else parent.name,
                    }
                )

            process = multiprocessing.Process(
                target=worker,
                args=(values,),
                name="async-process",
            )
            process.start()
            await _wait_until(lambda: not values.empty())

            assert values.get(block=False) == {
                "before": "async-process",
                "after": "async-process",
                "same": True,
                "parent": "MainProcess",
            }
            process.join(timeout=0)
            assert process.exitcode == 0
            assert multiprocessing.current_process().name == "MainProcess"
            assert multiprocessing.parent_process() is None
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_wasm_process_current_process_resets_after_async_failure() -> (
    None
):
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        failures: list[tuple[str, str]] = []
        old_excepthook = threading.excepthook

        def capture_excepthook(args: threading.ExceptHookArgs) -> None:
            failures.append((args.exc_type.__name__, str(args.exc_value)))

        threading.excepthook = capture_excepthook
        try:

            async def worker() -> None:
                assert multiprocessing.current_process().name == (
                    "failing-process"
                )
                await asyncio.sleep(0)
                raise RuntimeError("process failed")

            process = multiprocessing.Process(
                target=worker,
                name="failing-process",
            )
            process.start()
            await _wait_until(lambda: not process.is_alive())

            process.join(timeout=0)
            assert process.exitcode == 1
            assert failures == [("RuntimeError", "process failed")]
            assert multiprocessing.current_process().name == "MainProcess"
            assert multiprocessing.parent_process() is None
        finally:
            threading.excepthook = old_excepthook
            unpatch()


@pytest.mark.asyncio
async def test_wasm_process_does_not_inherit_ambient_contextvars() -> None:
    ambient = contextvars.ContextVar("ambient", default="unset")
    ambient.set("parent")

    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            values: Any = multiprocessing.Queue()

            async def worker(output: Any) -> None:
                output.put(ambient.get())
                ambient.set("child")
                await asyncio.sleep(0)
                output.put(ambient.get())

            process = multiprocessing.Process(target=worker, args=(values,))
            process.start()
            await _wait_until(lambda: values.qsize() == 2)

            assert values.get(block=False) == "unset"
            assert values.get(block=False) == "child"
            assert ambient.get() == "parent"
            process.join(timeout=0)
            assert process.exitcode == 0
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_wasm_process_child_thread_keeps_process_identity() -> None:
    ambient = contextvars.ContextVar("nested_process_ambient", default="unset")
    ambient.set("parent")

    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            values: Any = multiprocessing.Queue()

            async def worker(output: Any) -> None:
                ambient.set("process")
                thread_done = asyncio.Event()

                def thread_worker() -> None:
                    current = multiprocessing.current_process()
                    parent = multiprocessing.parent_process()
                    output.put(
                        {
                            "current": current.name,
                            "current_alive": current.is_alive(),
                            "parent": None if parent is None else parent.name,
                            "ambient": ambient.get(),
                        }
                    )
                    thread_done.set()

                thread = threading.Thread(target=thread_worker)
                thread.start()
                await thread_done.wait()
                output.put({"process_ambient": ambient.get()})

            process = multiprocessing.Process(
                target=worker,
                args=(values,),
                name="process-with-thread",
            )
            process.start()
            await _wait_until(lambda: values.qsize() == 2)
            process.join(timeout=0)

            assert process.exitcode == 0
            records = [values.get(block=False), values.get(block=False)]
            child_record = next(
                record for record in records if "current" in record
            )
            process_record = next(
                record for record in records if "process_ambient" in record
            )
            assert child_record == {
                "current": "process-with-thread",
                "current_alive": True,
                "parent": "MainProcess",
                "ambient": "unset",
            }
            assert process_record == {"process_ambient": "process"}
            assert ambient.get() == "parent"
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_process_kill_refuses_unpatch_until_cancelled_task_finishes() -> (
    None
):
    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()

        async def wait_forever() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup_started.set()
                await release_cleanup.wait()
                raise

        process = multiprocessing.Process(target=wait_forever)
        try:
            process.start()
            await asyncio.sleep(0)
            process.kill()

            assert process.exitcode is None
            await asyncio.wait_for(cleanup_started.wait(), timeout=1)
            assert process.is_alive()
            assert process in multiprocessing.active_children()

            with pytest.raises(RuntimeError, match="while live"):
                unpatch()
        finally:
            release_cleanup.set()
            await _wait_until(lambda: not process.is_alive())
            if process.is_alive():
                process.kill()
            unpatch()

        assert not process.is_alive()
        assert process not in multiprocessing.active_children()
        assert process.exitcode == -1
