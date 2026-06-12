from __future__ import annotations

import unittest
import json
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from callroo_printer.artifacts import ArtifactManager
from callroo_printer.config import LayoutConfig
from callroo_printer.service import (
    DashboardTriggerMonitor,
    FortunePrinterService,
    _scheduled_datetime,
    _web_search_prefetch_schedules,
)


class ServiceBluetoothResetTest(unittest.TestCase):
    def test_startup_failure_uses_configured_reset_threshold(self) -> None:
        service = _build_service(reset_after_failures=3, reset_cooldown_seconds=30.0)

        with patch("callroo_printer.service.time.monotonic", return_value=100.0):
            with patch("callroo_printer.service._reset_bluetooth_adapters") as reset:
                self.assertEqual(service._record_bluetooth_failure(startup=True), 1)
                self.assertEqual(service._record_bluetooth_failure(startup=True), 2)
                self.assertEqual(service._record_bluetooth_failure(startup=True), 3)

        reset.assert_called_once_with(("hci0",))
        self.assertEqual(service._consecutive_bluetooth_failures, 0)
        self.assertEqual(service._last_bluetooth_reset_at, 100.0)
        self.assertEqual(service.printer.close_calls, 1)
        self.assertEqual(service._played_events, ["printer_failed"])

    def test_record_bluetooth_failure_resets_adapter_after_threshold(self) -> None:
        service = _build_service(reset_after_failures=3, reset_cooldown_seconds=30.0)

        with patch("callroo_printer.service.time.monotonic", return_value=100.0):
            with patch("callroo_printer.service._reset_bluetooth_adapters") as reset:
                self.assertEqual(service._record_bluetooth_failure(), 1)
                self.assertEqual(service._record_bluetooth_failure(), 2)
                self.assertEqual(service._record_bluetooth_failure(), 3)

        reset.assert_called_once_with(("hci0",))
        self.assertEqual(service._consecutive_bluetooth_failures, 0)
        self.assertEqual(service._last_bluetooth_reset_at, 100.0)
        self.assertEqual(service.printer.close_calls, 1)
        self.assertEqual(service._played_events, ["printer_failed"])

    def test_record_bluetooth_failure_respects_reset_cooldown(self) -> None:
        service = _build_service(reset_after_failures=3, reset_cooldown_seconds=30.0)
        service._consecutive_bluetooth_failures = 2
        service._last_bluetooth_reset_at = 90.0

        with patch("callroo_printer.service.time.monotonic", return_value=100.0):
            with patch("callroo_printer.service._reset_bluetooth_adapters") as reset:
                self.assertEqual(service._record_bluetooth_failure(), 3)

        reset.assert_not_called()
        self.assertEqual(service._consecutive_bluetooth_failures, 3)
        self.assertEqual(service.printer.close_calls, 0)
        self.assertEqual(service._played_events, ["printer_failed"])

    def test_note_bluetooth_success_clears_failure_counter(self) -> None:
        service = _build_service(reset_after_failures=3, reset_cooldown_seconds=30.0)
        service._consecutive_bluetooth_failures = 5
        service._printer_failure_announced = True

        service._note_bluetooth_success()

        self.assertEqual(service._consecutive_bluetooth_failures, 0)
        self.assertEqual(service._played_events, ["printer_connected"])

    def test_note_bluetooth_success_plays_connected_on_initial_ready(self) -> None:
        service = _build_service(reset_after_failures=3, reset_cooldown_seconds=30.0)

        service._note_bluetooth_success()

        self.assertEqual(service._played_events, ["printer_connected"])

    def test_note_bluetooth_success_writes_dashboard_status(self) -> None:
        service = _build_service(reset_after_failures=3, reset_cooldown_seconds=30.0)
        with tempfile.TemporaryDirectory() as tmp:
            service.artifacts = ArtifactManager(Path(tmp))
            service.dry_run = False

            service._note_bluetooth_success()

            payload = json.loads(
                (Path(tmp) / "bluetooth-status.json").read_text(encoding="utf-8")
            )

        self.assertEqual(payload["status"], "connected")
        self.assertEqual(payload["backend"], "timiniprint_cli_direct")
        self.assertEqual(payload["mac_address"], "00:11:22:33:44:55")
        self.assertEqual(payload["failure_count"], 0)
        self.assertTrue(payload["last_success_at"])

    def test_select_llm_profile_uses_weighted_candidates(self) -> None:
        service = _build_service(reset_after_failures=3, reset_cooldown_seconds=30.0)

        with patch("callroo_printer.service.random.choices") as choices:
            choices.return_value = [service.config.llm.profiles[1]]
            selected = service._select_llm_profile()

        self.assertEqual(selected.name, "night")
        choices.assert_called_once()
        _, kwargs = choices.call_args
        self.assertEqual(kwargs["weights"], [1.0, 3.0])

    def test_select_launch_sound_player_uses_weighted_candidates(self) -> None:
        service = _build_service(reset_after_failures=3, reset_cooldown_seconds=30.0)
        first_player = SimpleNamespace(prime=lambda: True, clip_path="/tmp/launch-1.wav")
        second_player = SimpleNamespace(prime=lambda: True, clip_path="/tmp/launch-2.wav")
        service._launch_sound_players = (
            (SimpleNamespace(weight=1.0, file="/tmp/launch-1.wav"), first_player),
            (SimpleNamespace(weight=4.0, file="/tmp/launch-2.wav"), second_player),
        )

        with patch("callroo_printer.service.random.choices") as choices:
            choices.return_value = [second_player]
            selected = service._select_launch_sound_player()

        self.assertIs(selected, second_player)
        choices.assert_called_once()
        _, kwargs = choices.call_args
        self.assertEqual(kwargs["weights"], [1.0, 4.0])

    def test_select_launch_sound_player_skips_zero_weight_and_unprepared_players(self) -> None:
        service = _build_service(reset_after_failures=3, reset_cooldown_seconds=30.0)
        ignored_weight = SimpleNamespace(prime=lambda: True, clip_path="/tmp/launch-0.wav")
        ignored_unprepared = SimpleNamespace(prime=lambda: False, clip_path="/tmp/launch-x.wav")
        selected_player = SimpleNamespace(prime=lambda: True, clip_path="/tmp/launch-1.wav")
        service._launch_sound_players = (
            (SimpleNamespace(weight=0.0, file="/tmp/launch-0.wav"), ignored_weight),
            (SimpleNamespace(weight=2.0, file="/tmp/launch-x.wav"), ignored_unprepared),
            (SimpleNamespace(weight=1.0, file="/tmp/launch-1.wav"), selected_player),
        )

        with patch("callroo_printer.service.random.choices") as choices:
            selected = service._select_launch_sound_player()

        self.assertIs(selected, selected_player)
        choices.assert_not_called()

    def test_select_asset_for_profile_prefers_tagged_pool(self) -> None:
        service = _build_service(reset_after_failures=3, reset_cooldown_seconds=30.0)
        profile = service.config.llm.profiles[1]

        with patch("callroo_printer.service.random.choice", side_effect=lambda seq: list(seq)[0]):
            selected = service._select_asset_for_profile(
                profile,
                selected_tag="형광등",
            )

        self.assertEqual(selected, "/tmp/hallway-1.png")

    def test_resolve_selected_tag_falls_back_to_profile_tags(self) -> None:
        service = _build_service(reset_after_failures=3, reset_cooldown_seconds=30.0)
        profile = service.config.llm.profiles[0]

        with patch("callroo_printer.service.random.choice", return_value="자판기"):
            selected = service._resolve_selected_tag(profile, None)

        self.assertEqual(selected, "자판기")

    def test_dashboard_trigger_monitor_parses_trigger_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trigger_path = Path(tmp) / "dashboard-triggers.jsonl"
            trigger_path.write_text(
                json.dumps(
                    {
                        "request_id": "abc123",
                        "requested_at": "2026-06-05T10:00:00+09:00",
                        "note": "manual",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            event = DashboardTriggerMonitor._parse_line(trigger_path.read_text(encoding="utf-8"))

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.source, "dashboard")
        self.assertEqual(event.raw_input, "\n")
        self.assertEqual(event.details["request_id"], "abc123")
        self.assertEqual(event.details["note"], "manual")

    def test_dashboard_trigger_monitor_preserves_manual_print_payload(self) -> None:
        line = json.dumps(
            {
                "request_id": "manual123",
                "raw_input": "직접 출력",
                "manual_print": {
                    "text": "직접 출력",
                    "border_style": "thick",
                    "image_path": "/tmp/manual.png",
                },
            }
        )

        event = DashboardTriggerMonitor._parse_line(line + "\n")

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.raw_input, "직접 출력")
        self.assertEqual(event.details["manual_print"]["border_style"], "thick")

    def test_dashboard_trigger_monitor_ignores_malformed_trigger_lines(self) -> None:
        self.assertIsNone(DashboardTriggerMonitor._parse_line("{not json\n"))
        self.assertIsNone(DashboardTriggerMonitor._parse_line("[]\n"))
        self.assertIsNone(DashboardTriggerMonitor._parse_line("{}\n"))

    def test_dashboard_trigger_monitor_replays_only_unprocessed_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trigger_path = root / "dashboard-triggers.jsonl"
            trigger_path.write_text(
                "\n".join(
                    json.dumps(payload)
                    for payload in (
                        {
                            "request_id": "already-handled",
                            "requested_at": "2026-06-05T10:00:00+09:00",
                        },
                        {
                            "request_id": "pending",
                            "requested_at": "2026-06-05T10:01:00+09:00",
                            "note": "queued while down",
                        },
                        {
                            "request_id": "pending",
                            "requested_at": "2026-06-05T10:01:00+09:00",
                            "note": "duplicate",
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            job_dir = root / "jobs" / "20260605-100000-11111111"
            job_dir.mkdir(parents=True)
            (job_dir / "input.json").write_text(
                json.dumps(
                    {
                        "trigger_source": "dashboard",
                        "trigger_details": {"request_id": "already-handled"},
                    }
                ),
                encoding="utf-8",
            )

            monitor = DashboardTriggerMonitor(trigger_path)
            position = monitor._enqueue_existing_unprocessed_triggers()

            self.assertEqual(position, trigger_path.stat().st_size)
            event = monitor.next_trigger(timeout_seconds=0.0)
            self.assertIsNotNone(event)
            assert event is not None
            self.assertEqual(event.details["request_id"], "pending")
            self.assertEqual(event.details["note"], "queued while down")
            self.assertIsNone(monitor.next_trigger(timeout_seconds=0.0))

    def test_dashboard_trigger_monitor_waits_for_partial_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trigger_path = Path(tmp) / "dashboard-triggers.jsonl"
            trigger_path.write_text(
                json.dumps({"request_id": "partial"}),
                encoding="utf-8",
            )

            monitor = DashboardTriggerMonitor(trigger_path)

            self.assertEqual(monitor._enqueue_existing_unprocessed_triggers(), 0)
            self.assertIsNone(monitor.next_trigger(timeout_seconds=0.0))

    def test_manual_print_job_dry_run_builds_artifacts_without_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs_dir = root / "outputs"
            uploads_dir = outputs_dir / "manual-uploads" / "abc123"
            uploads_dir.mkdir(parents=True)
            image_path = uploads_dir / "manual.png"
            Image.new("RGB", (40, 30), color="black").save(image_path)

            service = FortunePrinterService.__new__(FortunePrinterService)
            service.config = SimpleNamespace(
                output=SimpleNamespace(outputs_dir=outputs_dir),
                layout=_layout_config(),
                trailing_feed_lines=3,
            )
            service.dry_run = True
            service.printer = _ArtifactPrinter()
            job = ArtifactManager(outputs_dir).create_job(
                triggered_at=datetime(2026, 6, 12, 10, 30, 0),
                raw_input="직접 출력",
                dry_run=True,
                trigger_source="dashboard",
                trigger_details={"request_id": "abc123"},
            )

            result, play_sound = service._handle_manual_print_job(
                job,
                {
                    "text": "직접 출력",
                    "image_path": str(image_path),
                    "border_style": "thick",
                    "text_align": "left",
                    "font_size": 30,
                    "label_width_px": 200,
                    "label_height_px": 100,
                    "content_margin_px": 6,
                    "text_vertical_align": "bottom",
                    "text_items": [
                        {
                            "id": "title",
                            "text": "위쪽",
                            "x": 0,
                            "y": 0,
                            "width": 120,
                            "height": 48,
                            "font_size": 28,
                            "text_align": "center",
                            "vertical_align": "top",
                        }
                    ],
                    "image_scale_percent": 160,
                    "image_crop": True,
                    "image_rotation_degrees": 90,
                },
                triggered_at=datetime(2026, 6, 12, 10, 30, 0),
            )

            self.assertFalse(play_sound)
            self.assertEqual(result["status"], "dry_run_completed")
            self.assertTrue(result["manual_print"])
            self.assertTrue((job.root / "composed-ticket.png").is_file())
            self.assertTrue((job.root / "print-job.bin").is_file())
            self.assertEqual((job.root / "fortune.txt").read_text(encoding="utf-8").strip(), "직접 출력")
            manual_payload = json.loads((job.root / "manual-print.json").read_text(encoding="utf-8"))
            self.assertEqual(manual_payload["border_style"], "thick")
            self.assertEqual(manual_payload["text_align"], "left")
            self.assertEqual(manual_payload["text_vertical_align"], "bottom")
            self.assertEqual(manual_payload["label_width_px"], 200)
            self.assertEqual(manual_payload["label_height_px"], 100)
            self.assertEqual(manual_payload["content_margin_px"], 6)
            self.assertEqual(result["manual_content_margin_px"], 6)
            self.assertEqual(manual_payload["text_items"][0]["text"], "위쪽")
            self.assertEqual(manual_payload["text_items"][0]["vertical_align"], "top")
            self.assertEqual(manual_payload["image_scale_percent"], 160)
            self.assertTrue(manual_payload["image_crop"])
            self.assertEqual(manual_payload["image_rotation_degrees"], 90)

    def test_manual_print_job_handles_multiple_positioned_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs_dir = root / "outputs"
            uploads_dir = outputs_dir / "manual-uploads" / "abc123"
            uploads_dir.mkdir(parents=True)
            first_path = uploads_dir / "first.png"
            second_path = uploads_dir / "second.png"
            Image.new("RGB", (40, 30), color="black").save(first_path)
            Image.new("RGB", (24, 24), color="black").save(second_path)

            service = FortunePrinterService.__new__(FortunePrinterService)
            service.config = SimpleNamespace(
                output=SimpleNamespace(outputs_dir=outputs_dir),
                layout=_layout_config(),
                trailing_feed_lines=3,
            )
            service.dry_run = True
            service.printer = _ArtifactPrinter()
            job = ArtifactManager(outputs_dir).create_job(
                triggered_at=datetime(2026, 6, 12, 10, 30, 0),
                raw_input="직접 출력",
                dry_run=True,
                trigger_source="dashboard",
                trigger_details={"request_id": "abc123"},
            )

            result, play_sound = service._handle_manual_print_job(
                job,
                {
                    "text": "",
                    "label_width_px": 220,
                    "label_height_px": 120,
                    "images": [
                        {
                            "id": "first",
                            "filename": "first.png",
                            "path": str(first_path),
                            "x": 8,
                            "y": 10,
                            "width": 80,
                            "height": 60,
                            "rotation_degrees": 30,
                            "crop": True,
                        },
                        {
                            "id": "second",
                            "filename": "second.png",
                            "path": str(second_path),
                            "x": 100,
                            "y": 20,
                            "width": 40,
                            "height": 40,
                        },
                    ],
                },
                triggered_at=datetime(2026, 6, 12, 10, 30, 0),
            )

            self.assertFalse(play_sound)
            self.assertEqual(result["manual_image_count"], 2)
            self.assertTrue((job.root / "manual-upload-01.png").is_file())
            self.assertTrue((job.root / "manual-upload-02.png").is_file())
            self.assertTrue((job.root / "composed-ticket.png").is_file())
            manual_payload = json.loads((job.root / "manual-print.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manual_payload["images"]), 2)
            self.assertEqual(manual_payload["images"][0]["x"], 8)
            self.assertTrue(manual_payload["images"][0]["crop"])

    def test_web_search_prefetch_schedules_include_enabled_profiles(self) -> None:
        config = SimpleNamespace(
            llm=SimpleNamespace(
                profiles=(
                    SimpleNamespace(
                        name="star",
                        web_search=SimpleNamespace(
                            enabled=True,
                            daily_prefetch_enabled=True,
                            daily_prefetch_time="09:00",
                            signs=("양", "황소"),
                        ),
                    ),
                    SimpleNamespace(
                        name="plain",
                        web_search=None,
                    ),
                    SimpleNamespace(
                        name="disabled",
                        web_search=SimpleNamespace(
                            enabled=True,
                            daily_prefetch_enabled=False,
                            daily_prefetch_time="09:00",
                            signs=("쥐",),
                        ),
                    ),
                )
            )
        )

        self.assertEqual(
            _web_search_prefetch_schedules(config),
            (("star", "09:00"),),
        )

    def test_prefetch_profile_web_search_calls_client(self) -> None:
        service = _build_service(reset_after_failures=3, reset_cooldown_seconds=30.0)
        client = SimpleNamespace(calls=[])

        def prefetch(*, date_key: str):
            client.calls.append(date_key)
            return {"count": 12}

        client.prefetch_web_search = prefetch
        service.clients = {"star": client}

        service._prefetch_profile_web_search("star", date_key="2026-06-11")

        self.assertEqual(client.calls, ["2026-06-11"])

    def test_scheduled_datetime_uses_local_day_time(self) -> None:
        from datetime import datetime

        scheduled = _scheduled_datetime(datetime(2026, 6, 11, 13, 30), "09:00")

        self.assertEqual(scheduled, datetime(2026, 6, 11, 9, 0))


class _FakePrinter:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _ArtifactPrinter:
    def build_artifacts(
        self,
        *,
        image_path: Path,
        image,
        threshold: int,
        trailing_feed_lines: int,
    ):
        del image_path, image, threshold, trailing_feed_lines
        return [SimpleNamespace(filename="print-job.bin", payload=b"manual-job")]

    def print_saved_image(self, **kwargs) -> None:
        raise AssertionError("dry-run manual print should not send to printer")


def _layout_config() -> LayoutConfig:
    return LayoutConfig(
        paper_width_px=384,
        side_margin_px=20,
        section_gap_px=16,
        image_max_height_px=120,
        title_icon_file=None,
        title_font_size=28,
        body_font_size=24,
        timestamp_font_size=18,
        font_path=None,
        threshold=160,
        max_fortune_chars=100,
    )


def _build_service(
    *,
    reset_after_failures: int,
    reset_cooldown_seconds: float,
) -> FortunePrinterService:
    service = FortunePrinterService.__new__(FortunePrinterService)
    service.config = SimpleNamespace(
        bluetooth=SimpleNamespace(
            backend="timiniprint_cli_direct",
            mac_address="00:11:22:33:44:55",
            adapter_name="hci0",
            disabled_adapter_names=(),
            adapter_reset_after_failures=reset_after_failures,
            adapter_reset_cooldown_seconds=reset_cooldown_seconds,
        ),
        llm=SimpleNamespace(
            profiles=(
                SimpleNamespace(
                    name="day",
                    weight=1.0,
                    tags={
                        "자판기": ("/tmp/vending-1.png",),
                        "주전자": ("/tmp/kettle-1.png",),
                    },
                ),
                SimpleNamespace(
                    name="night",
                    weight=3.0,
                    tags={"형광등": ("/tmp/hallway-1.png", "/tmp/hallway-2.png")},
                ),
            )
        ),
        assets_dir="/tmp/assets",
    )
    service.printer = _FakePrinter()
    service._launch_sound_players = ()
    service._event_audio_players = {}
    service._played_events = []
    service._play_event_sound = lambda event_name, delay_seconds=0.0: service._played_events.append(event_name)
    service._consecutive_bluetooth_failures = 0
    service._last_bluetooth_reset_at = 0.0
    service._printer_failure_announced = False
    service._printer_connected_once = False
    return service


if __name__ == "__main__":
    unittest.main()
