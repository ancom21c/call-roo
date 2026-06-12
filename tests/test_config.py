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
                        "dashboard": {
                            "edit_token": "edit-secret",
                        },
                        "input": {
                            "stdin_enabled": False,
                            "linux_event_enabled": True,
                            "linux_event_paths": ["devices/button-event0"],
                            "linux_event_keycodes": [28, 96],
                        },
                        "audio": {
                            "launch_sounds": [
                                {"file": "clip.mp3", "weight": 3.0},
                                {"file": "clip-alt.wav", "weight": 1.5},
                            ],
                            "launch_sound_volume": 0.25,
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
                            "adapter_name": "auto",
                            "disabled_adapter_names": ["hci0"],
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
                                "api_key": "config-key",
                                "api_key_env": "CONFIG_ENV_KEY",
                                "variation_hints": ["형광등 아래", "한 박자 늦게"],
                                "current_time_hint_format": "%m/%d %H:%M",
                                "current_time_hint_pre": "시각 힌트:",
                                "current_time_hint_post": "이 시각감을 반영해.",
                                "cleaned_examples_pre": "피해야 할 예시:",
                                "cleaned_examples_post": "위 표현을 반복하지 마.",
                                "response_tag_key": "selected_tag",
                                "web_search": {
                                    "enabled": True,
                                    "provider": "brave",
                                    "endpoint": "https://api.search.brave.com/res/v1/web/search",
                                    "api_key": "search-config-key",
                                    "api_key_env": "BRAVE_API_KEY",
                                    "query_template": "오늘 {sign}자리 운세",
                                    "signs": ["양", "황소"],
                                    "count": 4,
                                    "search_lang": "en",
                                    "country": "US",
                                    "search_depth": "fast",
                                    "include_answer": True,
                                    "include_raw_content": True,
                                    "tool_calling_enabled": True,
                                    "tool_name": "brave_search",
                                    "tool_description": "Search current web results.",
                                    "tool_max_rounds": 3,
                                    "daily_prefetch_enabled": True,
                                    "daily_prefetch_time": "09:00",
                                },
                                "tags": {
                                    "기계음": ["machine-2.png"],
                                    "형광등": ["hallway-1.png"]
                                }
                            }
                        ],
                        "layout": {
                            "font_path": "fonts/MyFont.ttf",
                            "title_icon_file": "title-icon.png",
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.assets_dir, (root / "assets").resolve())
            self.assertEqual(config.output.logs_dir, (root / "logs").resolve())
            self.assertEqual(config.output.outputs_dir, (root / "outputs").resolve())
            self.assertEqual(config.dashboard.edit_token, "edit-secret")
            self.assertFalse(config.input.stdin_enabled)
            self.assertTrue(config.input.linux_event_enabled)
            self.assertEqual(
                config.input.linux_event_paths,
                ((root / "devices" / "button-event0").resolve(),),
            )
            self.assertEqual(config.input.linux_event_keycodes, (28, 96))
            self.assertEqual(len(config.audio.launch_sounds), 2)
            self.assertEqual(
                config.audio.launch_sounds[0].file,
                (root / "assets" / "clip.mp3").resolve(),
            )
            self.assertEqual(config.audio.launch_sounds[0].weight, 3.0)
            self.assertEqual(
                config.audio.launch_sounds[1].file,
                (root / "assets" / "clip-alt.wav").resolve(),
            )
            self.assertEqual(config.audio.launch_sounds[1].weight, 1.5)
            self.assertEqual(config.audio.launch_sound_volume, 0.25)
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
            self.assertEqual(len(profile.models), 1)
            self.assertEqual(profile.models[0].name, "test-model")
            self.assertEqual(profile.models[0].model, "test-model")
            self.assertTrue(profile.enable_thinking)
            self.assertEqual(profile.api_key, "config-key")
            self.assertEqual(profile.api_key_env, "CONFIG_ENV_KEY")
            self.assertEqual(profile.current_time_hint_format, "%m/%d %H:%M")
            self.assertEqual(profile.current_time_hint_pre, "시각 힌트:")
            self.assertEqual(profile.current_time_hint_post, "이 시각감을 반영해.")
            self.assertEqual(profile.cleaned_examples_pre, "피해야 할 예시:")
            self.assertEqual(profile.cleaned_examples_post, "위 표현을 반복하지 마.")
            self.assertEqual(profile.response_tag_key, "selected_tag")
            self.assertEqual(profile.variation_hints, ("형광등 아래", "한 박자 늦게"))
            self.assertIsNotNone(profile.web_search)
            assert profile.web_search is not None
            self.assertTrue(profile.web_search.enabled)
            self.assertEqual(profile.web_search.provider, "brave")
            self.assertEqual(profile.web_search.api_key, "search-config-key")
            self.assertEqual(profile.web_search.count, 4)
            self.assertEqual(profile.web_search.search_lang, "en")
            self.assertEqual(profile.web_search.country, "US")
            self.assertEqual(profile.web_search.search_depth, "fast")
            self.assertTrue(profile.web_search.include_answer)
            self.assertTrue(profile.web_search.include_raw_content)
            self.assertTrue(profile.web_search.tool_calling_enabled)
            self.assertEqual(profile.web_search.tool_name, "brave_search")
            self.assertEqual(profile.web_search.tool_max_rounds, 3)
            self.assertTrue(profile.web_search.daily_prefetch_enabled)
            self.assertEqual(profile.web_search.daily_prefetch_time, "09:00")
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
            self.assertEqual(config.bluetooth.adapter_name, "auto")
            self.assertEqual(config.bluetooth.disabled_adapter_names, ("hci0",))
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
            self.assertEqual(
                config.layout.title_icon_file,
                (root / "assets" / "title-icon.png").resolve(),
            )

    def test_legacy_clip_settings_still_map_to_launch_sound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "assets_dir": "assets",
                        "audio": {
                            "clip_file": "clip.mp3",
                            "clip_volume": 0.4,
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertIsNone(config.dashboard.edit_token)
            self.assertEqual(len(config.audio.launch_sounds), 1)
            self.assertEqual(
                config.audio.launch_sounds[0].file,
                (root / "assets" / "clip.mp3").resolve(),
            )
            self.assertEqual(config.audio.launch_sounds[0].weight, 1.0)
            self.assertEqual(config.audio.launch_sound_volume, 0.4)

    def test_tavily_web_search_defaults_to_tavily_endpoint_and_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "assets_dir": "assets",
                        "llm": [
                            {
                                "name": "with-tavily",
                                "web_search": {
                                    "enabled": True,
                                    "provider": "tavily",
                                    "api_key": "tvly-config-key",
                                    "tool_calling_enabled": True,
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

            web_search = config.llm.profiles[0].web_search
            self.assertIsNotNone(web_search)
            assert web_search is not None
            self.assertEqual(web_search.provider, "tavily")
            self.assertEqual(web_search.endpoint, "https://api.tavily.com/search")
            self.assertEqual(web_search.api_key, "tvly-config-key")
            self.assertEqual(web_search.api_key_env, "TAVILY_API_KEY")
            self.assertEqual(web_search.search_depth, "basic")
            self.assertFalse(web_search.include_answer)
            self.assertFalse(web_search.include_raw_content)
            self.assertFalse(web_search.daily_prefetch_enabled)
            self.assertEqual(web_search.daily_prefetch_time, "09:00")

    def test_daysaju_web_search_defaults_to_daysaju_endpoint_without_key_env(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "assets_dir": "assets",
                        "llm": [
                            {
                                "name": "with-daysaju",
                                "web_search": {
                                    "enabled": True,
                                    "provider": "daysaju",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

            web_search = config.llm.profiles[0].web_search
            self.assertIsNotNone(web_search)
            assert web_search is not None
            self.assertEqual(web_search.provider, "daysaju")
            self.assertEqual(web_search.endpoint, "https://daysaju.com/fortune/zodiac")
            self.assertIsNone(web_search.api_key)
            self.assertEqual(web_search.api_key_env, "")

    def test_llm_profile_model_candidates_load_with_profile_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "assets_dir": "assets",
                        "llm": [
                            {
                                "name": "multi-model",
                                "endpoint": "https://primary.invalid/v1/",
                                "model": "primary-model",
                                "api_key": "primary-key",
                                "api_key_env": "PRIMARY_KEY",
                                "temperature": 0.8,
                                "max_tokens": 90,
                                "timeout_seconds": 11.0,
                                "models": [
                                    {
                                        "name": "primary",
                                    },
                                    {
                                        "name": "qwen-fallback",
                                        "endpoint": "https://fallback.invalid/v1/",
                                        "model": "fallback-model",
                                        "api_key": None,
                                        "api_key_env": "FALLBACK_KEY",
                                        "temperature": 0.4,
                                        "max_tokens": 70,
                                        "timeout_seconds": 5.0,
                                    },
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

            profile = config.llm.profiles[0]
            self.assertEqual(profile.endpoint, "https://primary.invalid/v1/")
            self.assertEqual(profile.model, "primary-model")
            self.assertEqual(len(profile.models), 2)
            primary, fallback = profile.models
            self.assertEqual(primary.name, "primary")
            self.assertEqual(primary.model, "primary-model")
            self.assertEqual(primary.api_key, "primary-key")
            self.assertEqual(primary.api_key_env, "PRIMARY_KEY")
            self.assertEqual(primary.temperature, 0.8)
            self.assertEqual(primary.max_tokens, 90)
            self.assertEqual(primary.timeout_seconds, 11.0)
            self.assertEqual(fallback.name, "qwen-fallback")
            self.assertEqual(fallback.endpoint, "https://fallback.invalid/v1/")
            self.assertEqual(fallback.model, "fallback-model")
            self.assertIsNone(fallback.api_key)
            self.assertEqual(fallback.api_key_env, "FALLBACK_KEY")
            self.assertEqual(fallback.temperature, 0.4)
            self.assertEqual(fallback.max_tokens, 70)
            self.assertEqual(fallback.timeout_seconds, 5.0)

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
