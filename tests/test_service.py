from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from callroo_printer.service import FortunePrinterService


class ServiceBluetoothResetTest(unittest.TestCase):
    def test_record_bluetooth_failure_resets_adapter_after_threshold(self) -> None:
        service = _build_service(reset_after_failures=3, reset_cooldown_seconds=30.0)

        with patch("callroo_printer.service.time.monotonic", return_value=100.0):
            with patch("callroo_printer.service._reset_bluetooth_adapter") as reset:
                self.assertEqual(service._record_bluetooth_failure(), 1)
                self.assertEqual(service._record_bluetooth_failure(), 2)
                self.assertEqual(service._record_bluetooth_failure(), 3)

        reset.assert_called_once_with("hci0")
        self.assertEqual(service._consecutive_bluetooth_failures, 0)
        self.assertEqual(service._last_bluetooth_reset_at, 100.0)
        self.assertEqual(service.printer.close_calls, 1)

    def test_record_bluetooth_failure_respects_reset_cooldown(self) -> None:
        service = _build_service(reset_after_failures=3, reset_cooldown_seconds=30.0)
        service._consecutive_bluetooth_failures = 2
        service._last_bluetooth_reset_at = 90.0

        with patch("callroo_printer.service.time.monotonic", return_value=100.0):
            with patch("callroo_printer.service._reset_bluetooth_adapter") as reset:
                self.assertEqual(service._record_bluetooth_failure(), 3)

        reset.assert_not_called()
        self.assertEqual(service._consecutive_bluetooth_failures, 3)
        self.assertEqual(service.printer.close_calls, 0)

    def test_note_bluetooth_success_clears_failure_counter(self) -> None:
        service = _build_service(reset_after_failures=3, reset_cooldown_seconds=30.0)
        service._consecutive_bluetooth_failures = 5

        service._note_bluetooth_success()

        self.assertEqual(service._consecutive_bluetooth_failures, 0)


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
            adapter_reset_after_failures=reset_after_failures,
            adapter_reset_cooldown_seconds=reset_cooldown_seconds,
        )
    )
    service.printer = _FakePrinter()
    service._consecutive_bluetooth_failures = 0
    service._last_bluetooth_reset_at = 0.0
    return service


if __name__ == "__main__":
    unittest.main()
