from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from callroo_printer.config import BluetoothConfig
from callroo_printer.printer import (
    TiMiniPrintDirectPrinter,
    TiMiniPrintDirectRuntime,
    resolve_bluetooth_adapter_names,
)


class PrinterTest(unittest.TestCase):
    def test_resolve_bluetooth_adapter_names_skips_missing_configured_adapters(self) -> None:
        config = _bluetooth_config(adapter_name="hci0,hci1")

        with patch(
            "callroo_printer.printer._list_available_bluetooth_adapters",
            return_value=("hci0",),
        ):
            adapter_names = resolve_bluetooth_adapter_names(config)

        self.assertEqual(adapter_names, ("hci0",))

    def test_timiniprint_direct_keeps_session_alive(self) -> None:
        session_class = FakeSession
        session_class.instances.clear()
        printer = TiMiniPrintDirectPrinter(_bluetooth_config())
        printer._runtime = _runtime(session_class)

        with patch(
            "callroo_printer.printer._list_available_bluetooth_adapters",
            return_value=("hci1",),
        ):
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
        load_calls = []
        printer = TiMiniPrintDirectPrinter(_bluetooth_config())
        printer._runtime = _runtime(session_class, load_calls=load_calls)

        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "ticket.png"
            Image.new("L", (384, 8), color=255).save(image_path)

            with patch(
                "callroo_printer.printer._list_available_bluetooth_adapters",
                return_value=("hci1",),
            ):
                printer.print_saved_image(
                    image_path=image_path,
                    image=Image.new("L", (384, 8), color=255),
                    threshold=160,
                    trailing_feed_lines=4,
                )

        self.assertEqual(
            load_calls,
            [
                {
                    "path": str(image_path),
                    "trim_side_margins": False,
                    "trim_top_bottom_margins": False,
                }
            ],
        )
        session = session_class.instances[-1]
        self.assertEqual(session.calls[0:3], ["connect", "enable_notifications", "query_status"])
        self.assertEqual(session.calls[3], "query_status")
        self.assertEqual(session.calls[4], ("print_page", b"page-1", 11, 93))
        self.assertEqual(session.calls[5], ("print_page", b"page-2", 12, 93))
        self.assertEqual(session.calls[6], "query_status")

    def test_timiniprint_direct_artifact_command_disables_auto_trim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "TiMini-Print"
            repo_path.mkdir()
            cli_path = repo_path / "timiniprint_command_line.py"
            cli_path.write_text("# test cli\n", encoding="utf-8")
            printer = TiMiniPrintDirectPrinter(
                _bluetooth_config(timiniprint_repo=repo_path, timiniprint_cli=cli_path)
            )

            artifacts = printer.build_artifacts(
                image_path=Path("/tmp/ticket.png"),
                image=Image.new("L", (384, 8), color=255),
                threshold=160,
                trailing_feed_lines=4,
            )

        payload = json.loads(artifacts[0].payload.decode("utf-8"))
        self.assertIn("--no-trim-side-margins", payload["command"])
        self.assertIn("--no-trim-top-bottom-margins", payload["command"])

    def test_timiniprint_direct_passes_configured_timeouts_to_session(self) -> None:
        session_class = FakeSession
        session_class.instances.clear()
        printer = TiMiniPrintDirectPrinter(_bluetooth_config())
        printer._runtime = _runtime(session_class)

        with patch(
            "callroo_printer.printer._list_available_bluetooth_adapters",
            return_value=("hci1",),
        ):
            printer.connect_if_needed()

        session = session_class.instances[-1]
        self.assertEqual(session.connect_timeout, 10.0)
        self.assertEqual(session.status_timeout, 3.0)
        self.assertEqual(session.adapter_name, "hci1")

    def test_timiniprint_direct_falls_back_to_next_adapter(self) -> None:
        session_class = FailFirstSession
        session_class.instances.clear()
        printer = TiMiniPrintDirectPrinter(_bluetooth_config(adapter_name="hci1,hci0"))
        printer._runtime = _runtime(session_class)

        with patch(
            "callroo_printer.printer._list_available_bluetooth_adapters",
            return_value=("hci1", "hci0"),
        ):
            printer.connect_if_needed()

        self.assertEqual(
            [instance.adapter_name for instance in session_class.instances],
            ["hci1", "hci0"],
        )

    def test_timiniprint_direct_rotates_start_adapter_after_full_failure(self) -> None:
        session_class = FailCycleSession
        session_class.instances.clear()
        printer = TiMiniPrintDirectPrinter(_bluetooth_config(adapter_name="hci1,hci0"))
        printer._runtime = _runtime(session_class)

        with patch(
            "callroo_printer.printer._list_available_bluetooth_adapters",
            return_value=("hci1", "hci0"),
        ):
            with self.assertRaises(RuntimeError):
                printer.connect_if_needed()

            printer.connect_if_needed()

        self.assertEqual(
            [instance.adapter_name for instance in session_class.instances],
            ["hci1", "hci0", "hci0"],
        )


class FakeSession:
    instances: list["FakeSession"] = []

    def __init__(
        self,
        *,
        address: str,
        reporter: object,
        connect_timeout: float = 15.0,
        status_timeout: float = 5.0,
        adapter_name: str | None = None,
    ) -> None:
        self.address = address
        self.reporter = reporter
        self.connect_timeout = connect_timeout
        self.status_timeout = status_timeout
        self.adapter_name = adapter_name
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


class FailFirstSession(FakeSession):
    def connect(self) -> None:
        self.calls.append("connect")
        if self.adapter_name == "hci1":
            raise RuntimeError("adapter failed")


class FailCycleSession(FakeSession):
    def connect(self) -> None:
        self.calls.append("connect")
        if len(self.instances) <= 2:
            raise RuntimeError("adapter failed")


def _runtime(
    session_class: type[FakeSession],
    *,
    load_calls: list[dict[str, object]] | None = None,
) -> TiMiniPrintDirectRuntime:
    page_markers = ["page-1", "page-2"]

    def load_pages(
        path: str,
        *,
        trim_side_margins: bool = True,
        trim_top_bottom_margins: bool = True,
    ):
        if load_calls is not None:
            load_calls.append(
                {
                    "path": path,
                    "trim_side_margins": trim_side_margins,
                    "trim_top_bottom_margins": trim_top_bottom_margins,
                }
            )
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
        printer_width=384,
    )


def _bluetooth_config(
    *,
    adapter_name: str = "hci1",
    timiniprint_repo: Path = Path("/tmp/TiMini-Print"),
    timiniprint_cli: Path = Path("/tmp/TiMini-Print/timiniprint_command_line.py"),
) -> BluetoothConfig:
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
        adapter_name=adapter_name,
        disabled_adapter_names=(),
        adapter_reset_after_failures=3,
        adapter_reset_cooldown_seconds=30.0,
        timiniprint_repo=timiniprint_repo,
        timiniprint_python=None,
        timiniprint_cli=timiniprint_cli,
        timiniprint_darkness=None,
        timiniprint_direct_y_scale=1.0,
    )
