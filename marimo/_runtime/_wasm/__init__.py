# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

from marimo._runtime._wasm._concurrency import _state
from marimo._runtime._wasm._concurrency._install import (
    install_wasm_concurrency_shims,
    install_wasm_process_compatibility_shims,
    shutdown_live_wasm_concurrency_work,
    shutdown_live_wasm_concurrency_work_async,
    unpatch_wasm_concurrency_shims,
    unpatch_wasm_process_compatibility_shims,
    wait_for_live_wasm_concurrency_work_async,
)
from marimo._runtime._wasm._patches import Unpatch


def ensure_wasm_runtime_bootstrapped() -> Unpatch:
    """Install WASM runtime patches for user notebook execution."""
    core_was_active = _state.active_unpatch() is not None
    try:
        install_wasm_concurrency_shims()
        install_wasm_process_compatibility_shims()
    except BaseException:
        if not core_was_active:
            unpatch_wasm_concurrency_shims()
        raise
    return unpatch_wasm_runtime


bootstrap_wasm_runtime = ensure_wasm_runtime_bootstrapped


def unpatch_wasm_runtime() -> None:
    """Remove active WASM concurrency patches for test teardown.

    This removes the bootstrap concurrency patches. The underlying unpatcher
    raises `RuntimeError` when shim threads or processes are still live.
    """
    unpatch_wasm_concurrency_shims()


def unpatch_wasm_process_compatibility() -> None:
    """Remove active process-shaped compatibility patches."""
    unpatch_wasm_process_compatibility_shims()


def shutdown_wasm_runtime_work() -> None:
    """Request cooperative cancellation for live WASM runtime work."""
    shutdown_live_wasm_concurrency_work()


async def shutdown_wasm_runtime_work_async(timeout: float = 1) -> None:
    """Request cancellation and wait for live WASM runtime work."""
    await shutdown_live_wasm_concurrency_work_async(timeout=timeout)


async def wait_for_wasm_runtime_work_async(timeout: float = 0.05) -> bool:
    """Give cooperative WASM runtime work a bounded chance to finish."""
    return await wait_for_live_wasm_concurrency_work_async(timeout=timeout)


__all__ = [
    "Unpatch",
    "bootstrap_wasm_runtime",
    "ensure_wasm_runtime_bootstrapped",
    "install_wasm_concurrency_shims",
    "shutdown_wasm_runtime_work",
    "shutdown_wasm_runtime_work_async",
    "unpatch_wasm_process_compatibility",
    "unpatch_wasm_runtime",
    "wait_for_wasm_runtime_work_async",
]
