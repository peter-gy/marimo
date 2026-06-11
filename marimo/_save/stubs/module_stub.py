# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

__all__ = ["MissingModule", "ModuleStub"]


class MissingModule(ModuleType):
    """Placeholder for a cached module def that is unimportable here.

    Restoring a cache must not fail just because an environment lacks a
    module that no executed cell actually touches — e.g. a WASM reader
    restoring a notebook whose `torch` def feeds only host-side cells.
    The placeholder satisfies the namespace binding; any *use* raises
    the same `ModuleNotFoundError` the import would have.
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.__missing__ = True

    def __getattr__(self, attr: str) -> Any:
        if attr.startswith("__") and attr.endswith("__"):
            # Dunder probes — pickling machinery, repr, and
            # getattr-with-default version lookups — must fall back
            # rather than propagate an import error.
            raise AttributeError(attr)
        raise ModuleNotFoundError(
            f"No module named {self.__name__!r} in this environment "
            f"(cached def restored lazily; accessing "
            f"{self.__name__}.{attr} requires the real module)"
        )


class ModuleStub:
    """Stub for module objects, storing only the module name."""

    def __init__(self, module: Any, hash: str = "") -> None:  # noqa: A002
        self.name = module.__name__
        self.hash = hash

    def load(self) -> Any:
        """Reload the module by name.

        Falls back to a `MissingModule` placeholder when the module is
        absent, so restoration succeeds and the error surfaces only on
        actual use.
        """
        try:
            return importlib.import_module(self.name)
        except ModuleNotFoundError:
            return MissingModule(self.name)
