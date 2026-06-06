# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import math
import multiprocessing
import threading
from typing import Any, cast

import pytest

from marimo._runtime._wasm._concurrency._diagnostics import (
    WasmConcurrencyDiagnosticCode,
    WasmConcurrencyDiagnostics,
    WasmConcurrencyTier,
    clear_wasm_concurrency_diagnostics,
    get_wasm_concurrency_diagnostics,
    get_wasm_concurrency_diagnostics_json,
)
from marimo._runtime._wasm._concurrency._install import (
    install_wasm_concurrency_shims,
    install_wasm_process_compatibility_shims,
)
from tests.conftest import mock_pyodide


def assert_diagnostic_json(
    diagnostic: dict[str, Any],
    *,
    code: str,
    api: str,
    tier: str,
    tier_label: str | None = None,
    details: dict[str, Any],
    count: int,
) -> None:
    assert diagnostic["code"] == code
    assert diagnostic["api"] == api
    assert diagnostic["tier"] == tier
    if tier_label is not None:
        assert diagnostic["tier_label"] == tier_label
    assert diagnostic["details"] == details
    assert diagnostic["count"] == count
    assert isinstance(diagnostic["message"], str)
    assert diagnostic["message"]
    assert isinstance(diagnostic["tier_label"], str)
    assert diagnostic["tier_label"]


def find_diagnostic_json(
    diagnostics: list[dict[str, Any]],
    *,
    code: str,
    api: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    for diagnostic in diagnostics:
        if (
            diagnostic["code"] == code
            and diagnostic["api"] == api
            and diagnostic["details"] == details
        ):
            return diagnostic
    raise AssertionError((code, api, details, diagnostics))


def test_wasm_concurrency_diagnostics_record_semantic_limit_codes() -> None:
    with mock_pyodide():
        clear_wasm_concurrency_diagnostics()
        unpatch = install_wasm_process_compatibility_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=2
            ) as executor:
                assert executor.submit(lambda: "thread").result() == "thread"

            process_context = multiprocessing.get_context("spawn")
            with concurrent.futures.ProcessPoolExecutor(
                max_workers=3,
                mp_context=process_context,
                max_tasks_per_child=2,
            ) as executor:
                assert executor.submit(lambda: "process").result() == "process"
                assert list(
                    executor.map(
                        lambda value: value,
                        [1, 2],
                        chunksize=4,
                    )
                ) == [1, 2]

            process = multiprocessing.Process(target=lambda: None)
            process.start()
            process.join(timeout=1)
            assert not process.is_alive()

            values: Any = multiprocessing.Queue()
            values.put("queued")
            simple_values: Any = multiprocessing.SimpleQueue()
            simple_values.put("simple")

            with multiprocessing.Pool(2, maxtasksperchild=3) as pool:
                assert pool.map(lambda value: value, [1], chunksize=5) == [1]
                assert list(
                    pool.imap_unordered(
                        lambda value: value,
                        [2],
                        chunksize=6,
                    )
                ) == [2]

            diagnostics = get_wasm_concurrency_diagnostics_json()
            codes = {diagnostic["code"] for diagnostic in diagnostics}

            assert {
                "executor.worker_count_ignored",
                "executor.chunksize_ignored",
                "process_executor.serialized",
                "process_executor.context_ignored",
                "process_executor.max_tasks_ignored",
                "process.started_as_asyncio_task",
                "multiprocessing.queue_reference_semantics",
                "multiprocessing.simple_queue_reference_semantics",
                "multiprocessing.pool_serialized",
                "multiprocessing.pool_chunksize_ignored",
            } <= codes
            serialized_label = "serialized in the current Pyodide interpreter"
            process_executor = find_diagnostic_json(
                diagnostics,
                code="process_executor.serialized",
                api="concurrent.futures.ProcessPoolExecutor",
                details={
                    "effective_workers": 1,
                    "max_tasks_per_child": 2,
                    "memory_isolation": False,
                    "mp_context": type(process_context).__name__,
                    "pickle_copy_boundary": False,
                    "requested_workers": 3,
                },
            )
            assert_diagnostic_json(
                process_executor,
                code="process_executor.serialized",
                api="concurrent.futures.ProcessPoolExecutor",
                tier="serialized",
                tier_label=serialized_label,
                details={
                    "effective_workers": 1,
                    "max_tasks_per_child": 2,
                    "memory_isolation": False,
                    "mp_context": type(process_context).__name__,
                    "pickle_copy_boundary": False,
                    "requested_workers": 3,
                },
                count=1,
            )
            context_ignored = find_diagnostic_json(
                diagnostics,
                code="process_executor.context_ignored",
                api="concurrent.futures.ProcessPoolExecutor",
                details={"mp_context": type(process_context).__name__},
            )
            assert_diagnostic_json(
                context_ignored,
                code="process_executor.context_ignored",
                api="concurrent.futures.ProcessPoolExecutor",
                tier="serialized",
                tier_label=serialized_label,
                details={"mp_context": type(process_context).__name__},
                count=1,
            )
            max_tasks_ignored = find_diagnostic_json(
                diagnostics,
                code="process_executor.max_tasks_ignored",
                api="concurrent.futures.ProcessPoolExecutor",
                details={"max_tasks_per_child": 2},
            )
            assert_diagnostic_json(
                max_tasks_ignored,
                code="process_executor.max_tasks_ignored",
                api="concurrent.futures.ProcessPoolExecutor",
                tier="serialized",
                tier_label=serialized_label,
                details={"max_tasks_per_child": 2},
                count=1,
            )
            process_started = find_diagnostic_json(
                diagnostics,
                code="process.started_as_asyncio_task",
                api="multiprocessing.Process",
                details={
                    "memory_isolation": False,
                    "pickle_copy_boundary": False,
                    "synthetic_pid": True,
                },
            )
            assert_diagnostic_json(
                process_started,
                code="process.started_as_asyncio_task",
                api="multiprocessing.Process",
                tier="serialized",
                tier_label=serialized_label,
                details={
                    "memory_isolation": False,
                    "pickle_copy_boundary": False,
                    "synthetic_pid": True,
                },
                count=1,
            )
            assert all(
                diagnostic["tier"] in {"serialized", "cooperative-only"}
                for diagnostic in diagnostics
            )
        finally:
            clear_wasm_concurrency_diagnostics()
            unpatch()


