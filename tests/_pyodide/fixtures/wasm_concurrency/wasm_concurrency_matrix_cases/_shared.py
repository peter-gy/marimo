# Copyright 2026 Marimo. All rights reserved.
"""Shared state and helpers for the WASM concurrency matrix."""

# ruff: noqa: F401, I001

import asyncio
import concurrent.futures
import contextvars
import inspect
import json
import logging
import marimo as mo
import queue
import sys
import threading
from concurrent.futures import CancelledError
from js import (
    marimoWasmConcurrencyDelay,
    marimoWasmConcurrencyEnvironment,
    marimoWasmConcurrencyMessages,
)
from marimo._runtime._wasm._concurrency._diagnostics import (
    get_wasm_concurrency_diagnostics_json,
)
from pyodide.ffi import run_sync

matrix = []

COOPERATIVE_TIMEOUT = 0.25
TIMER_DELAY = 0.05


def _case_name(case_id):
    return case_id.replace(".", " ").replace("_", " ")


def record(case_id, tier):
    matrix.append(
        {
            "id": case_id,
            "name": _case_name(case_id),
            "tier": tier,
        }
    )


def assert_diagnostic(
    code,
    *,
    api,
    tier,
    tier_label,
    details: dict[str, object],
    message_contains=(),
):
    for diagnostic_data in get_wasm_concurrency_diagnostics_json():
        if diagnostic_data["code"] != code:
            continue
        assert diagnostic_data["api"] == api
        assert diagnostic_data["tier"] == tier
        assert diagnostic_data["tier_label"] == tier_label
        message = diagnostic_data["message"].lower()
        for expected_text in message_contains:
            assert expected_text.lower() in message, (
                code,
                expected_text,
                diagnostic_data["message"],
            )
        actual_details = diagnostic_data["details"]
        for key, expected in details.items():
            assert actual_details[key] == expected, (
                code,
                key,
                actual_details,
            )
        return
    raise AssertionError("missing diagnostic " + code)


def assert_run_sync_not_called(action):
    import pyodide.ffi as pyodide_ffi

    original_run_sync = pyodide_ffi.run_sync

    def forbidden_run_sync(_awaitable):
        raise AssertionError("immediate timeout called pyodide.ffi.run_sync")

    pyodide_ffi.run_sync = forbidden_run_sync
    try:
        return action()
    finally:
        pyodide_ffi.run_sync = original_run_sync


process_unpatch = None
CURRENT_GROUP = None
FAILURE_PATH = "/home/pyodide/wasm_concurrency_matrix_failure.json"
RESULT_PATH = "/home/pyodide/wasm_concurrency_matrix_result.json"


def write_matrix_failure(group, error):
    import traceback

    failure = {
        "group": group,
        "error_type": type(error).__name__,
        "error": str(error),
        "traceback": traceback.format_exc(),
        "partial_rows": matrix,
    }
    with open(FAILURE_PATH, "w", encoding="utf-8") as f:
        json.dump(failure, f, indent=2)


def assert_unique_matrix_rows():
    seen_case_ids = [row["id"] for row in matrix]
    duplicate_case_ids = sorted(
        {
            case_id
            for case_id in seen_case_ids
            if seen_case_ids.count(case_id) > 1
        }
    )
    assert not duplicate_case_ids, duplicate_case_ids


def write_matrix_result():
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(matrix, f, indent=2)
