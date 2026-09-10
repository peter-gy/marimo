# Copyright 2026 Marimo. All rights reserved.
from unittest.mock import patch

import pytest

from marimo._runtime.commands import ExecuteCellCommand
from tests._runtime._helpers.session import mocked_kernel_session


@pytest.mark.parametrize("expression", ["", "mo.md('hello')"])
async def test_lens_mount_and_rerun(expression):
    lens = pytest.importorskip("marimo_lens")
    with mocked_kernel_session() as session:
        kernel = session.kernel
        with patch.object(lens, "Lens", wraps=lens.Lens) as create:
            command = ExecuteCellCommand(
                cell_id="lens", code=f"import marimo as mo\n{expression}"
            )
            await kernel.run([command])
            assert not kernel.errors
            assert create.call_count == 1
            outputs = [
                op.output.data
                for op in session.streams.stream.cell_notifications
                if op.cell_id == "lens" and op.output is not None
            ]
            if expression:
                assert "hello" in outputs[-1]
            assert "marimo-anywidget" in outputs[-1]
            await kernel.run([command])
            assert not kernel.errors
            assert create.call_count == 2
            await kernel.run(
                [ExecuteCellCommand(cell_id="other", code="mo.md('other')")]
            )
            assert create.call_count == 2


async def test_lens_absent():
    with mocked_kernel_session() as session:
        with patch("importlib.util.find_spec", return_value=None):
            await session.kernel.run(
                [
                    ExecuteCellCommand(
                        cell_id="lens", code="import marimo as mo"
                    )
                ]
            )
        assert not session.kernel.errors
