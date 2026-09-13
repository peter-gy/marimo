# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

from typing import TYPE_CHECKING

from marimo import _loggers
from marimo._runtime import output
from marimo._runtime.context.utils import running_in_notebook

if TYPE_CHECKING:
    from marimo._ast.cell import CellImpl
    from marimo._runtime.runner.hook_context import PostExecutionHookContext
    from marimo._runtime.runner.result import RunResult

LOGGER = _loggers.marimo_logger()


# Imports are cached before notebook execution; mount in the importing cell.
def mount_lens(
    cell: CellImpl, ctx: PostExecutionHookContext, result: RunResult
) -> None:
    from importlib.util import find_spec

    del ctx

    if (
        not result.success()
        or not running_in_notebook()
        or cell.namespace_to_variable("marimo") is None
    ):
        return

    try:
        if find_spec("marimo_lens") is None:
            return

        from marimo_lens import Lens  # type: ignore[import-not-found]

        lens = Lens()
        cell.set_output((cell.output, lens))
        if result.output is not None:
            output.append(result.output)
        output.append(lens)
    except Exception:
        LOGGER.warning(
            "Failed to automatically mount marimo-lens", exc_info=True
        )
