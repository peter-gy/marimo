# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import asyncio
import builtins
import concurrent.futures
import multiprocessing
import multiprocessing.context
import multiprocessing.pool
import multiprocessing.process
import multiprocessing.queues
import multiprocessing.synchronize
import sys
import threading
from typing import TYPE_CHECKING, Any, cast

import pytest

from marimo._runtime._wasm import unpatch_wasm_process_compatibility
from marimo._runtime._wasm._concurrency._install import (
    install_wasm_concurrency_shims,
    install_wasm_process_compatibility_shims,
    unpatch_wasm_concurrency_shims,
)
from marimo._runtime._wasm._concurrency._wait import (
    UnsupportedWasmConcurrencyError,
)
from tests.conftest import mock_pyodide

if TYPE_CHECKING:
    from collections.abc import Callable


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 1,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not bool(predicate()):
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(
                "condition did not become true before timeout"
            )
        await asyncio.sleep(0)


def test_wasm_concurrency_shim_restores_all_patch_groups() -> None:
    original_thread = threading.Thread
    original_event = threading.Event
    original_thread_lock = threading.Lock
    original_local = threading.local
    original_process = multiprocessing.Process
    original_executor = concurrent.futures.ThreadPoolExecutor
    original_process_executor = concurrent.futures.ProcessPoolExecutor
    original_pool = multiprocessing.Pool
    original_pool_class = multiprocessing.pool.Pool
    original_cpu_count = multiprocessing.cpu_count
    original_get_context = multiprocessing.get_context
    original_lock = multiprocessing.Lock

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        install_wasm_process_compatibility_shims()
        try:
            assert threading.Thread is not original_thread
            assert threading.Event is not original_event
            assert threading.Lock is not original_thread_lock
            assert threading.local is not original_local
            assert multiprocessing.Process is not original_process
            assert (
                concurrent.futures.ThreadPoolExecutor is not original_executor
            )
            assert (
                concurrent.futures.ProcessPoolExecutor
                is not original_process_executor
            )
            assert multiprocessing.Pool is not original_pool
            assert multiprocessing.pool.Pool is not original_pool_class
            assert multiprocessing.cpu_count is not original_cpu_count
            assert multiprocessing.get_context is not original_get_context
            assert multiprocessing.Lock is not original_lock
        finally:
            unpatch()

    assert threading.Thread is original_thread
    assert threading.Event is original_event
    assert threading.Lock is original_thread_lock
    assert threading.local is original_local
    assert multiprocessing.Process is original_process
    assert concurrent.futures.ThreadPoolExecutor is original_executor
    assert concurrent.futures.ProcessPoolExecutor is original_process_executor
    assert multiprocessing.Pool is original_pool
    assert multiprocessing.pool.Pool is original_pool_class
    assert multiprocessing.cpu_count is original_cpu_count
    assert multiprocessing.get_context is original_get_context
    assert multiprocessing.Lock is original_lock


def test_wasm_process_unpatch_preserves_later_global_patches() -> None:
    replacement_process_executor = object()
    original_process_executor = concurrent.futures.ProcessPoolExecutor
    futures_module = cast(Any, concurrent.futures)

    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            futures_module.ProcessPoolExecutor = replacement_process_executor

            unpatch()

            assert (
                concurrent.futures.ProcessPoolExecutor
                is replacement_process_executor
            )
        finally:
            futures_module.ProcessPoolExecutor = original_process_executor


