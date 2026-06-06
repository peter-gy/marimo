# Copyright 2026 Marimo. All rights reserved.
"""Repair thread-local storage captured before WASM bootstrap.

The normal Pyodide path installs the WASM concurrency shim before user code
imports runtime modules. Defensive import paths can still load marimo runtime
context or stream proxy modules first. This repair phase is intentionally
narrow: it replaces only the known thread-local storage objects that would let
a shim thread share and tear down the parent runtime context.
"""

from __future__ import annotations

import sys
from typing import Any

from marimo._runtime._wasm._concurrency._thread_locals import AsyncLocal
from marimo._runtime._wasm._patches import WasmPatchSet


def repair_preimported_runtime_context_storage(
    patches: WasmPatchSet,
) -> None:
    """Replace context storage captured before the WASM thread-local patch."""
    context_types = sys.modules.get("marimo._runtime.context.types")
    if context_types is None:
        return

    original_storage = getattr(context_types, "_THREAD_LOCAL_CONTEXT", None)
    if isinstance(original_storage, AsyncLocal):
        return

    class WasmRuntimeContextStorage(AsyncLocal):
        def __init__(self) -> None:
            self.runtime_context = None

        def initialize(self, runtime_context: Any) -> None:
            self.runtime_context = runtime_context

    storage = WasmRuntimeContextStorage()
    storage.runtime_context = getattr(
        original_storage,
        "runtime_context",
        None,
    )

    def _sync_before_restore() -> None:
        if original_storage is not None:
            original_storage.runtime_context = storage.runtime_context

    patches.replace(
        context_types,
        "_THREAD_LOCAL_CONTEXT",
        lambda _original: storage,
        before_restore=_sync_before_restore,
    )


def repair_preimported_thread_local_stream_proxies(
    patches: WasmPatchSet,
) -> None:
    """Replace stdout and stderr proxy locals captured before bootstrap."""
    stream_proxy_module = sys.modules.get(
        "marimo._messaging.thread_local_streams"
    )
    if stream_proxy_module is None:
        return
    proxy_type = getattr(stream_proxy_module, "ThreadLocalStreamProxy", None)
    if not isinstance(proxy_type, type):
        return

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if not isinstance(stream, proxy_type):
            continue
        original_local = getattr(stream, "_local", None)
        if isinstance(original_local, AsyncLocal):
            continue
        repaired_local = AsyncLocal()
        repaired_local.stream = getattr(original_local, "stream", None)

        def _sync_before_restore(
            original: Any = original_local,
            repaired: AsyncLocal = repaired_local,
        ) -> None:
            original.stream = getattr(repaired, "stream", None)

        def _local_wrapper(
            _original: Any, local: AsyncLocal = repaired_local
        ) -> AsyncLocal:
            del _original
            return local

        patches.replace(
            stream,
            "_local",
            _local_wrapper,
            before_restore=_sync_before_restore,
        )
