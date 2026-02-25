# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType


class _HtmlAssetRequestHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        route = self.path.split("?", 1)[0]
        dynamic_route = getattr(self.server, "dynamic_route", "")
        if route == dynamic_route:
            html = getattr(self.server, "dynamic_html", "")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
            return
        return super().do_GET()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        del format
        del args


class _HtmlAssetHTTPServer(ThreadingHTTPServer):
    dynamic_route: str
    dynamic_html: str


class HtmlAssetServer(AbstractContextManager["HtmlAssetServer"]):
    def __init__(self, *, directory: Path, route: str) -> None:
        self._directory = directory
        self._route = route if route.startswith("/") else f"/{route}"
        self._server: _HtmlAssetHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("HTML asset server is not running")
        host, port = self._server.server_address[:2]
        if isinstance(host, bytes):
            host = host.decode("utf-8")
        return f"http://{host}:{port}"

    @property
    def page_url(self) -> str:
        return f"{self.base_url}{self._route}"

    def set_html(self, html: str) -> None:
        if self._server is None:
            raise RuntimeError("HTML asset server is not running")
        self._server.dynamic_html = html

    def __enter__(self) -> Self:
        if not self._directory.is_dir():
            raise RuntimeError(f"Static assets not found at {self._directory}")

        handler = partial(
            _HtmlAssetRequestHandler,
            directory=str(self._directory),
        )
        self._server = _HtmlAssetHTTPServer(("127.0.0.1", 0), handler)
        self._server.dynamic_route = self._route
        self._server.dynamic_html = ""

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type
        del exc
        del tb

        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
