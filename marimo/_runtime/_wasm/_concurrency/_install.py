# Copyright 2026 Marimo. All rights reserved.
"""Patch stdlib concurrency modules for one Pyodide interpreter.

`install_wasm_concurrency_shims()` installs the default thread-shaped surface:
`threading`, `ThreadPoolExecutor`, and `asyncio` thread helpers. The Pyodide
bootstrap runs it before notebook code so runtime context storage sees the
patched `threading.local`.

`install_wasm_process_compatibility_shims()` installs the process-shaped
surface: `multiprocessing` and `ProcessPoolExecutor` entrypoints that are
callable in WASM but run in the same interpreter. They do not provide OS
processes, memory isolation, signal delivery, or pickle-copy IPC boundaries.

Each installer returns an unpatch function for the patches it activated.
"""

from __future__ import annotations

import sys
import threading as _threading
from importlib import import_module
from typing import TYPE_CHECKING, Any

from marimo._runtime._wasm._concurrency import _state
from marimo._runtime._wasm._concurrency._patch_plan import (
    DEFAULT_PATCH_SPECS,
    PatchOwner,
    apply_patch_specs,
)
from marimo._runtime._wasm._concurrency._preimport_repair import (
    repair_preimported_runtime_context_storage,
    repair_preimported_thread_local_stream_proxies,
)
from marimo._runtime._wasm._patches import Unpatch, WasmPatchSet
from marimo._utils.platform import is_pyodide

if TYPE_CHECKING:
    from types import ModuleType

    from marimo._runtime._wasm._concurrency._process_install import (
        ProcessPoolExecutorPatch,
    )


def install_wasm_concurrency_shims() -> Unpatch:
    """Install default thread-shaped and future-shaped patches in Pyodide."""
    if not is_pyodide():
        return lambda: None
    if _state.active_unpatch() is not None:
        return lambda: None

    import concurrent.futures.thread as futures_thread
    from concurrent import futures

    _state.set_patch_state(
        _state.PatchState(
            original_thread_type=_threading.Thread,
            original_current_thread=_threading.current_thread,
            original_get_ident=_threading.get_ident,
            original_get_native_id=getattr(
                _threading, "get_native_id", _threading.get_ident
            ),
            original_enumerate=_threading.enumerate,
            original_active_count=_threading.active_count,
            original_excepthook=_threading.excepthook,
        )
    )

    patches = WasmPatchSet()
    added_get_native_id = False
    try:
        if not hasattr(_threading, "get_native_id"):
            added_get_native_id = True
            _threading.get_native_id = _state.current_native_id  # type: ignore[attr-defined]

        apply_patch_specs(
            patches,
            DEFAULT_PATCH_SPECS,
            {
                PatchOwner.THREADING: _threading,
                PatchOwner.FUTURES: futures,
                PatchOwner.FUTURES_THREAD: futures_thread,
            },
        )
        repair_preimported_runtime_context_storage(patches)
        repair_preimported_thread_local_stream_proxies(patches)
    except BaseException:
        patches.unpatch_all()()
        if (
            added_get_native_id
            and getattr(_threading, "get_native_id", None)
            is _state.current_native_id
        ):
            del _threading.get_native_id  # type: ignore[attr-defined]
        _state.reset_runtime_state()
        raise

    unpatch = patches.unpatch_all()

    def _run_unpatch() -> None:
        unpatch()
        if (
            added_get_native_id
            and getattr(_threading, "get_native_id", None)
            is _state.current_native_id
        ):
            del _threading.get_native_id  # type: ignore[attr-defined]
        _state.reset_runtime_state()

    _state.set_active_unpatch(_run_unpatch)

    def _guarded_unpatch() -> None:
        unpatch_wasm_concurrency_shims()

    return _guarded_unpatch