def test_default_thread_primitives_do_not_record_semantic_limit_diagnostics() -> (
    None
):
    with mock_pyodide():
        clear_wasm_concurrency_diagnostics()
        unpatch = install_wasm_concurrency_shims()
        try:
            event = threading.Event()
            event.set()
            assert event.wait(0)

            assert get_wasm_concurrency_diagnostics() == []
        finally:
            clear_wasm_concurrency_diagnostics()
            unpatch()


def test_default_thread_pool_records_serialized_lane_diagnostic() -> None:
    with mock_pyodide():
        clear_wasm_concurrency_diagnostics()
        unpatch = install_wasm_concurrency_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                assert executor.submit(lambda: "ok").result() == "ok"

            [diagnostic] = get_wasm_concurrency_diagnostics()
            assert (
                diagnostic.code
                is WasmConcurrencyDiagnosticCode.EXECUTOR_SERIALIZED
            )
            assert diagnostic.api == "concurrent.futures.ThreadPoolExecutor"
            assert diagnostic.tier is WasmConcurrencyTier.SERIALIZED
            assert diagnostic.details == {
                "effective_workers": 1,
                "requested_workers": None,
            }
        finally:
            clear_wasm_concurrency_diagnostics()
            unpatch()


def test_wasm_thread_pool_records_serialized_lane_for_one_worker() -> None:
    with mock_pyodide():
        clear_wasm_concurrency_diagnostics()
        unpatch = install_wasm_concurrency_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=1
            ) as executor:
                assert executor.submit(lambda: "ok").result() == "ok"

            [diagnostic] = get_wasm_concurrency_diagnostics()
            assert (
                diagnostic.code
                is WasmConcurrencyDiagnosticCode.EXECUTOR_SERIALIZED
            )
            assert diagnostic.details == {
                "effective_workers": 1,
                "requested_workers": 1,
            }
        finally:
            clear_wasm_concurrency_diagnostics()
            unpatch()


