# Copyright 2026 Marimo. All rights reserved.
"""Declarative patch plan for the default WASM concurrency surface.

The default surface runs inside the current Pyodide interpreter. It gives
thread-shaped APIs synthetic identity, local storage, and cooperative waits
without creating OS threads.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from marimo._runtime._wasm._concurrency import _state
from marimo._runtime._wasm._concurrency._futures import (
    AsyncioThreadPoolExecutor,
    wasm_as_completed,
    wasm_wait,
)
from marimo._runtime._wasm._concurrency._thread_locals import AsyncLocal
from marimo._runtime._wasm._concurrency._threading import (
    AsyncCondition,
    AsyncEvent,
    AsyncioThread,
    AsyncLock,
    AsyncRLock,
    AsyncTimer,
)
from marimo._runtime._wasm._patches import WasmPatchSet

if TYPE_CHECKING:
    from collections.abc import Mapping


class WasmConcurrencyPatchGroup(Enum):
    THREADING_CORE = "threading-core"
    FUTURES_CORE = "futures-core"


class PatchOwner(Enum):
    THREADING = "threading"
    FUTURES = "concurrent.futures"
    FUTURES_THREAD = "concurrent.futures.thread"


@dataclass(frozen=True)
class PatchSpec:
    group: WasmConcurrencyPatchGroup
    owner: PatchOwner
    attr: str
    replacement: Any
    reason: str


DEFAULT_PATCH_SPECS: tuple[PatchSpec, ...] = (
    PatchSpec(
        WasmConcurrencyPatchGroup.THREADING_CORE,
        PatchOwner.THREADING,
        "Thread",
        AsyncioThread,
        "Run thread-shaped work as a synthetic asyncio-backed thread.",
    ),
    PatchSpec(
        WasmConcurrencyPatchGroup.THREADING_CORE,
        PatchOwner.THREADING,
        "Timer",
        AsyncTimer,
        "Run timer callbacks through the browser event loop.",
    ),
    PatchSpec(
        WasmConcurrencyPatchGroup.THREADING_CORE,
        PatchOwner.THREADING,
        "Event",
        AsyncEvent,
        "Provide cooperative event waits.",
    ),
    PatchSpec(
        WasmConcurrencyPatchGroup.THREADING_CORE,
        PatchOwner.THREADING,
        "Lock",
        AsyncLock,
        "Provide mutual exclusion in the current Pyodide interpreter.",
    ),
    PatchSpec(
        WasmConcurrencyPatchGroup.THREADING_CORE,
        PatchOwner.THREADING,
        "Condition",
        AsyncCondition,
        "Provide cooperative condition waits.",
    ),
    PatchSpec(
        WasmConcurrencyPatchGroup.THREADING_CORE,
        PatchOwner.THREADING,
        "RLock",
        AsyncRLock,
        "Provide recursive locking in the current Pyodide interpreter.",
    ),
    PatchSpec(
        WasmConcurrencyPatchGroup.THREADING_CORE,
        PatchOwner.THREADING,
        "local",
        AsyncLocal,
        "Isolate shim-thread locals by synthetic thread identity.",
    ),
    PatchSpec(
        WasmConcurrencyPatchGroup.THREADING_CORE,
        PatchOwner.THREADING,
        "current_thread",
        _state.current_thread,
        "Report the active synthetic thread identity.",
    ),
    PatchSpec(
        WasmConcurrencyPatchGroup.THREADING_CORE,
        PatchOwner.THREADING,
        "get_ident",
        _state.current_ident,
        "Return an opaque synthetic ident inside shim execution.",
    ),
    PatchSpec(
        WasmConcurrencyPatchGroup.THREADING_CORE,
        PatchOwner.THREADING,
        "get_native_id",
        _state.current_native_id,
        "Return an opaque synthetic native id inside shim execution.",
    ),
    PatchSpec(
        WasmConcurrencyPatchGroup.THREADING_CORE,
        PatchOwner.THREADING,
        "enumerate",
        _state.active_threads,
        "Include live synthetic threads in thread enumeration.",
    ),
    PatchSpec(
        WasmConcurrencyPatchGroup.THREADING_CORE,
        PatchOwner.THREADING,
        "active_count",
        _state.active_count,
        "Include live synthetic threads in the active count.",
    ),
    PatchSpec(
        WasmConcurrencyPatchGroup.FUTURES_CORE,
        PatchOwner.FUTURES,
        "ThreadPoolExecutor",
        AsyncioThreadPoolExecutor,
        "Serialize thread-pool work on one asyncio-backed execution lane.",
    ),
    PatchSpec(
        WasmConcurrencyPatchGroup.FUTURES_CORE,
        PatchOwner.FUTURES_THREAD,
        "ThreadPoolExecutor",
        AsyncioThreadPoolExecutor,
        "Keep direct concurrent.futures.thread imports on the same executor.",
    ),
    PatchSpec(
        WasmConcurrencyPatchGroup.FUTURES_CORE,
        PatchOwner.FUTURES,
        "wait",
        wasm_wait,
        "Use cooperative waits for pending shim futures.",
    ),
    PatchSpec(
        WasmConcurrencyPatchGroup.FUTURES_CORE,
        PatchOwner.FUTURES,
        "as_completed",
        wasm_as_completed,
        "Use cooperative iteration for pending shim futures.",
    ),
)


def apply_patch_specs(
    patches: WasmPatchSet,
    specs: tuple[PatchSpec, ...],
    owners: Mapping[PatchOwner, Any],
) -> None:
    for spec in specs:
        owner = owners[spec.owner]
        replacement = spec.replacement

        def replacement_factory(
            _original: object,
            replacement: Any = replacement,
        ) -> Any:
            return replacement

        patches.replace(owner, spec.attr, replacement_factory)
