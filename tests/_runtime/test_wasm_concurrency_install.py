# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import concurrent.futures
import multiprocessing
import threading

import pytest

from marimo._runtime._wasm._concurrency._install import (
    install_wasm_concurrency_shims,
)
from marimo._runtime._wasm._patches import WasmPatchSet
from tests.conftest import mock_pyodide


def test_wasm_concurrency_shim_is_inert_outside_pyodide() -> None:
    original_thread = threading.Thread
    original_process = multiprocessing.Process
    original_executor = concurrent.futures.ThreadPoolExecutor

    unpatch = install_wasm_concurrency_shims()
    unpatch()

    assert threading.Thread is original_thread
    assert multiprocessing.Process is original_process
    assert concurrent.futures.ThreadPoolExecutor is original_executor


def test_wasm_redundant_core_install_handle_does_not_unpatch_owner() -> None:
    original_thread = threading.Thread
    original_executor = concurrent.futures.ThreadPoolExecutor

    with mock_pyodide():
        owner_unpatch = install_wasm_concurrency_shims()
        redundant_unpatch = install_wasm_concurrency_shims()
        try:
            assert threading.Thread is not original_thread
            assert (
                concurrent.futures.ThreadPoolExecutor is not original_executor
            )

            redundant_unpatch()

            assert threading.Thread is not original_thread
            assert (
                concurrent.futures.ThreadPoolExecutor is not original_executor
            )
        finally:
            owner_unpatch()

    assert threading.Thread is original_thread
    assert concurrent.futures.ThreadPoolExecutor is original_executor


def test_wasm_default_install_rolls_back_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marimo._runtime._wasm._concurrency._install as install_module

    original_thread = threading.Thread
    original_executor = concurrent.futures.ThreadPoolExecutor

    def fail_repair(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("repair failed")

    with mock_pyodide():
        with monkeypatch.context() as m:
            m.setattr(
                install_module,
                "repair_preimported_runtime_context_storage",
                fail_repair,
            )

            with pytest.raises(RuntimeError, match="repair failed"):
                install_wasm_concurrency_shims()

        assert threading.Thread is original_thread
        assert concurrent.futures.ThreadPoolExecutor is original_executor

        unpatch = install_wasm_concurrency_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                assert executor.submit(lambda: "ok").result() == "ok"
        finally:
            unpatch()

    assert threading.Thread is original_thread
    assert concurrent.futures.ThreadPoolExecutor is original_executor


def test_wasm_patch_set_can_replace_none_attributes() -> None:
    class Owner:
        value = None

    with mock_pyodide():
        patches = WasmPatchSet()
        patches.replace(Owner, "value", lambda _original: "patched")
        unpatch = patches.unpatch_all()

        assert Owner.value == "patched"

        unpatch()

    assert Owner.value is None
