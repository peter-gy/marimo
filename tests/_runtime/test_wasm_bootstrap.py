# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import concurrent.futures
import contextlib
import importlib
import json
import multiprocessing
import subprocess
import sys
import threading
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator


def _noop_unpatch() -> None:
    return


def _put_queue_value(output: Any, value: str) -> None:
    output.put(value)


def _assert_default_concurrency_surface() -> None:
    thread_records: list[str] = []
    thread = threading.Thread(
        target=lambda: thread_records.append("thread"),
        name="bootstrap-thread",
    )
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert thread_records == ["thread"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        assert (
            executor.submit(str, "thread-pool").result(timeout=1)
            == "thread-pool"
        )


def _assert_process_shaped_surface(mp_module: Any = multiprocessing) -> None:
    values: Any = mp_module.Queue()
    process = mp_module.Process(
        target=_put_queue_value,
        args=(values, "process"),
    )
    process.start()
    process.join(timeout=1)
    assert not process.is_alive()
    assert process.exitcode == 0
    assert values.get(block=False) == "process"

    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as executor:
        assert (
            executor.submit(str, "process-pool").result(timeout=1)
            == "process-pool"
        )

    with mp_module.Pool(2) as pool:
        assert pool.map(str, ["pool"]) == ["pool"]

    assert mp_module.cpu_count() == 1


@contextlib.contextmanager
def mock_pyodide() -> Generator[None, None, None]:
    modules = {"pyodide": Mock()}
    with (
        patch.object(sys, "platform", "emscripten"),
        patch.dict(sys.modules, modules),
    ):
        yield


def test_wasm_thread_patch_preserves_main_thread_instance_check() -> None:
    import marimo
    import marimo._runtime.threads as baseline_runtime_threads

    assert marimo.Thread is baseline_runtime_threads.Thread

    original_module = sys.modules.get("marimo._runtime.threads")
    runtime_package = sys.modules.get("marimo._runtime")
    had_runtime_attr = runtime_package is not None and hasattr(
        runtime_package, "threads"
    )
    original_runtime_attr = (
        getattr(runtime_package, "threads", None)
        if runtime_package is not None
        else None
    )
    sys.modules.pop("marimo._runtime.threads", None)
    if runtime_package is not None and had_runtime_attr:
        runtime_package.__dict__.pop("threads", None)

    from marimo._runtime._wasm._concurrency._install import (
        install_wasm_concurrency_shims,
    )

    with mock_pyodide():
        unpatch = _noop_unpatch
        try:
            unpatch = install_wasm_concurrency_shims()
            runtime_threads = importlib.import_module(
                "marimo._runtime.threads"
            )
            assert isinstance(threading.current_thread(), threading.Thread)
            assert isinstance(threading.main_thread(), threading.Thread)
            assert not isinstance(
                threading.current_thread(),
                runtime_threads.Thread,
            )
            assert not runtime_threads.is_marimo_thread()
        finally:
            unpatch()
            sys.modules.pop("marimo._runtime.threads", None)
            if original_module is not None:
                sys.modules["marimo._runtime.threads"] = original_module
            if runtime_package is not None:
                if had_runtime_attr:
                    runtime_package.__dict__["threads"] = original_runtime_attr
                else:
                    runtime_package.__dict__.pop("threads", None)

    if runtime_package is not None and had_runtime_attr:
        assert runtime_package.__dict__["threads"] is original_runtime_attr
    assert marimo.Thread is baseline_runtime_threads.Thread


def test_wasm_bootstrap_installs_default_concurrency_surface() -> None:
    original_thread = threading.Thread
    original_executor = concurrent.futures.ThreadPoolExecutor
    original_process_executor = concurrent.futures.ProcessPoolExecutor
    original_process = multiprocessing.Process
    original_pool = multiprocessing.Pool
    original_cpu_count = multiprocessing.cpu_count

    with mock_pyodide():
        import marimo._runtime._wasm as wasm

        wasm = importlib.reload(wasm)
        try:
            assert threading.Thread is original_thread
            assert concurrent.futures.ThreadPoolExecutor is original_executor

            wasm.bootstrap_wasm_runtime()

            _assert_default_concurrency_surface()
            _assert_process_shaped_surface()
        finally:
            wasm.unpatch_wasm_runtime()

    assert threading.Thread is original_thread
    assert concurrent.futures.ThreadPoolExecutor is original_executor
    assert concurrent.futures.ProcessPoolExecutor is original_process_executor
    assert multiprocessing.Process is original_process
    assert multiprocessing.Pool is original_pool
    assert multiprocessing.cpu_count is original_cpu_count


def test_wasm_bootstrap_rolls_back_core_when_process_install_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_thread = threading.Thread
    original_executor = concurrent.futures.ThreadPoolExecutor
    original_process = multiprocessing.Process
    original_pool = multiprocessing.Pool
    original_cpu_count = multiprocessing.cpu_count

    with mock_pyodide():
        import marimo._runtime._wasm as wasm

        wasm = importlib.reload(wasm)

        def fail_process_install() -> None:
            raise RuntimeError("process install failed")

        monkeypatch.setattr(
            wasm,
            "install_wasm_process_compatibility_shims",
            fail_process_install,
        )

        with pytest.raises(RuntimeError, match="process install failed"):
            wasm.ensure_wasm_runtime_bootstrapped()

    assert threading.Thread is original_thread
    assert concurrent.futures.ThreadPoolExecutor is original_executor
    assert multiprocessing.Process is original_process
    assert multiprocessing.Pool is original_pool
    assert multiprocessing.cpu_count is original_cpu_count


def test_top_level_marimo_import_bootstraps_wasm_before_public_api() -> None:
    code = """
import concurrent.futures
import json
import multiprocessing
import sys
import threading
import types

sys.platform = "emscripten"
pyodide = types.ModuleType("pyodide")
pyodide_ffi = types.ModuleType("pyodide.ffi")
pyodide.ffi = pyodide_ffi
sys.modules["pyodide"] = pyodide
sys.modules["pyodide.ffi"] = pyodide_ffi

original_thread = threading.Thread
original_local = threading.local
original_process = multiprocessing.Process
original_process_executor = concurrent.futures.ProcessPoolExecutor

import marimo
from marimo._runtime.context import types as context_types

print(json.dumps({
    "thread_patched": threading.Thread is not original_thread,
    "local_patched": threading.local is not original_local,
    "process_patched": multiprocessing.Process is not original_process,
    "process_executor_patched": (
        concurrent.futures.ProcessPoolExecutor
        is not original_process_executor
    ),
    "public_thread_uses_patched_base": issubclass(
        marimo.Thread,
        threading.Thread,
    ),
    "runtime_context_uses_patched_local": isinstance(
        context_types._THREAD_LOCAL_CONTEXT,
        threading.local,
    ),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "thread_patched": True,
        "local_patched": True,
        "process_patched": True,
        "process_executor_patched": True,
        "public_thread_uses_patched_base": True,
        "runtime_context_uses_patched_local": True,
    }


def test_wasm_bootstrap_installs_process_shaped_submodule_factories() -> None:
    import marimo._runtime._wasm as wasm

    removed_modules = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "multiprocessing" or name.startswith("multiprocessing.")
    }
    for name in removed_modules:
        del sys.modules[name]

    try:
        with mock_pyodide():
            try:
                wasm.bootstrap_wasm_runtime()
                mp_module = importlib.import_module("multiprocessing")
                importlib.import_module("multiprocessing.context")
                mp_pool = importlib.import_module("multiprocessing.pool")

                ctx = mp_module.get_context("spawn")
                values: Any = ctx.Queue()
                process = ctx.Process(
                    target=_put_queue_value,
                    args=(values, "context-process"),
                )
                process.start()
                process.join(timeout=1)
                assert not process.is_alive()
                assert process.exitcode == 0
                assert values.get(block=False) == "context-process"

                with mp_pool.Pool(1) as pool:
                    assert pool.map(str, ["submodule-pool"]) == [
                        "submodule-pool"
                    ]
            finally:
                wasm.unpatch_wasm_runtime()
    finally:
        for name in list(sys.modules):
            if name == "multiprocessing" or name.startswith(
                "multiprocessing."
            ):
                del sys.modules[name]
        sys.modules.update(removed_modules)


def test_wasm_thread_module_uses_caller_bootstrap_contract() -> None:
    original_module = sys.modules.get("marimo._runtime.threads")
    runtime_package = sys.modules.get("marimo._runtime")
    had_runtime_attr = runtime_package is not None and hasattr(
        runtime_package, "threads"
    )
    original_runtime_attr = (
        getattr(runtime_package, "threads", None)
        if runtime_package is not None
        else None
    )
    original_thread = threading.Thread
    sys.modules.pop("marimo._runtime.threads", None)
    if runtime_package is not None and had_runtime_attr:
        runtime_package.__dict__.pop("threads", None)

    with mock_pyodide():
        try:
            module = importlib.import_module("marimo._runtime.threads")
            assert threading.Thread is original_thread
            assert issubclass(module.Thread, threading.Thread)
        finally:
            sys.modules.pop("marimo._runtime.threads", None)
            if original_module is not None:
                sys.modules["marimo._runtime.threads"] = original_module
            if runtime_package is not None:
                if had_runtime_attr:
                    runtime_package.__dict__["threads"] = original_runtime_attr
                else:
                    runtime_package.__dict__.pop("threads", None)