def test_wasm_process_compatibility_records_shared_state_diagnostics() -> None:
    with mock_pyodide():
        clear_wasm_concurrency_diagnostics()
        unpatch = install_wasm_process_compatibility_shims()
        try:
            with concurrent.futures.ProcessPoolExecutor(
                max_workers=1
            ) as executor:
                assert executor.submit(lambda: "lambda works").result() == (
                    "lambda works"
                )

            parent_state: list[str] = []
            process = multiprocessing.Process(
                target=lambda state: state.append("mutated"),
                args=(parent_state,),
            )
            process.start()
            process.join(timeout=1)
            assert not process.is_alive()
            assert parent_state == ["mutated"]

            payload: list[str] = []
            values: Any = multiprocessing.Queue()
            values.put(payload)
            payload.append("same-reference")
            assert values.get(block=False) is payload

            simple_payload: list[str] = []
            simple_values: Any = multiprocessing.SimpleQueue()
            simple_values.put(simple_payload)
            simple_payload.append("same-reference")
            assert simple_values.get() is simple_payload

            with multiprocessing.Pool(1) as pool:
                assert pool.map(lambda value: value, [1]) == [1]

            diagnostics = get_wasm_concurrency_diagnostics()
            by_code = {
                diagnostic.code: diagnostic for diagnostic in diagnostics
            }

            process_pool = by_code[
                WasmConcurrencyDiagnosticCode.PROCESS_EXECUTOR_SERIALIZED
            ]
            assert process_pool.details["pickle_copy_boundary"] is False

            process_start = by_code[
                WasmConcurrencyDiagnosticCode.PROCESS_STARTED_AS_ASYNCIO_TASK
            ]
            assert process_start.details["memory_isolation"] is False

            queue_semantics = by_code[
                WasmConcurrencyDiagnosticCode.MP_QUEUE_REFERENCE_SEMANTICS
            ]
            assert queue_semantics.details["pickle_copy_boundary"] is False

            simple_queue_semantics = by_code[
                WasmConcurrencyDiagnosticCode.MP_SIMPLE_QUEUE_REFERENCE_SEMANTICS
            ]
            assert (
                simple_queue_semantics.details["pickle_copy_boundary"] is False
            )

            pool_diagnostic = by_code[
                WasmConcurrencyDiagnosticCode.MP_POOL_SERIALIZED
            ]
            assert pool_diagnostic.details["memory_isolation"] is False
            assert pool_diagnostic.details["pickle_copy_boundary"] is False
        finally:
            clear_wasm_concurrency_diagnostics()
            unpatch()


def test_wasm_diagnostics_reject_non_scalar_details() -> None:
    diagnostics = WasmConcurrencyDiagnostics()
    with pytest.raises(TypeError, match="JSON-safe scalars"):
        diagnostics.record(
            code=WasmConcurrencyDiagnosticCode.EXECUTOR_WORKER_COUNT_IGNORED,
            api="example",
            tier=WasmConcurrencyTier.SERIALIZED,
            message="bad details",
            details=cast(Any, {"nested": ["not-json-scalar"]}),
        )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_wasm_diagnostics_reject_non_finite_details(value: float) -> None:
    diagnostics = WasmConcurrencyDiagnostics()
    with pytest.raises(TypeError, match="finite JSON-safe scalars"):
        diagnostics.record(
            code=WasmConcurrencyDiagnosticCode.EXECUTOR_WORKER_COUNT_IGNORED,
            api="example",
            tier=WasmConcurrencyTier.SERIALIZED,
            message="bad details",
            details={"duration": value},
        )


@pytest.mark.asyncio
async def test_wasm_process_kill_records_cooperative_limits() -> None:
    with mock_pyodide():
        clear_wasm_concurrency_diagnostics()
        unpatch = install_wasm_process_compatibility_shims()
        release = asyncio.Event()

        async def wait_forever() -> None:
            await release.wait()

        try:
            process = multiprocessing.Process(target=wait_forever)
            process.start()
            await asyncio.sleep(0)
            assert process.is_alive()
            process.kill()
            for _ in range(5):
                await asyncio.sleep(0)
                if not process.is_alive():
                    break

            kill = next(
                diagnostic
                for diagnostic in get_wasm_concurrency_diagnostics()
                if diagnostic.code
                is WasmConcurrencyDiagnosticCode.PROCESS_KILL_COOPERATIVE_ONLY
            )
            assert kill.api == "multiprocessing.Process.kill"
            assert kill.tier is WasmConcurrencyTier.COOPERATIVE
            assert kill.details["preemptive_cancel"] is False
            assert kill.details["signal_delivery"] is False
            kill_json = next(
                diagnostic
                for diagnostic in get_wasm_concurrency_diagnostics_json()
                if diagnostic["code"] == "process.kill_cooperative_only"
            )
            assert_diagnostic_json(
                kill_json,
                code="process.kill_cooperative_only",
                api="multiprocessing.Process.kill",
                tier="cooperative-only",
                details={
                    "preemptive_cancel": False,
                    "signal_delivery": False,
                    "synthetic_pid": True,
                },
                count=1,
            )
        finally:
            release.set()
            await asyncio.sleep(0)
            clear_wasm_concurrency_diagnostics()
            unpatch()


