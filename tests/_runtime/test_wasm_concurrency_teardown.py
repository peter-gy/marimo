# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import asyncio
import concurrent.futures
import multiprocessing
import threading
from typing import TYPE_CHECKING

import pytest

from marimo._runtime._wasm._concurrency import _state
from marimo._runtime._wasm._concurrency._install import (
    install_wasm_concurrency_shims,
    install_wasm_process_compatibility_shims,
    shutdown_live_wasm_concurrency_work,
    shutdown_live_wasm_concurrency_work_async,
    wait_for_live_wasm_concurrency_work_async,
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


async def test_wasm_process_unpatch_ignores_live_core_executor() -> None:
    original_thread = threading.Thread
    original_process = multiprocessing.Process

    with mock_pyodide():
        core_unpatch = install_wasm_concurrency_shims()
        process_unpatch = install_wasm_process_compatibility_shims()

        executor = concurrent.futures.ThreadPoolExecutor()
        try:
            process_unpatch()

            assert threading.Thread is not original_thread
            assert multiprocessing.Process is original_process
            with pytest.raises(RuntimeError, match="while live"):
                core_unpatch()
        finally:
            executor.shutdown()
            core_unpatch()

    assert threading.Thread is original_thread
    assert multiprocessing.Process is original_process


async def test_wasm_owned_process_unpatch_preflights_core_tasks() -> None:
    original_thread = threading.Thread
    original_process = multiprocessing.Process

    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()

        executor = concurrent.futures.ThreadPoolExecutor()
        try:
            with pytest.raises(RuntimeError, match="while live"):
                unpatch()

            assert threading.Thread is not original_thread
            assert multiprocessing.Process is not original_process
        finally:
            executor.shutdown()
            unpatch()

    assert threading.Thread is original_thread
    assert multiprocessing.Process is original_process


async def test_wasm_concurrency_unpatch_handle_refuses_live_tasks() -> None:
    original_thread = threading.Thread

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        release = asyncio.Event()
        started = asyncio.Event()

        async def wait_forever() -> None:
            started.set()
            await release.wait()

        thread = threading.Thread(target=wait_forever)
        thread.start()
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            assert thread.is_alive()
            with pytest.raises(RuntimeError, match="cannot unpatch"):
                unpatch()
        finally:
            release.set()
            await _wait_until(lambda: not thread.is_alive())
            assert not thread.is_alive()
            unpatch()

    assert threading.Thread is original_thread


async def test_wasm_concurrency_unpatch_refuses_unclosed_executor() -> None:
    original_thread = threading.Thread

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        executor = concurrent.futures.ThreadPoolExecutor()
        try:
            with pytest.raises(RuntimeError, match="cannot unpatch"):
                unpatch()
        finally:
            executor.shutdown()
            unpatch()


async def test_wasm_shutdown_finishes_idle_unclosed_executor() -> None:
    original_thread = threading.Thread

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        unpatched = False

        executor = concurrent.futures.ThreadPoolExecutor()
        future = executor.submit(lambda: "done")
        try:
            await _wait_until(future.done)
            assert future.result(timeout=0) == "done"

            await shutdown_live_wasm_concurrency_work_async()

            unpatch()
            unpatched = True
        finally:
            executor.shutdown(cancel_futures=True)
            if not unpatched:
                unpatch()

    assert threading.Thread is original_thread


async def test_wasm_shutdown_allows_default_executor_recreation() -> None:
    original_thread = threading.Thread

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        loop = asyncio.get_running_loop()
        try:
            assert (
                await loop.run_in_executor(None, lambda: "before shutdown")
                == "before shutdown"
            )

            await shutdown_live_wasm_concurrency_work_async()

            assert (
                await loop.run_in_executor(None, lambda: "after shutdown")
                == "after shutdown"
            )
        finally:
            await shutdown_live_wasm_concurrency_work_async()
            unpatch()

    assert threading.Thread is original_thread


async def test_wasm_shutdown_requests_live_work_cancellation() -> None:
    original_thread = threading.Thread

    with mock_pyodide():
        unpatch = install_wasm_process_compatibility_shims()
        release_thread = asyncio.Event()
        release_process = asyncio.Event()
        thread_cancelled = False
        process_cancelled = False

        async def thread_target() -> None:
            nonlocal thread_cancelled
            try:
                await release_thread.wait()
            except asyncio.CancelledError:
                thread_cancelled = True
                raise

        async def process_target() -> None:
            nonlocal process_cancelled
            try:
                await release_process.wait()
            except asyncio.CancelledError:
                process_cancelled = True
                raise

        thread = threading.Thread(target=thread_target)
        process = multiprocessing.Process(target=process_target)
        try:
            thread.start()
            process.start()
            await asyncio.sleep(0)

            assert thread.is_alive()
            assert process.is_alive()

            shutdown_live_wasm_concurrency_work()
            await _wait_until(
                lambda: (
                    thread_cancelled
                    and process_cancelled
                    and not thread.is_alive()
                    and not process.is_alive()
                )
            )

            assert thread_cancelled
            assert process_cancelled
            assert not thread.is_alive()
            assert not process.is_alive()
            assert process.exitcode == -1
        finally:
            release_thread.set()
            release_process.set()
            await asyncio.sleep(0)
            unpatch()

    assert threading.Thread is original_thread


async def test_wasm_shutdown_requests_registered_executor_shutdown() -> None:
    class RegisteredExecutor:
        shutdown_requested = False

        def shutdown_for_wasm_teardown(self) -> None:
            self.shutdown_requested = True

        def is_idle_for_wasm_teardown(self) -> bool:
            return self.shutdown_requested

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        executor = RegisteredExecutor()
        unpatched = False
        try:
            _state.register_executor(executor)

            shutdown_live_wasm_concurrency_work()
            assert executor.shutdown_requested
            assert await wait_for_live_wasm_concurrency_work_async(timeout=1)

            unpatch()
            unpatched = True
        finally:
            _state.unregister_executor(executor)
            if not unpatched:
                unpatch()


async def test_wasm_grace_wait_allows_cooperative_async_thread_to_exit() -> (
    None
):
    original_thread = threading.Thread

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        should_exit = asyncio.Event()
        observed_exit = False

        async def target() -> None:
            nonlocal observed_exit
            await should_exit.wait()
            observed_exit = True

        thread = threading.Thread(target=target)
        try:
            thread.start()
            await asyncio.sleep(0)
            assert thread.is_alive()

            should_exit.set()
            assert await wait_for_live_wasm_concurrency_work_async(timeout=1)

            assert observed_exit
            assert not thread.is_alive()
        finally:
            should_exit.set()
            if thread.is_alive():
                shutdown_live_wasm_concurrency_work()
                await _wait_until(lambda: not thread.is_alive())
            unpatch()

    assert threading.Thread is original_thread
