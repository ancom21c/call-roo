from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from callroo_printer.config import BluetoothConfig
from callroo_printer.printer import TiMiniPrintDirectPrinter, TiMiniPrintDirectRuntime


class PrinterTest(unittest.TestCase):
    def test_timiniprint_direct_keeps_session_alive(self) -> None:
        session_class = FakeSession
        session_class.instances.clear()
        printer = TiMiniPrintDirectPrinter(_bluetooth_config())
        printer._runtime = _runtime(session_class)

        printer.connect_if_needed()
        printer.keep_alive()
        printer.close()

        session = session_class.instances[-1]
        self.assertEqual(
            session.calls,
            [
                "connect",
                "enable_notifications",
                "query_status",
                "query_status",
                "close",
            ],
        )

    def test_timiniprint_direct_prints_with_persistent_session(self) -> None:
        session_class = FakeSession
        session_class.instances.clear()
        printer = TiMiniPrintDirectPrinter(_bluetooth_config())
        printer._runtime = _runtime(session_class)

        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "ticket.png"
            Image.new("L", (8, 8), color=255).save(image_path)

            printer.print_saved_image(
                image_path=image_path,
                image=Image.new("L", (8, 8), color=255),
                threshold=160,
                trailing_feed_lines=4,
            )

        session = session_class.instances[-1]
        self.assertEqual(session.calls[0:3], ["connect", "enable_notifications", "query_status"])
        self.assertEqual(session.calls[3], "query_status")
        self.assertEqual(session.calls[4], ("print_page", b"page-1", 11, 93))
        self.assertEqual(session.calls[5], ("print_page", b"page-2", 12, 93))
        self.assertEqual(session.calls[6], "query_status")

    def test_timiniprint_direct_passes_configured_timeouts_to_session(self) -> None:
        session_class = FakeSession
        session_class.instances.clear()
        printer = TiMiniPrintDirectPrinter(_bluetooth_config())
        printer._runtime = _runtime(session_class)

        printer.connect_if_needed()

        session = session_class.instances[-1]
        self.assertEqual(session.connect_timeout, 10.0)
        self.assertEqual(session.status_timeout, 3.0)


class FakeSession:
    instances: list["FakeSession"] = []

    def __init__(
        self,
        *,
        address: str,
        reporter: object,
        connect_timeout: float = 15.0,
        status_timeout: float = 5.0,
    ) -> None:
        self.address = address
        self.reporter = reporter
        self.connect_timeout = connect_timeout
        self.status_timeout = status_timeout
        self.calls: list[object] = []
        self.instances.append(self)

    def connect(self) -> None:
        self.calls.append("connect")

    def enable_notifications(self) -> None:
        self.calls.append("enable_notifications")

    def query_status(self) -> bytes:
        self.calls.append("query_status")
        return b"status"

    def print_page(self, buffer: bytes, height: int, intensity: int) -> None:
        self.calls.append(("print_page", buffer, height, intensity))

    def close(self) -> None:
        self.calls.append("close")


class _Reporting:
    DUMMY_REPORTER = object()


def _runtime(session_class: type[FakeSession]) -> TiMiniPrintDirectRuntime:
    page_markers = ["page-1", "page-2"]

    def load_pages(path: str):
        return page_markers, Path(path)

    def scale_pages_vertically(pages, y_scale: float):
        assert y_scale == 1.0
        return list(pages)

    def build_page_buffer(page: str):
        heights = {"page-1": 11, "page-2": 12}
        return page.encode("utf-8"), heights[page]

    def darkness_to_intensity(darkness):
        return 93 if darkness is None else darkness

    return TiMiniPrintDirectRuntime(
        reporting=_Reporting,
        session_cls=session_class,
        load_pages=load_pages,
        scale_pages_vertically=scale_pages_vertically,
        build_page_buffer=build_page_buffer,
        darkness_to_intensity=darkness_to_intensity,
    )


def _bluetooth_config() -> BluetoothConfig:
    return BluetoothConfig(
        backend="timiniprint_cli_direct",
        mac_address="48:0F:57:2B:CB:24",
        channel=None,
        channel_candidates=(1, 6),
        auto_detect_channel=True,
        connect_timeout_seconds=10.0,
        reconnect_delay_seconds=5.0,
        keepalive_interval_seconds=20.0,
        keepalive_timeout_seconds=3.0,
        keepalive_hex="1f1111",
        keepalive_response_bytes=3,
        adapter_name="hci0",
        adapter_reset_after_failures=3,
        adapter_reset_cooldown_seconds=30.0,
        timiniprint_repo=Path("/tmp/TiMini-Print"),
        timiniprint_python=None,
        timiniprint_cli=Path("/tmp/TiMini-Print/timiniprint_command_line.py"),
        timiniprint_darkness=None,
        timiniprint_direct_y_scale=1.0,
    )
