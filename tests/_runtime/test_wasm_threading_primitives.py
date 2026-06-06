# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import asyncio
import contextvars
import logging
import queue
import sys
import threading
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast

import pytest

from marimo._runtime._wasm._concurrency._install import (
    install_wasm_concurrency_shims,
)
from marimo._runtime._wasm._concurrency._wait import (
    UnsupportedWasmConcurrencyError,
    cooperative_wait,
)
from tests.conftest import mock_pyodide

if TYPE_CHECKING:
    from collections.abc import Generator


class AwaitableProbe:
    def __await__(self) -> Generator[None, None, None]:
        if False:
            yield None
        return None


def _install_run_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    pyodide_module = ModuleType("pyodide")
    ffi_module = ModuleType("pyodide.ffi")

    def run_sync(awaitable: object) -> object:
        return asyncio.run(cast(Any, awaitable))

    cast(Any, ffi_module).run_sync = run_sync
    cast(Any, pyodide_module).ffi = ffi_module
    monkeypatch.setitem(sys.modules, "pyodide", pyodide_module)
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi_module)


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


def _forbid_asyncio_wait_for(monkeypatch: pytest.MonkeyPatch) -> None:
    def wait_for(
        _awaitable: object, *args: object, **kwargs: object
    ) -> object:
        del args
        del kwargs
        raise AssertionError("timed WASM waits must not use asyncio.wait_for")

    monkeypatch.setattr(asyncio, "wait_for", wait_for)


async def _wait_until(condition: object, timeout: float = 1) -> None:
    predicate = cast(Any, condition)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition was not met before timeout")
        await asyncio.sleep(0)


def test_wasm_concurrency_runs_threads_and_thread_locals_serially() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            local = threading.local()
            results: list[tuple[str, int]] = []

            def worker(value: int) -> None:
                local.value = value
                results.append((threading.current_thread().name, local.value))

            t1 = threading.Thread(target=worker, args=(1,), name="one")
            t2 = threading.Thread(target=worker, args=(2,), name="two")

            t1.start()
            t2.start()
            t1.join(timeout=1)
            t2.join(timeout=1)

            assert not t1.is_alive()
            assert not t2.is_alive()
            assert results == [("one", 1), ("two", 2)]
            assert not hasattr(local, "value")
        finally:
            unpatch()


def test_wasm_thread_identity_is_unset_until_start() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            thread = threading.Thread(target=lambda: None)

            assert thread.ident is None
            assert thread.native_id is None

            thread.start()
            assert thread.ident is not None
            assert thread.native_id == thread.ident
            thread.join(timeout=1)
            assert thread.ident is not None
            assert thread.native_id == thread.ident
        finally:
            unpatch()


def test_wasm_thread_daemon_cannot_change_after_start() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            thread = threading.Thread(target=lambda: None)
            thread.daemon = True
            thread.start()
            thread.join(timeout=1)

            assert not thread.is_alive()
            with pytest.raises(RuntimeError, match="daemon status"):
                thread.daemon = False
        finally:
            unpatch()


def test_threading_local_subclass_initializes_per_shim_thread() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            records: list[tuple[str, int]] = []

            class LocalState(threading.local):
                def __init__(self, value: int) -> None:
                    self.value = value

            local = LocalState(10)
            assert local.value == 10
            local.value = 1

            def worker() -> None:
                records.append((threading.current_thread().name, local.value))
                local.value = 2
                records.append((threading.current_thread().name, local.value))

            thread = threading.Thread(target=worker, name="local-worker")
            thread.start()
            thread.join(timeout=1)

            assert not thread.is_alive()
            assert records == [("local-worker", 10), ("local-worker", 2)]
            assert local.value == 1
        finally:
            unpatch()


def test_threading_local_subclass_defaults_can_be_overridden() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            records: list[str] = []

            class LocalState(threading.local):
                value = "default"

            local = LocalState()
            assert local.value == "default"
            local.value = "main"
            assert local.value == "main"

            def worker() -> None:
                records.append(local.value)
                local.value = "worker"
                records.append(local.value)

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=1)

            assert records == ["default", "worker"]
            assert local.value == "main"
        finally:
            unpatch()


