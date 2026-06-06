# Copyright 2026 Marimo. All rights reserved.
"""Thread-local storage for shim-managed logical threads."""

from __future__ import annotations

import weakref
from typing import Any

from marimo._runtime._wasm._concurrency._state import current_ident

_ASYNC_LOCALS: weakref.WeakSet[AsyncLocal] = weakref.WeakSet()


def clear_thread_local_state(ident: int) -> None:
    """Remove per-ident storage after a logical thread finishes."""
    for local in list(_ASYNC_LOCALS):
        storage: dict[int, dict[str, Any]] = object.__getattribute__(
            local, "_storage"
        )
        storage.pop(ident, None)
        initialized: set[int] = object.__getattribute__(
            local, "_initialized_idents"
        )
        initializing: set[int] = object.__getattribute__(
            local, "_initializing_idents"
        )
        initialized.discard(ident)
        initializing.discard(ident)


class AsyncLocal:
    """`threading.local` backed by the shim's current-thread context."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        if cls is AsyncLocal and (args or kwargs):
            raise TypeError("Initialization arguments are not supported")
        self = super().__new__(cls)
        object.__setattr__(self, "_storage", {})
        object.__setattr__(self, "_local_args", args)
        object.__setattr__(self, "_local_kwargs", kwargs)
        object.__setattr__(self, "_initialized_idents", {current_ident()})
        object.__setattr__(self, "_initializing_idents", set())
        _ASYNC_LOCALS.add(self)
        return self

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if args or kwargs:
            raise TypeError("Initialization arguments are not supported")

    def _namespace(self) -> dict[str, Any]:
        storage: dict[int, dict[str, Any]] = object.__getattribute__(
            self, "_storage"
        )
        ident = current_ident()
        namespace = storage.setdefault(ident, {})
        initialized: set[int] = object.__getattribute__(
            self, "_initialized_idents"
        )
        initializing: set[int] = object.__getattribute__(
            self, "_initializing_idents"
        )
        if ident in initialized or ident in initializing:
            return namespace

        initializing.add(ident)
        try:
            type(self).__init__(
                self,
                *object.__getattribute__(self, "_local_args"),
                **object.__getattribute__(self, "_local_kwargs"),
            )
        finally:
            initializing.remove(ident)
        initialized.add(ident)
        return namespace

    def __getattribute__(self, name: str) -> Any:
        if name in {
            "_storage",
            "_local_args",
            "_local_kwargs",
            "_initialized_idents",
            "_initializing_idents",
            "_namespace",
            "_class_attribute",
            "_data_descriptor",
        }:
            return object.__getattribute__(self, name)
        if name == "__dict__":
            return self._namespace()

        class_attribute = self._class_attribute(name)
        if hasattr(class_attribute, "__get__") and (
            hasattr(class_attribute, "__set__")
            or hasattr(class_attribute, "__delete__")
        ):
            return object.__getattribute__(self, name)

        namespace = self._namespace()
        if name in namespace:
            return namespace[name]

        try:
            return object.__getattribute__(self, name)
        except AttributeError:
            raise AttributeError(name) from None

    def _class_attribute(self, name: str) -> Any:
        for cls in type(self).__mro__:
            namespace = vars(cls)
            if name in namespace:
                return namespace[name]
        return None

    def _data_descriptor(self, name: str) -> Any:
        class_attribute = self._class_attribute(name)
        if hasattr(class_attribute, "__get__") and (
            hasattr(class_attribute, "__set__")
            or hasattr(class_attribute, "__delete__")
        ):
            return class_attribute
        return None

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_storage":
            object.__setattr__(self, name, value)
            return
        if self._data_descriptor(name) is not None:
            object.__setattr__(self, name, value)
            return
        self._namespace()[name] = value

    def __delattr__(self, name: str) -> None:
        if self._data_descriptor(name) is not None:
            object.__delattr__(self, name)
            return
        namespace = self._namespace()
        try:
            del namespace[name]
        except KeyError as exc:
            raise AttributeError(name) from exc
