# Copyright 2026 Marimo. All rights reserved.
"""Patch manifest for multiprocessing-shaped compatibility.

Every replacement in this file preserves a callable `multiprocessing` entrypoint
inside the current Pyodide interpreter. The manifest does not imply OS process
support.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from marimo._runtime._wasm._concurrency._mp_context import (
    barrier_factory,
    bounded_semaphore_factory,
    condition_factory,
    cpu_count,
    direct_barrier_factory,
    direct_bounded_semaphore_factory,
    direct_condition_factory,
    direct_event_factory,
    direct_lock_factory,
    direct_rlock_factory,
    direct_semaphore_factory,
    event_factory,
    freeze_support,
    get_all_start_methods,
    get_start_method,
    lock_factory,
    rlock_factory,
    semaphore_factory,
    set_start_method,
)
from marimo._runtime._wasm._concurrency._mp_pool import pool_factory
from marimo._runtime._wasm._concurrency._mp_process import (
    AsyncProcess,
    active_children,
    current_process,
    parent_process,
    process_factory,
)
from marimo._runtime._wasm._concurrency._mp_queue import (
    direct_queue_factory,
    direct_simple_queue_factory,
    queue_factory,
    simple_queue_factory,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class MultiprocessingFactoryPatch:
    attr: str
    replacement: Callable[..., Any]


TOP_LEVEL_MULTIPROCESSING_FACTORIES = (
    MultiprocessingFactoryPatch("Process", AsyncProcess),
    MultiprocessingFactoryPatch("Queue", direct_queue_factory),
    MultiprocessingFactoryPatch("SimpleQueue", direct_simple_queue_factory),
    MultiprocessingFactoryPatch("Event", direct_event_factory),
    MultiprocessingFactoryPatch("Lock", direct_lock_factory),
    MultiprocessingFactoryPatch("RLock", direct_rlock_factory),
    MultiprocessingFactoryPatch("Semaphore", direct_semaphore_factory),
    MultiprocessingFactoryPatch(
        "BoundedSemaphore", direct_bounded_semaphore_factory
    ),
    MultiprocessingFactoryPatch("Condition", direct_condition_factory),
    MultiprocessingFactoryPatch("Barrier", direct_barrier_factory),
)

TOP_LEVEL_MULTIPROCESSING_HELPERS = (
    MultiprocessingFactoryPatch("cpu_count", cpu_count),
    MultiprocessingFactoryPatch(
        "get_all_start_methods", get_all_start_methods
    ),
    MultiprocessingFactoryPatch("get_start_method", get_start_method),
    MultiprocessingFactoryPatch("set_start_method", set_start_method),
    MultiprocessingFactoryPatch("current_process", current_process),
    MultiprocessingFactoryPatch("parent_process", parent_process),
    MultiprocessingFactoryPatch("active_children", active_children),
    MultiprocessingFactoryPatch("freeze_support", freeze_support),
)

CONTEXT_MULTIPROCESSING_FACTORIES = (
    MultiprocessingFactoryPatch("Process", process_factory),
    MultiprocessingFactoryPatch("Queue", queue_factory),
    MultiprocessingFactoryPatch("SimpleQueue", simple_queue_factory),
    MultiprocessingFactoryPatch("Pool", pool_factory),
    MultiprocessingFactoryPatch("Event", event_factory),
    MultiprocessingFactoryPatch("Lock", lock_factory),
    MultiprocessingFactoryPatch("RLock", rlock_factory),
    MultiprocessingFactoryPatch("Semaphore", semaphore_factory),
    MultiprocessingFactoryPatch("BoundedSemaphore", bounded_semaphore_factory),
    MultiprocessingFactoryPatch("Condition", condition_factory),
    MultiprocessingFactoryPatch("Barrier", barrier_factory),
)

BLOCKED_MULTIPROCESSING_FACTORIES = (
    "Pipe",
    "Manager",
    "JoinableQueue",
    "Value",
    "Array",
    "RawValue",
    "RawArray",
)
