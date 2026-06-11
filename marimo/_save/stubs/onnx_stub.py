# Copyright 2026 Marimo. All rights reserved.
"""OnnxRuntime — an environment-adaptive ONNX inference wrapper, and the
CustomStub that carries it through the cache.

`OnnxRuntime` is the "library that outputs a runtime": construct it from
serialized ONNX model bytes on any host, and `run()` lazily binds the
right backend — native `onnxruntime` where available, `onnxruntime-web`
(WASM, over the pyodide JS FFI) in the browser. Dual-backend pattern
after rambip/sketch-vectorization.

`OnnxRuntimeStub` serializes the wrapper as exactly its model bytes, so
a cached def whose value is an `OnnxRuntime` rehydrates as a *working*
inference session in a static WASM export — a torch- or sklearn-trained
model runs in a browser that can never import torch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from marimo._save.stubs.stubs import CustomStub

if TYPE_CHECKING:
    import numpy as np

__all__ = ["OnnxRuntime", "OnnxRuntimeStub"]


class OnnxRuntime:
    """Lazy, environment-adaptive ONNX inference session.

    Holds only the serialized model; the live session is constructed on
    first `run()` per environment, and is never serialized (pickling an
    instance round-trips just the bytes).
    """

    def __init__(self, onnx_bytes: bytes) -> None:
        self.onnx_bytes = onnx_bytes
        self._session: Any = None
        self._ort: Any = None
        self._kind: str | None = None

    def __getstate__(self) -> dict[str, Any]:
        return {"onnx_bytes": self.onnx_bytes}

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.onnx_bytes = state["onnx_bytes"]
        self._session = None
        self._ort = None
        self._kind = None

    @staticmethod
    def _in_browser() -> bool:
        from importlib.util import find_spec

        return find_spec("js") is not None and find_spec("pyodide") is not None

    async def _ensure(self) -> None:
        if self._session is not None:
            return
        if self._in_browser():
            import js  # type: ignore[import-not-found]
            from pyodide.ffi import to_js  # type: ignore[import-not-found]

            ort = await js.eval(
                "import('https://cdn.jsdelivr.net/npm/onnxruntime-web"
                "/dist/ort.all.bundle.min.mjs')"
            )
            ort.env.wasm.wasmPaths = (
                "https://cdn.jsdelivr.net/npm/onnxruntime-web/dist/"
            )
            opts = js.Object.new()
            opts.executionProviders = to_js(["wasm"])
            self._ort = ort
            self._session = await ort.InferenceSession.create(
                to_js(self.onnx_bytes), opts
            )
            self._kind = "web"
        else:
            try:
                import onnxruntime as ort
            except ImportError as e:
                raise RuntimeError(
                    "OnnxRuntime.run() outside the browser requires the "
                    "`onnxruntime` package."
                ) from e

            self._session = ort.InferenceSession(self.onnx_bytes)
            self._kind = "native"

    async def run(
        self,
        inputs: dict[str, np.ndarray],
        output_names: list[str] | None = None,
    ) -> list[np.ndarray]:
        """Run inference; returns outputs in `output_names` order."""
        import numpy as np

        await self._ensure()
        if self._kind == "native":
            return self._session.run(output_names, inputs)

        from pyodide.ffi import to_js  # type: ignore[import-not-found]

        feeds = {}
        for name, arr in inputs.items():
            arr = np.ascontiguousarray(arr, dtype=np.float32)
            feeds[name] = self._ort.Tensor.new(
                "float32", to_js(arr.ravel()), to_js(list(arr.shape))
            )
        results = await self._session.run(to_js(feeds))
        names = output_names or list(results.object_keys())
        out = []
        for name in names:
            tensor = getattr(results, name)
            out.append(
                np.asarray(tensor.data.to_py(), dtype=np.float32).reshape(
                    list(tensor.dims.to_py())
                )
            )
        return out


class OnnxRuntimeStub(CustomStub):
    """Serialize an `OnnxRuntime` as exactly its model bytes."""

    __slots__ = ("onnx_bytes",)

    def __init__(self, runtime: Any) -> None:
        self.onnx_bytes = runtime.onnx_bytes

    def load(self, glbls: dict[str, Any]) -> Any:
        del glbls
        return OnnxRuntime(self.onnx_bytes)

    @staticmethod
    def get_type() -> type:
        return OnnxRuntime

    def to_bytes(self) -> bytes:
        return self.onnx_bytes
