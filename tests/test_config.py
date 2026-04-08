from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from callroo_printer.config import load_config


class ConfigTest(unittest.TestCase):
    def test_relative_paths_resolve_from_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "assets_dir": "assets",
                        "output": {
                            "logs_dir": "logs",
                            "outputs_dir": "outputs",
                        },
                        "input": {
                            "stdin_enabled": False,
                            "linux_event_enabled": True,
                            "linux_event_paths": ["devices/button-event0"],
                            "linux_event_keycodes": [28, 96],
                        },
                        "audio": {
                            "clip_file": "clip.mp3",
                            "clip_volume": 0.25,
                            "aplay_device": "plughw:CARD=Headphones,DEV=0",
                        },
                        "bluetooth": {
                            "backend": "timiniprint_cli_direct",
                            "timiniprint_repo": "vendor/TiMini-Print",
                            "connect_timeout_seconds": 4.0,
                            "reconnect_delay_seconds": 2.0,
                            "keepalive_interval_seconds": 5.0,
                            "keepalive_timeout_seconds": 3.0,
                            "adapter_name": "hci0",
                            "adapter_reset_after_failures": 4,
                            "adapter_reset_cooldown_seconds": 45.0,
                        },
                        "llm": {"enable_thinking": True},
                        "layout": {"font_path": "fonts/MyFont.ttf"},
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.assets_dir, (root / "assets").resolve())
            self.assertEqual(config.output.logs_dir, (root / "logs").resolve())
            self.assertEqual(config.output.outputs_dir, (root / "outputs").resolve())
            self.assertFalse(config.input.stdin_enabled)
            self.assertTrue(config.input.linux_event_enabled)
            self.assertEqual(
                config.input.linux_event_paths,
                ((root / "devices" / "button-event0").resolve(),),
            )
            self.assertEqual(config.input.linux_event_keycodes, (28, 96))
            self.assertEqual(config.audio.clip_file, (root / "assets" / "clip.mp3").resolve())
            self.assertEqual(config.audio.clip_volume, 0.25)
            self.assertEqual(config.audio.aplay_device, "plughw:CARD=Headphones,DEV=0")
            self.assertTrue(config.llm.enable_thinking)
            self.assertEqual(config.bluetooth.backend, "timiniprint_cli_direct")
            self.assertEqual(config.bluetooth.connect_timeout_seconds, 4.0)
            self.assertEqual(config.bluetooth.reconnect_delay_seconds, 2.0)
            self.assertEqual(config.bluetooth.keepalive_interval_seconds, 5.0)
            self.assertEqual(config.bluetooth.keepalive_timeout_seconds, 3.0)
            self.assertEqual(config.bluetooth.adapter_name, "hci0")
            self.assertEqual(config.bluetooth.adapter_reset_after_failures, 4)
            self.assertEqual(config.bluetooth.adapter_reset_cooldown_seconds, 45.0)
            self.assertEqual(
                config.bluetooth.timiniprint_repo,
                (root / "vendor" / "TiMini-Print").resolve(),
            )
            self.assertEqual(
                config.layout.font_path,
                (root / "fonts" / "MyFont.ttf").resolve(),
            )


if __name__ == "__main__":
    unittest.main()