def test_threading_local_subclass_data_descriptors_are_preserved() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            records: list[str] = []

            class PropertyLocal(threading.local):
                @property
                def value(self) -> str:
                    return getattr(self, "_value", "default")

                @value.setter
                def value(self, value: str) -> None:
                    self._value = value

            local = PropertyLocal()
            assert local.value == "default"
            local.value = "main"
            assert local.value == "main"

            def worker() -> None:
                records.append(local.value)
                local.value = "worker"
                records.append(local.value)

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=1)

            assert records == ["default", "worker"]
            assert local.value == "main"
        finally:
            unpatch()


def test_threading_local_subclass_slots_are_shared_descriptors() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            records: list[str] = []

            class SlotLocal(threading.local):
                __slots__ = ("value",)

            local = SlotLocal()
            local.value = "main"

            def worker() -> None:
                records.append(local.value)
                local.value = "worker"
                records.append(local.value)

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=1)

            assert records == ["main", "worker"]
            assert local.value == "worker"
        finally:
            unpatch()


def test_wasm_thread_does_not_inherit_ambient_contextvars() -> None:
    ambient = contextvars.ContextVar("ambient", default="unset")
    ambient.set("parent")

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            records: list[str] = []

            def worker() -> None:
                records.append(ambient.get())
                ambient.set("child")
                records.append(ambient.get())

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=1)

            assert not thread.is_alive()
            assert records == ["unset", "child"]
            assert ambient.get() == "parent"
        finally:
            unpatch()


def test_wasm_rlock_owner_is_logical_thread() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            lock = threading.RLock()
            assert lock.acquire()
            worker_results: list[bool] = []

            def worker() -> None:
                worker_results.append(lock.acquire(False))

            thread = threading.Thread(target=worker, name="rlock-worker")
            thread.start()
            thread.join(timeout=1)

            assert worker_results == [False]
            lock.release()
            assert lock.acquire(False)
            lock.release()
        finally:
            unpatch()


def test_wasm_lock_timeout_zero_is_immediate_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        _forbid_run_sync(monkeypatch)
        try:
            lock = threading.Lock()
            assert lock.acquire()
            assert lock.acquire(timeout=0) is False
            lock.release()
            assert lock.acquire(blocking=False) is True
            lock.release()
        finally:
            unpatch()


def test_wasm_event_and_condition_negative_timeouts_are_immediate_polls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        _forbid_run_sync(monkeypatch)
        try:
            event = threading.Event()
            assert event.wait(timeout=-1) is False
            assert event.wait(timeout=-0.5) is False

            condition = threading.Condition()
            with condition:
                assert condition.wait(timeout=-1) is False
                assert condition.wait(timeout=-0.5) is False
        finally:
            unpatch()


def test_wasm_locks_reject_invalid_negative_timeouts() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            lock = threading.Lock()
            rlock = threading.RLock()

            with pytest.raises(ValueError, match="timeout value"):
                lock.acquire(timeout=-2)
            with pytest.raises(ValueError, match="timeout value"):
                lock.acquire(timeout=-0.5)
            with pytest.raises(ValueError, match="timeout value"):
                rlock.acquire(timeout=-2)
            with pytest.raises(ValueError, match="timeout value"):
                rlock.acquire(timeout=-0.5)
            with pytest.raises(ValueError, match="non-blocking"):
                lock.acquire(blocking=False, timeout=0)
            with pytest.raises(ValueError, match="non-blocking"):
                rlock.acquire(blocking=False, timeout=0)
        finally:
            unpatch()


def test_wasm_condition_with_rlock_restores_recursive_acquire_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        _install_run_sync(monkeypatch)
        try:
            lock = threading.RLock()
            condition = threading.Condition(lock)
            with condition:
                condition.acquire()
                assert not condition.wait(timeout=0.001)
                condition.release()

            assert lock.acquire(blocking=False)
            lock.release()
        finally:
            unpatch()


def test_wasm_timed_waits_avoid_pyodide_absolute_deadlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        _install_run_sync(monkeypatch)
        _forbid_asyncio_wait_for(monkeypatch)
        try:
            event = threading.Event()
            assert event.wait(timeout=0.001) is False

            lock = threading.Lock()
            assert lock.acquire()
            assert lock.acquire(timeout=0.001) is False
            lock.release()

            condition = threading.Condition()
            with condition:
                assert condition.wait(timeout=0.001) is False

            semaphore = threading.Semaphore(0)
            assert semaphore.acquire(timeout=0.001) is False
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_immediate_timer_cancel_does_not_leave_live_thread() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        timer = threading.Timer(10, lambda: None)
        try:
            timer.start()
            timer.cancel()
            with pytest.raises(RuntimeError, match="while live"):
                unpatch()
            await _wait_until(lambda: not timer.is_alive())
            assert not timer.is_alive()
            unpatch()
        finally:
            if timer.is_alive():
                timer.cancel()
                timer.join(timeout=0)


