from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from callroo_printer.service import DashboardTriggerMonitor, FortunePrinterService


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


class _FakePrinter:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def _build_service(
    *,
    reset_after_failures: int,
    reset_cooldown_seconds: float,
) -> FortunePrinterService:
    service = FortunePrinterService.__new__(FortunePrinterService)
    service.config = SimpleNamespace(
        bluetooth=SimpleNamespace(
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
