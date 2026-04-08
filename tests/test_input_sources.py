from __future__ import annotations

import struct
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from callroo_printer.input_sources import _stdin_is_interactive, parse_linux_input_events


class InputSourcesTest(unittest.TestCase):
    def test_stdin_is_interactive_reflects_tty_state(self) -> None:
        fake_stdin = Mock()
        fake_stdin.isatty.return_value = False
        with patch("callroo_printer.input_sources.sys.stdin", fake_stdin):
            self.assertFalse(_stdin_is_interactive())

    def test_parse_linux_input_events_emits_pressed_enter(self) -> None:
        payload = struct.pack("llHHi", 0, 0, 1, 28, 1) + struct.pack(
            "llHHi", 0, 0, 1, 28, 0
        )

        events = parse_linux_input_events(
            payload,
            device_path=Path("/dev/input/event0"),
            trigger_keycodes={28, 96},
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].raw_input, "\n")
        self.assertEqual(events[0].source, "linux-input")
        self.assertEqual(events[0].details["key_code"], 28)
        self.assertEqual(events[0].details["key_name"], "KEY_ENTER")

    def test_parse_linux_input_events_ignores_non_matching_keys(self) -> None:
        payload = struct.pack("llHHi", 0, 0, 1, 30, 1)

        events = parse_linux_input_events(
            payload,
            device_path=Path("/dev/input/event0"),
            trigger_keycodes={28, 96},
        )

        self.assertEqual(events, [])