@pytest.mark.asyncio
async def test_timer_cancel_refuses_unpatch_until_cancelled_task_finishes() -> (
    None
):
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        cleanup_started = asyncio.Event()
        callback_started = asyncio.Event()
        release_cleanup = asyncio.Event()

        async def callback() -> None:
            callback_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await release_cleanup.wait()

        timer = threading.Timer(0, callback)
        try:
            timer.start()
            await asyncio.wait_for(callback_started.wait(), timeout=1)
            timer.cancel()
            await asyncio.wait_for(cleanup_started.wait(), timeout=1)

            assert timer.is_alive()
            with pytest.raises(RuntimeError, match="while live"):
                unpatch()
        finally:
            release_cleanup.set()
            for _ in range(5):
                await asyncio.sleep(0)
                if not timer.is_alive():
                    break
            if timer.is_alive():
                timer.cancel()
            unpatch()

        assert not timer.is_alive()


def test_timer_cancel_before_start_skips_callback() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            called = False

            def callback() -> None:
                nonlocal called
                called = True

            timer = threading.Timer(60, callback)
            timer.cancel()
            timer.start()
            timer.join(timeout=1)

            assert not timer.is_alive()
            assert not called
        finally:
            unpatch()


@pytest.mark.asyncio
async def test_thread_join_timeout_zero_is_immediate_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        _forbid_run_sync(monkeypatch)
        release = asyncio.Event()

        async def wait_forever() -> None:
            await release.wait()

        thread = threading.Thread(target=wait_forever)
        thread.start()
        try:
            thread.join(timeout=0)
            assert thread.is_alive()
        finally:
            release.set()
            for _ in range(5):
                await asyncio.sleep(0)
                if not thread.is_alive():
                    break
            assert not thread.is_alive()
            unpatch()


@pytest.mark.asyncio
async def test_thread_join_negative_timeout_is_immediate_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        release = asyncio.Event()

        async def wait_until_released() -> None:
            await release.wait()

        thread = threading.Thread(target=wait_until_released)
        thread.start()
        _forbid_run_sync(monkeypatch)
        try:
            thread.join(timeout=-1)
            assert thread.is_alive()
        finally:
            release.set()
            for _ in range(5):
                await asyncio.sleep(0)
                if not thread.is_alive():
                    break
            assert not thread.is_alive()
            unpatch()


def test_threading_excepthook_receives_except_hook_args() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            observed = []

            def capture_excepthook(args: threading.ExceptHookArgs) -> None:
                observed.append(args)

            old_excepthook = threading.excepthook
            threading.excepthook = capture_excepthook
            thread = threading.Thread(
                target=lambda: (_ for _ in ()).throw(
                    RuntimeError("default hook boom")
                )
            )
            try:
                thread.start()
                thread.join(timeout=1)
                assert not thread.is_alive()
            finally:
                threading.excepthook = old_excepthook

            assert len(observed) == 1
            assert observed[0].exc_type is RuntimeError
            assert str(observed[0].exc_value) == "default hook boom"
            assert observed[0].thread is thread
        finally:
            unpatch()


def test_thread_target_cancelled_error_reaches_excepthook() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            observed: list[tuple[str, str, object]] = []

            async def target() -> None:
                raise asyncio.CancelledError("user cancel")

            def capture_excepthook(args: threading.ExceptHookArgs) -> None:
                observed.append(
                    (
                        args.exc_type.__name__,
                        str(args.exc_value),
                        args.thread,
                    )
                )

            old_excepthook = threading.excepthook
            threading.excepthook = capture_excepthook
            thread = threading.Thread(target=target, name="cancelled-worker")
            try:
                thread.start()
                thread.join(timeout=1)
                assert not thread.is_alive()
            finally:
                threading.excepthook = old_excepthook

            assert observed == [
                ("CancelledError", "user cancel", thread),
            ]
        finally:
            unpatch()