def install_wasm_process_compatibility_shims() -> Unpatch:
    """Install process-shaped compatibility patches.

    Process-shaped APIs keep the callable `multiprocessing` and
    `ProcessPoolExecutor` surface that validation needs, then execute work in
    the current Pyodide interpreter. They provide no child processes, memory
    isolation, signal delivery, or pickle-copy IPC boundary. Pyodide bootstrap
    calls this before notebook code so user imports see the patched factories.
    Existing aliases keep the object they imported.
    """
    if not is_pyodide():
        return lambda: None
    core_was_active = _state.active_unpatch() is not None
    install_wasm_concurrency_shims()
    if _state.active_process_unpatch() is not None:
        return lambda: None

    import multiprocessing
    from concurrent import futures

    from marimo._runtime._wasm._concurrency._process_install import (
        ProcessPoolExecutorPatch,
        install_multiprocessing_core,
        install_multiprocessing_pool,
        install_process_pool_executor,
    )

    patches = WasmPatchSet()
    multiprocessing_context = _import_submodule_for_patch(
        patches,
        multiprocessing,
        "multiprocessing.context",
        "context",
    )
    multiprocessing_pool = _import_submodule_for_patch(
        patches,
        multiprocessing,
        "multiprocessing.pool",
        "pool",
    )
    process_executor_patch = ProcessPoolExecutorPatch(
        original=None,
        had_original=False,
        created_process_module=False,
        process_module=None,
        futures_had_process_attr=False,
        futures_original_process_attr=None,
    )
    try:
        process_executor_patch = install_process_pool_executor(
            patches, futures
        )
        install_multiprocessing_core(
            patches,
            multiprocessing,
            multiprocessing_context,
        )
        install_multiprocessing_pool(
            patches, multiprocessing, multiprocessing_pool
        )
    except BaseException:
        patches.unpatch_all()()
        _restore_process_pool_executor(
            futures=futures,
            patch=process_executor_patch,
        )
        if not core_was_active:
            unpatch_wasm_concurrency_shims()
        raise

    unpatch = patches.unpatch_all()
    _state.set_process_compatibility_owns_core(not core_was_active)

    def _run_process_unpatch() -> None:
        unpatch()
        _restore_process_pool_executor(
            futures=futures,
            patch=process_executor_patch,
        )
        _state.set_active_process_unpatch(None)
        _state.set_process_compatibility_owns_core(False)

    _state.set_active_process_unpatch(_run_process_unpatch)

    def _guarded_process_unpatch() -> None:
        unpatch_wasm_process_compatibility_shims()

    return _guarded_process_unpatch


def _import_submodule_for_patch(
    patches: WasmPatchSet,
    parent: Any,
    module_name: str,
    parent_attr: str,
) -> ModuleType:
    had_module = module_name in sys.modules
    had_parent_attr = hasattr(parent, parent_attr)
    original_parent_attr = getattr(parent, parent_attr, None)
    module = import_module(module_name)
    if had_module:
        return module

    def _remove_imported_submodule() -> None:
        if sys.modules.get(module_name) is module:
            sys.modules.pop(module_name, None)
        if getattr(parent, parent_attr, None) is module:
            if had_parent_attr:
                setattr(parent, parent_attr, original_parent_attr)
            else:
                delattr(parent, parent_attr)

    patches.add_cleanup(_remove_imported_submodule)
    return module


def _restore_process_pool_executor(
    *,
    futures: Any,
    patch: ProcessPoolExecutorPatch,
) -> None:
    from marimo._runtime._wasm._concurrency._futures import (
        AsyncioProcessPoolExecutor,
    )

    if patch.had_original:
        if (
            getattr(futures, "ProcessPoolExecutor", None)
            is AsyncioProcessPoolExecutor
        ):
            futures.ProcessPoolExecutor = patch.original
    elif (
        "ProcessPoolExecutor" in vars(futures)
        and futures.ProcessPoolExecutor is AsyncioProcessPoolExecutor
    ):
        del futures.ProcessPoolExecutor
    if patch.created_process_module:
        if (
            sys.modules.get("concurrent.futures.process")
            is patch.process_module
        ):
            sys.modules.pop("concurrent.futures.process", None)
        if getattr(futures, "process", None) is patch.process_module:
            if patch.futures_had_process_attr:
                futures.process = patch.futures_original_process_attr
            else:
                del futures.process


def unpatch_wasm_concurrency_shims() -> None:
    _state.discard_finished_runtime_records()
    if _state.has_live_wasm_work():
        raise RuntimeError("cannot unpatch WASM concurrency shims while live")
    process_unpatch = _state.active_process_unpatch()
    if process_unpatch is not None:
        process_unpatch()
    unpatch = _state.active_unpatch()
    if unpatch is not None:
        unpatch()


def unpatch_wasm_process_compatibility_shims() -> None:
    _state.discard_finished_runtime_records()
    owns_core = _state.process_compatibility_owns_core()
    if owns_core and _state.has_live_core_work():
        raise RuntimeError("cannot unpatch WASM concurrency shims while live")
    if _state.has_live_process_work():
        raise RuntimeError(
            "cannot unpatch WASM process compatibility shims while live"
        )
    process_unpatch = _state.active_process_unpatch()
    if process_unpatch is not None:
        process_unpatch()
        if owns_core:
            unpatch_wasm_concurrency_shims()


def shutdown_live_wasm_concurrency_work() -> None:
    """Request cooperative cancellation for live WASM shim work."""
    _state.request_shutdown()


async def shutdown_live_wasm_concurrency_work_async(
    timeout: float = 1,
) -> None:
    """Request cancellation and wait for live WASM shim work to finish."""
    _state.request_shutdown()
    await _state.wait_until_idle(timeout)


async def wait_for_live_wasm_concurrency_work_async(
    timeout: float = 0.05,
) -> bool:
    """Give cooperative WASM shim work a bounded chance to finish."""
    return await _state.wait_until_idle_or_timeout(timeout)
