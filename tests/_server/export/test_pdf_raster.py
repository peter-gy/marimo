from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from marimo._ast.app import App, InternalApp
from marimo._messaging.cell_output import CellChannel, CellOutput
from marimo._messaging.notification import (
    CellNotification,
    UpdateCellIdsNotification,
)
from marimo._server.export import _pdf_raster
from marimo._session.state.session_view import SessionView


def _cell_notification(
    *,
    cell_id: str,
    mimetype: str,
    data: object,
) -> CellNotification:
    return CellNotification(
        cell_id=cell_id,
        status="idle",
        output=CellOutput(
            channel=CellChannel.OUTPUT,
            mimetype=mimetype,
            data=data,
        ),
        timestamp=0,
    )


def test_collect_raster_targets_detects_html_vega_and_anywidget() -> None:
    session_view = SessionView()
    session_view.cell_notifications["1"] = _cell_notification(
        cell_id="1",
        mimetype="text/html",
        data="<div>hello</div>",
    )
    session_view.cell_notifications["2"] = _cell_notification(
        cell_id="2",
        mimetype="application/vnd.marimo+mimebundle",
        data={
            "application/vnd.vegalite.v5+json": {"mark": "point"},
            "text/plain": "vega",
        },
    )
    session_view.cell_notifications["3"] = _cell_notification(
        cell_id="3",
        mimetype="text/markdown",
        data=(
            "&lt;marimo-anywidget "
            'data-initial-value=\'{"model_id":"model-1"}\''
            "&gt;&lt;/marimo-anywidget&gt;"
        ),
    )
    session_view.cell_notifications["4"] = _cell_notification(
        cell_id="4",
        mimetype="text/plain",
        data="plain text",
    )
    session_view.cell_notifications["5"] = _cell_notification(
        cell_id="5",
        mimetype="text/html",
        data=("<marimo-stack>application/vnd.vegalite.v6+json</marimo-stack>"),
    )

    targets = _pdf_raster._collect_raster_targets(session_view)

    by_id = {target.cell_id: target for target in targets}
    assert set(by_id) == {"2", "3", "5"}
    assert by_id["2"].expects == ("vega",)
    assert by_id["2"].requires_live_capture is False
    assert by_id["3"].expects == ("anywidget",)
    assert by_id["3"].requires_live_capture is True
    assert by_id["5"].expects == ("vega",)
    assert by_id["5"].requires_live_capture is True


def test_collect_pdf_png_fallbacks_static_and_live_without_mutation() -> None:
    session_view = SessionView()
    original_anywidget = (
        '&lt;marimo-anywidget data-initial-value=\'{"model_id":"m-live"}\''
        "&gt;&lt;/marimo-anywidget&gt;"
    )
    session_view.cell_notifications["1"] = _cell_notification(
        cell_id="1",
        mimetype="text/plain",
        data=original_anywidget,
    )
    session_view.cell_notifications["2"] = _cell_notification(
        cell_id="2",
        mimetype="text/html",
        data="<marimo-slider></marimo-slider>",
    )
    session_view.cell_notifications["3"] = _cell_notification(
        cell_id="3",
        mimetype="application/vnd.marimo+mimebundle",
        data={
            "application/vnd.vegalite.v6+json": {"mark": "point"},
            "text/plain": "vega",
        },
    )
    session_view.cell_ids = UpdateCellIdsNotification(cell_ids=["3", "2", "1"])
    app = InternalApp(App())

    class _FakeServer:
        base_url = "http://127.0.0.1:1234"
        page_url = "http://127.0.0.1:1234/__marimo_pdf_raster__.html"

        def __enter__(self) -> _FakeServer:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def set_html(self, html: str) -> None:
            assert "<html" in html

    async def _capture_static(
        *,
        page_url: str,
        targets: list[_pdf_raster._RasterTarget],
        scale: float,
    ) -> dict[str, str]:
        assert page_url.endswith("__marimo_pdf_raster__.html")
        assert scale == 4.0
        assert [target.cell_id for target in targets] == ["3"]
        return {"3": "data:image/png;base64,c3RhdGlj"}

    async def _capture_live(
        *,
        filepath: str,
        targets: list[_pdf_raster._RasterTarget],
        scale: float,
        argv: list[str] | None,
    ) -> dict[str, str]:
        del filepath
        del scale
        del argv
        assert [target.cell_id for target in targets] == ["2", "1"]
        return {
            "2": "data:image/png;base64,bGl2ZTI=",
            "1": "data:image/png;base64,bGl2ZQ==",
        }

    def _fake_export_as_html(*args: Any, **kwargs: Any) -> tuple[str, str]:
        del args
        del kwargs
        return "<html></html>", "demo.html"

    async def _run() -> dict[str, str]:
        with (
            patch.object(
                _pdf_raster,
                "HtmlAssetServer",
                return_value=_FakeServer(),
            ),
            patch.object(
                _pdf_raster,
                "_capture_pngs_from_page",
                side_effect=_capture_static,
            ),
            patch.object(
                _pdf_raster,
                "_capture_pngs_from_live_page",
                side_effect=_capture_live,
            ),
            patch(
                "marimo._server.export.exporter.Exporter.export_as_html",
                side_effect=_fake_export_as_html,
            ),
        ):
            return await _pdf_raster.collect_pdf_png_fallbacks(
                app=app,
                session_view=session_view,
                filename="demo.py",
                filepath="demo.py",
                argv=["--arg", "value"],
                options=_pdf_raster.PDFRasterizationOptions(),
            )

    captures = asyncio.run(_run())
    assert captures == {
        "1": "data:image/png;base64,bGl2ZQ==",
        "2": "data:image/png;base64,bGl2ZTI=",
        "3": "data:image/png;base64,c3RhdGlj",
    }
    output = session_view.cell_notifications["1"].output
    assert output is not None
    assert output.mimetype == "text/plain"
    assert output.data == original_anywidget


def test_wait_for_target_ready_uses_settle_wait_for_dynamic_targets() -> None:
    class _TimeoutError(Exception):
        pass

    page = AsyncMock()
    locator = AsyncMock()
    target = _pdf_raster._RasterTarget(
        cell_id="dynamic-cell",
        expects=("vega",),
        requires_live_capture=False,
    )

    settle_wait = AsyncMock()
    with patch.object(
        _pdf_raster,
        "_wait_for_navigation_settled",
        settle_wait,
    ):
        ready = asyncio.run(
            _pdf_raster._wait_for_target_ready(
                page=page,
                target=target,
                locator=locator,
                timeout_error=_TimeoutError,
            )
        )

    assert ready is True
    settle_wait.assert_awaited_once()