def test_timer_target_cancelled_error_reaches_excepthook() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            observed: list[tuple[str, str, object]] = []

            async def target() -> None:
                raise asyncio.CancelledError("timer cancel")

            def capture_excepthook(args: threading.ExceptHookArgs) -> None:
                observed.append(
                    (
                        args.exc_type.__name__,
                        str(args.exc_value),
                        args.thread,
                    )
                )

            old_excepthook = threading.excepthook
            threading.excepthook = capture_excepthook
            timer = threading.Timer(0, target)
            try:
                timer.start()
                timer.join(timeout=1)
                assert not timer.is_alive()
            finally:
                threading.excepthook = old_excepthook

            assert observed == [
                ("CancelledError", "timer cancel", timer),
            ]
        finally:
            unpatch()


def test_wasm_concurrency_preserves_basic_stdlib_behavior() -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            records: queue.Queue[str] = queue.Queue()
            records.put("queued")
            assert records.get(block=False) == "queued"

            messages: list[str] = []

            class CapturingHandler(logging.Handler):
                def emit(self, record: logging.LogRecord) -> None:
                    messages.append(record.getMessage())

            logger = logging.getLogger("marimo.wasm.threading.test")
            handler = CapturingHandler()
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            try:
                logger.info("logged")
            finally:
                logger.removeHandler(handler)

            async def roundtrip_asyncio_queue() -> str:
                items: asyncio.Queue[str] = asyncio.Queue()
                await items.put("async queued")
                return await items.get()

            assert messages == ["logged"]
            assert asyncio.run(roundtrip_asyncio_queue()) == "async queued"
        finally:
            unpatch()


def test_event_wait_timeout_and_release_allow_unpatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        _install_run_sync(monkeypatch)
        unpatched = False
        timer: threading.Timer | None = None
        try:
            event = threading.Event()
            for _ in range(5):
                assert event.wait(timeout=0.001) is False

            timer = threading.Timer(0.001, event.set)
            timer.start()
            timer.join(timeout=1)
            assert not timer.is_alive()
            assert event.wait(timeout=1) is True
            unpatch()
            unpatched = True
        finally:
            if timer is not None and timer.is_alive():
                timer.cancel()
                timer.join(timeout=0)
            if event.is_set():
                event.clear()
            if not unpatched:
                unpatch()


def test_cooperative_wait_preserves_user_run_sync_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pyodide_module = ModuleType("pyodide")
    ffi_module = ModuleType("pyodide.ffi")

    def run_sync(_awaitable: object) -> object:
        raise ValueError("user callback failed")

    cast(Any, ffi_module).run_sync = run_sync
    cast(Any, pyodide_module).ffi = ffi_module
    monkeypatch.setitem(sys.modules, "pyodide", pyodide_module)
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi_module)

    with pytest.raises(ValueError, match="user callback failed"):
        cooperative_wait(AwaitableProbe())


def test_cooperative_wait_classifies_jspi_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pyodide_module = ModuleType("pyodide")
    ffi_module = ModuleType("pyodide.ffi")

    def run_sync(_awaitable: object) -> object:
        raise AssertionError("can_run_sync=False should avoid run_sync")

    cast(Any, ffi_module).run_sync = run_sync
    cast(Any, ffi_module).can_run_sync = lambda: False
    cast(Any, pyodide_module).ffi = ffi_module
    monkeypatch.setitem(sys.modules, "pyodide", pyodide_module)
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi_module)

    with pytest.raises(
        UnsupportedWasmConcurrencyError,
        match="JSPI promising frame",
    ):
        cooperative_wait(AwaitableProbe())


def test_cooperative_wait_preserves_user_runtime_error_with_promise_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pyodide_module = ModuleType("pyodide")
    ffi_module = ModuleType("pyodide.ffi")

    def run_sync(_awaitable: object) -> object:
        raise RuntimeError("JSPI promising frame is not available")

    cast(Any, ffi_module).run_sync = run_sync
    cast(Any, pyodide_module).ffi = ffi_module
    monkeypatch.setitem(sys.modules, "pyodide", pyodide_module)
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi_module)

    with pytest.raises(RuntimeError, match="JSPI promising frame"):
        cooperative_wait(AwaitableProbe())
