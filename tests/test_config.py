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
                            "event_volume": 0.75,
                            "aplay_device": "plughw:CARD=Headphones,DEV=0",
                            "printer_connected_file": "connected.mp3",
                            "printer_failed_file": "failed.mp3",
                            "print_completed_file": "done.mp3",
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
                        "llm": [
                            {
                                "name": "night-shift",
                                "weight": 2.5,
                                "endpoint": "https://example.invalid/v1/",
                                "model": "test-model",
                                "enable_thinking": True,
                                "variation_hints": ["형광등 아래", "한 박자 늦게"],
                                "current_time_hint_format": "%m/%d %H:%M",
                                "current_time_hint_pre": "시각 힌트:",
                                "current_time_hint_post": "이 시각감을 반영해.",
                                "cleaned_examples_pre": "피해야 할 예시:",
                                "cleaned_examples_post": "위 표현을 반복하지 마.",
                                "response_tag_key": "selected_tag",
                                "tags": {
                                    "기계음": ["machine-2.png"],
                                    "형광등": ["hallway-1.png"]
                                }
                            }
                        ],
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
            self.assertEqual(config.audio.event_volume, 0.75)
            self.assertEqual(config.audio.aplay_device, "plughw:CARD=Headphones,DEV=0")
            self.assertEqual(
                config.audio.printer_connected_file,
                (root / "assets" / "connected.mp3").resolve(),
            )
            self.assertEqual(
                config.audio.printer_failed_file,
                (root / "assets" / "failed.mp3").resolve(),
            )
            self.assertEqual(
                config.audio.print_completed_file,
                (root / "assets" / "done.mp3").resolve(),
            )
            self.assertEqual(len(config.llm.profiles), 1)
            profile = config.llm.profiles[0]
            self.assertEqual(profile.name, "night-shift")
            self.assertEqual(profile.weight, 2.5)
            self.assertEqual(profile.model, "test-model")
            self.assertTrue(profile.enable_thinking)
            self.assertEqual(profile.current_time_hint_format, "%m/%d %H:%M")
            self.assertEqual(profile.current_time_hint_pre, "시각 힌트:")
            self.assertEqual(profile.current_time_hint_post, "이 시각감을 반영해.")
            self.assertEqual(profile.cleaned_examples_pre, "피해야 할 예시:")
            self.assertEqual(profile.cleaned_examples_post, "위 표현을 반복하지 마.")
            self.assertEqual(profile.response_tag_key, "selected_tag")
            self.assertEqual(profile.variation_hints, ("형광등 아래", "한 박자 늦게"))
            self.assertEqual(sorted(profile.tags.keys()), ["기계음", "형광등"])
            self.assertEqual(
                profile.tags["기계음"],
                ((root / "assets" / "machine-2.png").resolve(),),
            )
            self.assertEqual(
                profile.tags["형광등"],
                ((root / "assets" / "hallway-1.png").resolve(),),
            )
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

    def test_single_llm_settings_still_load_as_default_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "assets_dir": "assets",
                        "llm": {
                            "model": "legacy-model",
                            "tags": {"실내": ["room.png"]},
                        }
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(len(config.llm.profiles), 1)
            profile = config.llm.profiles[0]
            self.assertEqual(profile.name, "default")
            self.assertEqual(profile.model, "legacy-model")
            self.assertEqual(profile.variation_hints, ())
            self.assertEqual(sorted(profile.tags.keys()), ["실내"])
            self.assertEqual(
                profile.tags["실내"],
                ((root / "assets" / "room.png").resolve(),),
            )


if __name__ == "__main__":
    unittest.main()
