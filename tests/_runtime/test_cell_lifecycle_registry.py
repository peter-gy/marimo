# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from marimo._runtime.cell_lifecycle_item import CellLifecycleItem
from marimo._runtime.cell_lifecycle_registry import (
    CellLifecycleRegistry,
    LifecycleDisposeError,
)
from marimo._runtime.context.types import (
    initialize_context,
    teardown_context,
)
from marimo._types.ids import CellId_t


class RecordingLifecycleItem(CellLifecycleItem):
    def __init__(
        self,
        name: str,
        calls: list[str],
        *,
        remove_on_dispose: bool = True,
        error: BaseException | None = None,
    ) -> None:
        self.name = name
        self.calls = calls
        self.remove_on_dispose = remove_on_dispose
        self.error = error

    def create(self, context: Any) -> None:
        del context

    def dispose(self, context: Any, deletion: bool) -> bool:
        del context
        self.calls.append(f"{self.name}:{deletion}")
        if self.error is not None:
            raise self.error
        return self.remove_on_dispose


def test_dispose_all_continues_when_lifecycle_item_fails() -> None:
    teardown_context()
    initialize_context(MagicMock())
    registry = CellLifecycleRegistry()
    calls: list[str] = []
    failed = RecordingLifecycleItem(
        "failed", calls, error=RuntimeError("dispose failed")
    )
    retried = RecordingLifecycleItem("retried", calls, remove_on_dispose=False)
    removed = RecordingLifecycleItem("removed", calls)
    other_cell = RecordingLifecycleItem("other-cell", calls)

    first_cell = CellId_t("first")
    second_cell = CellId_t("second")
    registry.inject(first_cell, failed)
    registry.inject(first_cell, retried)
    registry.inject(first_cell, removed)
    registry.inject(second_cell, other_cell)

    try:
        with pytest.raises(
            LifecycleDisposeError, match="Failed to dispose"
        ) as exc_info:
            registry.dispose_all(deletion=True)
    finally:
        teardown_context()

    assert [type(error) for error in exc_info.value.errors] == [RuntimeError]
    assert str(exc_info.value.errors[0]) == "dispose failed"
    assert set(calls) == {
        "failed:True",
        "retried:True",
        "removed:True",
        "other-cell:True",
    }
    assert registry.registry == {first_cell: {failed, retried}}
