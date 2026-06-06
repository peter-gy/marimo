# Copyright 2026 Marimo. All rights reserved.
"""Record semantic limits of WASM concurrency adapters.

Adapters call the `record_*` functions when a Python API keeps its callable
shape but cannot keep the matching CPython runtime semantics in Pyodide. Common
cases are ignored worker counts, serialized executor lanes, and
same-interpreter queue references. Runtime tests and validation scripts read
snapshots through `get_wasm_concurrency_diagnostics()` to assert that contract.
The registry stays internal to the WASM runtime.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
DiagnosticDetails: TypeAlias = dict[str, JsonScalar]
JsonDiagnostic: TypeAlias = dict[str, object]


class WasmConcurrencyTier(str, Enum):
    """Classify how closely a WASM adapter matches the checked Python API.

    `API_COMPATIBLE` means the tested behavior matches the Python API contract.
    `SERIALIZED` means work runs one item at a time in the current Pyodide
    interpreter. `COOPERATIVE` means waits or cancellation depend on JSPI and
    cannot preempt running Python code. `BLOCKED` means the API would require
    process, shared-memory, or OS support that Pyodide does not provide.
    """

    API_COMPATIBLE = "api-compatible"
    SERIALIZED = "serialized"
    COOPERATIVE = "cooperative-only"
    BLOCKED = "blocked"


TIER_LABELS: dict[WasmConcurrencyTier, str] = {
    WasmConcurrencyTier.API_COMPATIBLE: (
        "API-compatible for checked behavior"
    ),
    WasmConcurrencyTier.SERIALIZED: (
        "serialized in the current Pyodide interpreter"
    ),
    WasmConcurrencyTier.COOPERATIVE: "cooperative wait through JSPI",
    WasmConcurrencyTier.BLOCKED: "blocked in WASM",
}

TIER_API_COMPATIBLE = WasmConcurrencyTier.API_COMPATIBLE.value
TIER_COMPATIBLE_SERIALIZED = WasmConcurrencyTier.SERIALIZED.value
TIER_COOPERATIVE_ONLY = WasmConcurrencyTier.COOPERATIVE.value
TIER_BLOCKED = WasmConcurrencyTier.BLOCKED.value


class WasmConcurrencyDiagnosticCode(str, Enum):
    DIAGNOSTIC_EVENTS_DROPPED = "diagnostics.events_dropped"
    EXECUTOR_SERIALIZED = "executor.serialized"
    EXECUTOR_WORKER_COUNT_IGNORED = "executor.worker_count_ignored"
    EXECUTOR_CHUNKSIZE_IGNORED = "executor.chunksize_ignored"
    PROCESS_EXECUTOR_SERIALIZED = "process_executor.serialized"
    PROCESS_EXECUTOR_CONTEXT_IGNORED = "process_executor.context_ignored"
    PROCESS_EXECUTOR_MAX_TASKS_IGNORED = "process_executor.max_tasks_ignored"
    PROCESS_STARTED_AS_ASYNCIO_TASK = "process.started_as_asyncio_task"
    PROCESS_KILL_COOPERATIVE_ONLY = "process.kill_cooperative_only"
    MP_QUEUE_REFERENCE_SEMANTICS = "multiprocessing.queue_reference_semantics"
    MP_SIMPLE_QUEUE_REFERENCE_SEMANTICS = (
        "multiprocessing.simple_queue_reference_semantics"
    )
    MP_POOL_SERIALIZED = "multiprocessing.pool_serialized"
    MP_POOL_CHUNKSIZE_IGNORED = "multiprocessing.pool_chunksize_ignored"


@dataclass(frozen=True)
class WasmConcurrencyDiagnostic:
    """One recorded limit for a callable WASM concurrency API."""

    code: WasmConcurrencyDiagnosticCode
    api: str
    tier: WasmConcurrencyTier
    message: str
    details: DiagnosticDetails
    count: int = 1

    def as_dict(self) -> JsonDiagnostic:
        return {
            "code": self.code.value,
            "api": self.api,
            "tier": self.tier.value,
            "tier_label": TIER_LABELS[self.tier],
            "message": self.message,
            "details": dict(sorted(self.details.items())),
            "count": self.count,
        }


DiagnosticKey = tuple[
    WasmConcurrencyDiagnosticCode,
    str,
    tuple[tuple[str, JsonScalar], ...],
]

_MAX_DIAGNOSTICS = 256


class WasmConcurrencyDiagnostics:
    """Stores bounded, deduplicated concurrency diagnostics."""

    def __init__(self) -> None:
        self._events: OrderedDict[DiagnosticKey, WasmConcurrencyDiagnostic] = (
            OrderedDict()
        )
        self._dropped: int = 0

    def record(
        self,
        *,
        code: WasmConcurrencyDiagnosticCode,
        api: str,
        tier: WasmConcurrencyTier,
        message: str,
        details: DiagnosticDetails | None = None,
    ) -> None:
        normalized = _normalize_details(details or {})
        key = (code, api, tuple(sorted(normalized.items())))
        existing = self._events.get(key)
        if existing is not None:
            self._events[key] = WasmConcurrencyDiagnostic(
                code=existing.code,
                api=existing.api,
                tier=existing.tier,
                message=existing.message,
                details=existing.details,
                count=existing.count + 1,
            )
            return

        if len(self._events) >= _MAX_DIAGNOSTICS:
            self._dropped += 1
            self._record_dropped()
            return

        self._events[key] = WasmConcurrencyDiagnostic(
            code=code,
            api=api,
            tier=tier,
            message=message,
            details=normalized,
        )

    def snapshot(
        self, *, clear: bool = False
    ) -> list[WasmConcurrencyDiagnostic]:
        diagnostics = list(self._events.values())
        if clear:
            self.clear()
        return diagnostics

    def snapshot_json(self, *, clear: bool = False) -> list[JsonDiagnostic]:
        return [
            diagnostic.as_dict() for diagnostic in self.snapshot(clear=clear)
        ]

    def clear(self) -> None:
        self._events.clear()
        self._dropped = 0

    def _record_dropped(self) -> None:
        code = WasmConcurrencyDiagnosticCode.DIAGNOSTIC_EVENTS_DROPPED
        key = (code, "marimo._runtime._wasm", ())
        if key not in self._events and len(self._events) >= _MAX_DIAGNOSTICS:
            self._events.popitem(last=False)
        self._events[key] = WasmConcurrencyDiagnostic(
            code=code,
            api="marimo._runtime._wasm",
            tier=WasmConcurrencyTier.BLOCKED,
            message="diagnostic buffer reached its event limit",
            details={"dropped": self._dropped},
            count=1,
        )


_DIAGNOSTICS = WasmConcurrencyDiagnostics()


def _record_wasm_concurrency_diagnostic(
    *,
    code: WasmConcurrencyDiagnosticCode,
    api: str,
    tier: WasmConcurrencyTier,
    message: str,
    details: DiagnosticDetails | None = None,
) -> None:
    _DIAGNOSTICS.record(
        code=code,
        api=api,
        tier=tier,
        message=message,
        details=details,
    )


def get_wasm_concurrency_diagnostics(
    *, clear: bool = False
) -> list[WasmConcurrencyDiagnostic]:
    return _DIAGNOSTICS.snapshot(clear=clear)


def get_wasm_concurrency_diagnostics_json(
    *, clear: bool = False
) -> list[JsonDiagnostic]:
    return _DIAGNOSTICS.snapshot_json(clear=clear)


def clear_wasm_concurrency_diagnostics() -> None:
    _DIAGNOSTICS.clear()


def record_executor_worker_count_ignored(
    *, api: str, requested_workers: int | None
) -> None:
    if requested_workers is None or requested_workers <= 1:
        return
    _record_wasm_concurrency_diagnostic(
        code=WasmConcurrencyDiagnosticCode.EXECUTOR_WORKER_COUNT_IGNORED,
        api=api,
        tier=WasmConcurrencyTier.SERIALIZED,
        message=(
            "worker count is accepted for API compatibility. Submitted work "
            "still runs on one Pyodide event-loop lane"
        ),
        details={
            "requested_workers": requested_workers,
            "effective_workers": 1,
        },
    )


def record_executor_serialized(
    *, api: str, requested_workers: int | None
) -> None:
    _record_wasm_concurrency_diagnostic(
        code=WasmConcurrencyDiagnosticCode.EXECUTOR_SERIALIZED,
        api=api,
        tier=WasmConcurrencyTier.SERIALIZED,
        message=(
            "executor work runs on one Pyodide event-loop lane. The API "
            "returns futures, but it does not create worker threads"
        ),
        details={
            "requested_workers": requested_workers,
            "effective_workers": 1,
        },
    )


def record_executor_chunksize_ignored(
    *, api: str, chunksize: int | None
) -> None:
    if chunksize in (None, 1):
        return
    _record_wasm_concurrency_diagnostic(
        code=WasmConcurrencyDiagnosticCode.EXECUTOR_CHUNKSIZE_IGNORED,
        api=api,
        tier=WasmConcurrencyTier.SERIALIZED,
        message="chunksize is accepted but has no effect on one execution lane",
        details={"chunksize": chunksize},
    )


def record_process_executor_serialized(
    *,
    max_workers: int | None,
    mp_context: object | None,
    max_tasks_per_child: int | None,
) -> None:
    _record_wasm_concurrency_diagnostic(
        code=WasmConcurrencyDiagnosticCode.PROCESS_EXECUTOR_SERIALIZED,
        api="concurrent.futures.ProcessPoolExecutor",
        tier=WasmConcurrencyTier.SERIALIZED,
        message=(
            "ProcessPoolExecutor is serialized in the main Pyodide "
            "interpreter. It creates no child processes and provides no "
            "memory isolation or pickle-copy boundary"
        ),
        details={
            "requested_workers": max_workers,
            "effective_workers": 1,
            "memory_isolation": False,
            "pickle_copy_boundary": False,
            "mp_context": type(mp_context).__name__
            if mp_context is not None
            else None,
            "max_tasks_per_child": max_tasks_per_child,
        },
    )
    record_executor_worker_count_ignored(
        api="concurrent.futures.ProcessPoolExecutor",
        requested_workers=max_workers,
    )
    if mp_context is not None:
        _record_wasm_concurrency_diagnostic(
            code=WasmConcurrencyDiagnosticCode.PROCESS_EXECUTOR_CONTEXT_IGNORED,
            api="concurrent.futures.ProcessPoolExecutor",
            tier=WasmConcurrencyTier.SERIALIZED,
            message=(
                "mp_context is accepted for API compatibility. It does not "
                "create child processes or process isolation"
            ),
            details={"mp_context": type(mp_context).__name__},
        )
    if max_tasks_per_child is not None:
        _record_wasm_concurrency_diagnostic(
            code=(
                WasmConcurrencyDiagnosticCode.PROCESS_EXECUTOR_MAX_TASKS_IGNORED
            ),
            api="concurrent.futures.ProcessPoolExecutor",
            tier=WasmConcurrencyTier.SERIALIZED,
            message=(
                "max_tasks_per_child is accepted, but no worker process "
                "exists to recycle"
            ),
            details={"max_tasks_per_child": max_tasks_per_child},
        )


def record_process_started(*, pid: int | None) -> None:
    _record_wasm_concurrency_diagnostic(
        code=WasmConcurrencyDiagnosticCode.PROCESS_STARTED_AS_ASYNCIO_TASK,
        api="multiprocessing.Process",
        tier=WasmConcurrencyTier.SERIALIZED,
        message=(
            "multiprocessing.Process runs as an asyncio-backed local task in "
            "the main Pyodide interpreter. Its pid is synthetic, memory is "
            "shared with the parent, and values are not pickle-copied"
        ),
        details={
            "synthetic_pid": pid is not None,
            "memory_isolation": False,
            "pickle_copy_boundary": False,
        },
    )


def record_process_kill(*, pid: int | None) -> None:
    _record_wasm_concurrency_diagnostic(
        code=WasmConcurrencyDiagnosticCode.PROCESS_KILL_COOPERATIVE_ONLY,
        api="multiprocessing.Process.kill",
        tier=WasmConcurrencyTier.COOPERATIVE,
        message=(
            "Process termination is cooperative. It cancels pending shim "
            "tasks but cannot preempt running Python bytecode"
        ),
        details={
            "synthetic_pid": pid is not None,
            "preemptive_cancel": False,
            "signal_delivery": False,
        },
    )


def record_queue_reference_semantics(*, maxsize: int) -> None:
    _record_wasm_concurrency_diagnostic(
        code=WasmConcurrencyDiagnosticCode.MP_QUEUE_REFERENCE_SEMANTICS,
        api="multiprocessing.Queue",
        tier=WasmConcurrencyTier.SERIALIZED,
        message=(
            "multiprocessing.Queue stores object references in the same "
            "interpreter. Values are not pickled or copied across a process "
            "boundary"
        ),
        details={
            "maxsize": maxsize,
            "pickle_copy_boundary": False,
            "bounded_put_supported": True,
        },
    )


def record_simple_queue_reference_semantics() -> None:
    _record_wasm_concurrency_diagnostic(
        code=(
            WasmConcurrencyDiagnosticCode.MP_SIMPLE_QUEUE_REFERENCE_SEMANTICS
        ),
        api="multiprocessing.SimpleQueue",
        tier=WasmConcurrencyTier.SERIALIZED,
        message=(
            "multiprocessing.SimpleQueue stores object references in the same "
            "interpreter. Values are not pickled or copied across a process "
            "boundary"
        ),
        details={"pickle_copy_boundary": False},
    )


def record_pool_serialized(
    *,
    processes: int | None,
    maxtasksperchild: int | None,
    context: object | None,
) -> None:
    _record_wasm_concurrency_diagnostic(
        code=WasmConcurrencyDiagnosticCode.MP_POOL_SERIALIZED,
        api="multiprocessing.Pool",
        tier=WasmConcurrencyTier.SERIALIZED,
        message=(
            "multiprocessing.Pool is serialized in the main Pyodide "
            "interpreter. It creates no worker processes and provides no "
            "memory isolation or pickle-copy boundary"
        ),
        details={
            "requested_processes": processes,
            "effective_processes": 1,
            "maxtasksperchild": maxtasksperchild,
            "context": type(context).__name__ if context is not None else None,
            "memory_isolation": False,
            "pickle_copy_boundary": False,
        },
    )
    record_executor_worker_count_ignored(
        api="multiprocessing.Pool",
        requested_workers=processes,
    )


def record_pool_chunksize_ignored(*, api: str, chunksize: int | None) -> None:
    if chunksize in (None, 1):
        return
    _record_wasm_concurrency_diagnostic(
        code=WasmConcurrencyDiagnosticCode.MP_POOL_CHUNKSIZE_IGNORED,
        api=api,
        tier=WasmConcurrencyTier.SERIALIZED,
        message="chunksize is accepted but has no effect on one execution lane",
        details={"chunksize": chunksize},
    )


def _normalize_details(details: DiagnosticDetails) -> DiagnosticDetails:
    normalized: DiagnosticDetails = {}
    for key, value in details.items():
        if isinstance(value, float) and not math.isfinite(value):
            raise TypeError(
                "WASM concurrency diagnostic details must be finite "
                f"JSON-safe scalars, got {value!r} for {key!r}"
            )
        if isinstance(value, str | int | float | bool) or value is None:
            normalized[key] = value
        else:
            raise TypeError(
                "WASM concurrency diagnostic details must be JSON-safe "
                f"scalars, got {type(value).__name__} for {key!r}"
            )
    return normalized