def test_wasm_process_unpatch_removes_created_futures_process_attr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_process_executor = concurrent.futures.ProcessPoolExecutor
    futures_module = cast(Any, concurrent.futures)
    original_import = builtins.__import__
    original_process_attr = getattr(futures_module, "process", None)
    had_process_attr = hasattr(futures_module, "process")
    original_process_module = sys.modules.get("concurrent.futures.process")

    def import_without_process_module(
        name: str,
        _globals: object | None = None,
        _locals: object | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name == "concurrent.futures.process":
            raise ModuleNotFoundError(name)
        return original_import(name, _globals, _locals, fromlist, level)

    monkeypatch.delattr(futures_module, "process", raising=False)
    monkeypatch.delitem(
        sys.modules, "concurrent.futures.process", raising=False
    )
    monkeypatch.setattr(builtins, "__import__", import_without_process_module)

    try:
        with mock_pyodide():
            unpatch = install_wasm_process_compatibility_shims()
            created_process_module = futures_module.process
            try:
                assert (
                    created_process_module.ProcessPoolExecutor
                    is concurrent.futures.ProcessPoolExecutor
                )
                assert (
                    sys.modules["concurrent.futures.process"]
                    is created_process_module
                )
            finally:
                unpatch()

        assert not hasattr(futures_module, "process")
        assert "concurrent.futures.process" not in sys.modules
        assert (
            concurrent.futures.ProcessPoolExecutor is original_process_executor
        )
    finally:
        if had_process_attr:
            futures_module.process = original_process_attr
        else:
            monkeypatch.delattr(futures_module, "process", raising=False)
        if original_process_module is not None:
            sys.modules["concurrent.futures.process"] = original_process_module


def test_wasm_process_direct_unpatch_removes_owned_core_shims() -> None:
    original_thread = threading.Thread
    original_process = multiprocessing.Process

    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            assert threading.Thread is not original_thread
            assert multiprocessing.Process is not original_process
        finally:
            unpatch()

    assert threading.Thread is original_thread
    assert multiprocessing.Process is original_process


def test_wasm_runtime_process_unpatch_keeps_default_core_shims() -> None:
    original_thread = threading.Thread
    original_process = multiprocessing.Process

    with mock_pyodide():
        core_unpatch = install_wasm_concurrency_shims()
        install_wasm_process_compatibility_shims()
        try:
            assert threading.Thread is not original_thread
            assert multiprocessing.Process is not original_process

            unpatch_wasm_process_compatibility()

            assert threading.Thread is not original_thread
            assert multiprocessing.Process is original_process
        finally:
            core_unpatch()

    assert threading.Thread is original_thread
    assert multiprocessing.Process is original_process


def test_wasm_redundant_process_install_handle_does_not_unpatch_owner() -> (
    None
):
    original_thread = threading.Thread
    original_process = multiprocessing.Process
    original_pool = multiprocessing.Pool

    with mock_pyodide():
        core_unpatch = install_wasm_concurrency_shims()
        owner_process_unpatch = install_wasm_process_compatibility_shims()
        redundant_process_unpatch = install_wasm_process_compatibility_shims()
        try:
            assert threading.Thread is not original_thread
            assert multiprocessing.Process is not original_process
            assert multiprocessing.Pool is not original_pool

            redundant_process_unpatch()

            assert threading.Thread is not original_thread
            assert multiprocessing.Process is not original_process
            assert multiprocessing.Pool is not original_pool

            owner_process_unpatch()

            assert threading.Thread is not original_thread
            assert multiprocessing.Process is original_process
            assert multiprocessing.Pool is original_pool
        finally:
            core_unpatch()

    assert threading.Thread is original_thread
    assert multiprocessing.Process is original_process
    assert multiprocessing.Pool is original_pool


def test_wasm_process_opt_in_does_not_rewrite_existing_from_import_aliases() -> (
    None
):
    with mock_pyodide():
        from concurrent.futures import (
            ProcessPoolExecutor as imported_process_pool_executor,
        )
        from multiprocessing import Process as imported_process

        unpatch = install_wasm_process_compatibility_shims()
        try:
            assert multiprocessing.Process is not imported_process
            assert (
                concurrent.futures.ProcessPoolExecutor
                is not imported_process_pool_executor
            )

            values: Any = multiprocessing.Queue()
            process = multiprocessing.Process(
                target=lambda output: output.put("patched"),
                args=(values,),
            )
            process.start()
            process.join(timeout=1)

            assert process.exitcode == 0
            assert values.get(block=False) == "patched"
        finally:
            unpatch()


def test_wasm_process_install_patches_direct_submodule_aliases() -> None:
    original_context_process = multiprocessing.context.Process
    original_queue = multiprocessing.queues.Queue
    original_lock = multiprocessing.synchronize.Lock
    original_active_children = multiprocessing.process.active_children

    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            assert multiprocessing.context.Process is multiprocessing.Process
            assert (
                multiprocessing.context.Process is not original_context_process
            )
            assert multiprocessing.queues.Queue is not original_queue
            assert multiprocessing.synchronize.Lock is not original_lock
            assert (
                multiprocessing.process.active_children
                is not original_active_children
            )

            values: Any = multiprocessing.queues.Queue()
            values.put("submodule")
            assert values.get(block=False) == "submodule"

            context = multiprocessing.get_context("spawn")
            ctx_values: Any = multiprocessing.queues.Queue(1, ctx=context)
            ctx_values.put("ctx-submodule")
            assert ctx_values.get(block=False) == "ctx-submodule"
            ctx_simple_values: Any = multiprocessing.queues.SimpleQueue(
                ctx=context
            )
            ctx_simple_values.put("ctx-simple")
            assert ctx_simple_values.get() == "ctx-simple"

            lock = multiprocessing.synchronize.Lock()
            assert lock.acquire(block=False)
            lock.release()
            ctx_lock = multiprocessing.synchronize.Lock(ctx=context)
            assert ctx_lock.acquire(block=False)
            ctx_lock.release()
            ctx_event = multiprocessing.synchronize.Event(ctx=context)
            ctx_event.set()
            assert ctx_event.is_set()
            ctx_semaphore = multiprocessing.synchronize.Semaphore(
                2, ctx=context
            )
            assert ctx_semaphore.acquire(block=False)
            ctx_semaphore.release()

            process = multiprocessing.context.Process(target=lambda: None)
            process.start()
            process.join(timeout=1)
            assert process.exitcode == 0

            with pytest.raises(
                UnsupportedWasmConcurrencyError,
                match="multiprocessing.queues.JoinableQueue",
            ):
                multiprocessing.queues.JoinableQueue()
        finally:
            unpatch()

    assert multiprocessing.context.Process is original_context_process
    assert multiprocessing.queues.Queue is original_queue
    assert multiprocessing.synchronize.Lock is original_lock
    assert multiprocessing.process.active_children is original_active_children


def test_wasm_process_compatibility_install_is_idempotent() -> None:
    original_process = multiprocessing.Process
    original_pool = multiprocessing.Pool
    original_process_executor = concurrent.futures.ProcessPoolExecutor

    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            assert multiprocessing.Process is not original_process
            assert multiprocessing.Pool is not original_pool
            assert (
                concurrent.futures.ProcessPoolExecutor
                is not original_process_executor
            )

            values: Any = multiprocessing.Queue()
            process = multiprocessing.Process(
                target=lambda output: output.put("ok"),
                args=(values,),
            )
            process.start()
            process.join(timeout=1)
            assert not process.is_alive()
            assert values.get(block=False) == "ok"
            with multiprocessing.Pool(2) as pool:
                assert pool.map(lambda value: value + 1, [1, 2]) == [2, 3]
            with concurrent.futures.ProcessPoolExecutor(2) as executor:
                assert executor.submit(lambda: "process").result() == "process"
        finally:
            unpatch()

    assert multiprocessing.Process is original_process
    assert multiprocessing.Pool is original_pool
    assert concurrent.futures.ProcessPoolExecutor is original_process_executor

    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        try:
            retry_values: Any = multiprocessing.Queue()
            process = multiprocessing.Process(
                target=lambda output: output.put("ok"),
                args=(retry_values,),
            )
            process.start()
            process.join(timeout=1)

            assert not process.is_alive()
            assert retry_values.get(block=False) == "ok"
        finally:
            unpatch()


def test_wasm_process_unpatch_keeps_preexisting_core_installed() -> None:
    original_thread = threading.Thread
    original_process = multiprocessing.Process

    with mock_pyodide():
        core_unpatch = install_wasm_concurrency_shims()
        process_unpatch = install_wasm_process_compatibility_shims()
        try:
            assert threading.Thread is not original_thread
            assert multiprocessing.Process is not original_process

            process_unpatch()

            assert threading.Thread is not original_thread
            assert multiprocessing.Process is original_process
        finally:
            core_unpatch()

    assert threading.Thread is original_thread
    assert multiprocessing.Process is original_process


def test_wasm_process_install_rolls_back_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marimo._runtime._wasm._concurrency._process_install as process_install

    original_thread = threading.Thread
    original_process = multiprocessing.Process
    original_process_executor = concurrent.futures.ProcessPoolExecutor

    def fail_multiprocessing_core(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("install failed")

    with mock_pyodide():
        with monkeypatch.context() as m:
            m.setattr(
                process_install,
                "install_multiprocessing_core",
                fail_multiprocessing_core,
            )
            try:
                with pytest.raises(RuntimeError, match="install failed"):
                    install_wasm_process_compatibility_shims()
            finally:
                unpatch_wasm_concurrency_shims()

        assert threading.Thread is original_thread
        assert multiprocessing.Process is original_process
        assert (
            concurrent.futures.ProcessPoolExecutor is original_process_executor
        )

        retry_unpatch = install_wasm_process_compatibility_shims()
        try:
            values: Any = multiprocessing.Queue()
            process = multiprocessing.Process(
                target=lambda output: output.put("retry"),
                args=(values,),
            )
            process.start()
            process.join(timeout=1)

            assert not process.is_alive()
            assert values.get(block=False) == "retry"
        finally:
            retry_unpatch()

    assert threading.Thread is original_thread
    assert multiprocessing.Process is original_process
    assert concurrent.futures.ProcessPoolExecutor is original_process_executor


async def test_wasm_process_unpatch_refuses_live_process_pool_executor() -> (
    None
):
    original_process = multiprocessing.Process
    original_process_executor = concurrent.futures.ProcessPoolExecutor

    with mock_pyodide():
        core_unpatch = install_wasm_concurrency_shims()
        process_unpatch = install_wasm_process_compatibility_shims()

        executor = concurrent.futures.ProcessPoolExecutor()
        future = executor.submit(lambda: "done")
        try:
            await _wait_until(future.done)
            assert future.result(timeout=0) == "done"

            with pytest.raises(RuntimeError, match="process compatibility"):
                process_unpatch()

            assert multiprocessing.Process is not original_process
            assert (
                concurrent.futures.ProcessPoolExecutor
                is not original_process_executor
            )
        finally:
            executor.shutdown(cancel_futures=True)
            process_unpatch()
            core_unpatch()

    assert multiprocessing.Process is original_process
    assert concurrent.futures.ProcessPoolExecutor is original_process_executor


async def test_wasm_process_unpatch_refuses_process_owned_child_thread() -> (
    None
):
    original_thread = threading.Thread
    original_process = multiprocessing.Process

    with mock_pyodide():
        core_unpatch = install_wasm_concurrency_shims()
        process_unpatch = install_wasm_process_compatibility_shims()
        release_child = asyncio.Event()
        child_started = asyncio.Event()
        child_threads: list[Any] = []

        async def child_target() -> None:
            child_started.set()
            await release_child.wait()

        def process_target() -> None:
            child = threading.Thread(target=child_target)
            child_threads.append(child)
            child.start()

        process = multiprocessing.Process(target=process_target)
        try:
            process.start()
            await asyncio.wait_for(child_started.wait(), timeout=1)
            await _wait_until(lambda: not process.is_alive())

            assert not process.is_alive()
            assert child_threads[0].is_alive()
            with pytest.raises(RuntimeError, match="process compatibility"):
                process_unpatch()

            assert threading.Thread is not original_thread
            assert multiprocessing.Process is not original_process
        finally:
            release_child.set()
            if child_threads:
                await _wait_until(lambda: not child_threads[0].is_alive())
            process_unpatch()
            core_unpatch()

    assert threading.Thread is original_thread
    assert multiprocessing.Process is original_process


def test_wasm_process_unpatch_refuses_idle_process_pool_executor() -> None:
    original_process = multiprocessing.Process
    original_process_executor = concurrent.futures.ProcessPoolExecutor

    with mock_pyodide():
        core_unpatch = install_wasm_concurrency_shims()
        process_unpatch = install_wasm_process_compatibility_shims()

        executor = concurrent.futures.ProcessPoolExecutor()
        try:
            with pytest.raises(RuntimeError, match="process compatibility"):
                process_unpatch()

            assert multiprocessing.Process is not original_process
            assert (
                concurrent.futures.ProcessPoolExecutor
                is not original_process_executor
            )
        finally:
            executor.shutdown(cancel_futures=True)
            process_unpatch()
            core_unpatch()

    assert multiprocessing.Process is original_process
    assert concurrent.futures.ProcessPoolExecutor is original_process_executor