def test_wasm_process_diagnostics_aggregate_by_semantics() -> None:
    with mock_pyodide():
        clear_wasm_concurrency_diagnostics()
        unpatch = install_wasm_process_compatibility_shims()
        try:
            for _ in range(3):
                process = multiprocessing.Process(target=lambda: None)
                process.start()
                process.join(timeout=1)
                assert not process.is_alive()

            diagnostics = get_wasm_concurrency_diagnostics()
            process_start = next(
                diagnostic
                for diagnostic in diagnostics
                if diagnostic.code
                is WasmConcurrencyDiagnosticCode.PROCESS_STARTED_AS_ASYNCIO_TASK
            )
            assert process_start.count == 3
            assert process_start.details["synthetic_pid"] is True
        finally:
            clear_wasm_concurrency_diagnostics()
            unpatch()


def test_wasm_diagnostics_json_snapshot_shape_dedupe_and_clear() -> None:
    with mock_pyodide():
        clear_wasm_concurrency_diagnostics()
        unpatch = install_wasm_concurrency_shims()
        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=2
            ) as executor:
                assert executor.submit(lambda: "ok").result() == "ok"
            with concurrent.futures.ThreadPoolExecutor(max_workers=2):
                pass

            diagnostics = get_wasm_concurrency_diagnostics_json(clear=True)
            by_code = {
                diagnostic["code"]: diagnostic for diagnostic in diagnostics
            }
            assert_diagnostic_json(
                by_code["executor.worker_count_ignored"],
                code="executor.worker_count_ignored",
                api="concurrent.futures.ThreadPoolExecutor",
                tier="serialized",
                details={
                    "effective_workers": 1,
                    "requested_workers": 2,
                },
                count=2,
            )
            assert_diagnostic_json(
                by_code["executor.serialized"],
                code="executor.serialized",
                api="concurrent.futures.ThreadPoolExecutor",
                tier="serialized",
                details={
                    "effective_workers": 1,
                    "requested_workers": 2,
                },
                count=2,
            )
            json.dumps(diagnostics, allow_nan=False)
            assert get_wasm_concurrency_diagnostics() == []
            assert get_wasm_concurrency_diagnostics_json() == []
        finally:
            clear_wasm_concurrency_diagnostics()
            unpatch()


def test_wasm_diagnostics_buffer_keeps_bounded_overflow_event() -> None:
    with mock_pyodide():
        clear_wasm_concurrency_diagnostics()
        unpatch = install_wasm_concurrency_shims()
        try:
            for requested_workers in range(2, 302):
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=requested_workers
                ):
                    pass

            diagnostics = get_wasm_concurrency_diagnostics()
            dropped = next(
                diagnostic
                for diagnostic in diagnostics
                if diagnostic.code
                is WasmConcurrencyDiagnosticCode.DIAGNOSTIC_EVENTS_DROPPED
            )
            dropped_count = cast(int, dropped.details["dropped"])
            assert dropped_count > 0
            json_dropped = next(
                diagnostic
                for diagnostic in get_wasm_concurrency_diagnostics_json()
                if diagnostic["code"] == "diagnostics.events_dropped"
            )
            assert_diagnostic_json(
                json_dropped,
                code="diagnostics.events_dropped",
                api="marimo._runtime._wasm",
                tier="blocked",
                details={"dropped": dropped_count},
                count=1,
            )
            json.dumps(json_dropped, allow_nan=False)
        finally:
            clear_wasm_concurrency_diagnostics()
            unpatch()
