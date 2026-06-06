# Copyright 2026 Marimo. All rights reserved.
"""Pyodide adapters for Python concurrency APIs.

The default WASM install covers thread-shaped APIs: `threading`,
`concurrent.futures.ThreadPoolExecutor`, and `asyncio` thread helpers. These
adapters preserve the checked Python API shape while running on Pyodide's
single interpreter and event-loop lane.

Process-shaped APIs are multiprocessing and process-pool entrypoints whose
constructors, results, callbacks, and lifecycle methods can be exercised in
WASM. They are same-interpreter compatibility adapters. They do not create OS
processes, memory isolation, signal delivery, or pickle-copy IPC boundaries.

`serialized` means submitted work is drained one item at a time in the current
Pyodide interpreter. `cooperative-only` means a blocking-looking wait can make
progress only when Pyodide exposes JSPI `run_sync`. `_threading` owns synthetic
thread identity and synchronization primitives. `_futures` owns serialized
executors. `_mp_process`, `_mp_queue`, `_mp_pool`, and `_mp_context` own the
process-shaped adapters. `_wait` owns the JSPI bridge.
"""

from __future__ import annotations

from marimo._runtime._wasm._concurrency._wait import (
    UnsupportedWasmConcurrencyError,
)

__all__ = [
    "UnsupportedWasmConcurrencyError",
]
