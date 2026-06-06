# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


class RestartableTask:
    def __init__(
        self, coro: Callable[[], Any], *, restart_on_completion: bool = True
    ):
        self.coro = coro
        self.task: asyncio.Task[Any] | None = None
        self.stopped = False
        self.restart_on_completion = restart_on_completion
        self._restart_requested_task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        """Create a task that runs the coro."""
        while True:
            if self.stopped:
                break

            task = asyncio.create_task(self.coro())
            self.task = task
            try:
                await task
            except asyncio.CancelledError:
                if self.stopped:
                    break
                if self._restart_requested_task is task:
                    self._restart_requested_task = None
                    continue
                raise
            else:
                if self._restart_requested_task is task:
                    self._restart_requested_task = None
                    continue
                if not self.restart_on_completion:
                    break
            finally:
                if self._restart_requested_task is task:
                    self._restart_requested_task = None

    def stop(self) -> None:
        # Stop the task and set the stopped flag
        self.stopped = True
        assert self.task is not None
        self.task.cancel()

    def restart(self) -> None:
        # Cancel the current task, which will cause
        # the while loop to start a new task
        assert self.task is not None
        self._restart_requested_task = self.task
        self.task.cancel()
