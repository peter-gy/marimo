# Copyright 2026 Marimo. All rights reserved.
"""WASM-only monkey-patch helpers.

`WasmPatchSet` wraps a target call. If the original raises a configured
exception, the fallback runs. `unpatch_all()` returns one handle that restores
all active patches. Outside Pyodide, patch registration is inert.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from marimo._loggers import marimo_logger
from marimo._utils.platform import is_pyodide

LOGGER = marimo_logger()

Unpatch = Callable[[], None]
Fallback = Callable[..., Any]
WrapperFactory = Callable[[Any], Any]
_MISSING = object()


class WasmPatchSet:
    """Collect WASM-only patches behind one restore handle.

    `patch` replaces `owner.attr` with a wrapper. The wrapper calls the
    original, then runs `fallback(original, *args, **kwargs)` only when the
    original raises a configured `catch` exception. If the fallback also
    raises, callers receive the original exception with the fallback chained as
    the cause.
    """

    def __init__(self) -> None:
        self._unpatches: list[Unpatch] = []
        self._active = is_pyodide()

    def patch(
        self,
        owner: Any,
        attr: str,
        fallback: Fallback,
        *,
        catch: tuple[type[BaseException], ...] = (NameError, Exception),
    ) -> None:
        """Register a patch on `owner.attr`.

        No-op outside pyodide or if `attr` is missing (e.g. renamed across
        polars versions).
        """

        def wrapper_factory(
            original: Callable[..., Any],
        ) -> Callable[..., Any]:
            @functools.wraps(original)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return original(*args, **kwargs)
                except catch as original_exc:
                    original_tb = original_exc.__traceback__
                    try:
                        return fallback(original, *args, **kwargs)
                    except ModuleNotFoundError:
                        # Let missing-dependency errors bubble up so marimo can
                        # prompt the user to install the package.
                        raise
                    except Exception as fallback_exc:
                        raise original_exc.with_traceback(
                            original_tb
                        ) from fallback_exc

            return wrapper

        self.replace(owner, attr, wrapper_factory)

    def replace(
        self,
        owner: Any,
        attr: str,
        wrapper_factory: WrapperFactory,
        *,
        before_restore: Callable[[], None] | None = None,
    ) -> None:
        """Replace `owner.attr` with a WASM-only wrapper.

        Unlike `patch`, this does not call the original first. Use this for
        APIs where an original call can have side effects before failing.
        """
        if not self._active:
            return

        original = getattr(owner, attr, _MISSING)
        if original is _MISSING:
            return

        wrapper = wrapper_factory(original)
        setattr(owner, attr, wrapper)

        def _unpatch() -> None:
            # Only restore if we're still the active wrapper.
            if getattr(owner, attr, None) is wrapper:
                if before_restore is not None:
                    before_restore()
                setattr(owner, attr, original)

        self._unpatches.append(_unpatch)

    def add_cleanup(self, cleanup: Unpatch) -> None:
        if self._active:
            self._unpatches.append(cleanup)

    def unpatch_all(self) -> Unpatch:
        """Return a callable that restores all originals (idempotent)."""
        unpatches = self._unpatches
        self._unpatches = []

        def _run() -> None:
            for u in reversed(unpatches):
                try:
                    u()
                except Exception as e:
                    LOGGER.warning("Failed to unpatch: %s", e)

        return _run
