from __future__ import annotations

import json
import os
import tempfile
import unittest
import base64
from io import BytesIO
from http import HTTPStatus
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from PIL import Image

from callroo_printer.config import load_config
from callroo_printer.dashboard import (
    DashboardSnapshotBuilder,
    LOG_STALE_SECONDS,
    _DASHBOARD_HTML,
    _PRINTER_DASHBOARD_HTML,
    detect_service_config_path,
    _create_llm_profile_config,
    _delete_llm_profile_config,
    _build_health_response,
    _delete_manual_history,
    _parse_multipart_form,
    _queue_dashboard_print,
    _queue_manual_history_reprint,
    _queue_manual_print,
    _queue_rest_image_print,
    _queue_rest_text_print,
    _update_llm_profile_config,
    _upload_asset,
    _verify_dashboard_edit_token,
    _write_config_payload,
)


class DashboardHtmlTest(unittest.TestCase):
    def test_dashboard_uses_product_headline_and_compact_subtitle(self) -> None:
        self.assertIn("<h1>CALLROO PRINTER DASHBOARD</h1>", _DASHBOARD_HTML)
        self.assertIn(
            '<div class="hero-subtitle">생성 결과, 상태, 로그를 한 화면에서</div>',
            _DASHBOARD_HTML,
        )

    def test_dashboard_renders_bluetooth_status_card(self) -> None:
        self.assertIn('label: "Bluetooth Status"', _DASHBOARD_HTML)
        self.assertIn(
            "renderRuntime(snapshot.runtime || {}, snapshot.bluetooth || {});",
            _DASHBOARD_HTML,
        )

    def test_dashboard_renders_bluetooth_status_pill(self) -> None:
        self.assertIn('id="bluetooth-pill"', _DASHBOARD_HTML)
        self.assertIn('id="bluetooth-pill-text"', _DASHBOARD_HTML)
        self.assertIn("bluetoothStatusLevel(bluetooth.status)", _DASHBOARD_HTML)
        self.assertIn("Bluetooth ${bluetoothStatusLabel(bluetooth.status)}", _DASHBOARD_HTML)

    def test_dashboard_uses_asset_for_favicon_and_headline_mark(self) -> None:
        asset_url = "/asset/Gemini_Generated_Image_trohomtrohomtroh_top_left.png"

        self.assertIn(f'<link rel="icon" type="image/png" href="{asset_url}">', _DASHBOARD_HTML)
        self.assertIn(f'<link rel="apple-touch-icon" href="{asset_url}">', _DASHBOARD_HTML)
        self.assertIn(f'<img class="hero-mark" src="{asset_url}"', _DASHBOARD_HTML)

    def test_dashboard_links_to_manual_printer_page(self) -> None:
        self.assertIn('href="/print"', _DASHBOARD_HTML)
        self.assertIn("MANUAL PRINTER DASHBOARD", _PRINTER_DASHBOARD_HTML)
        self.assertIn('href="/"', _PRINTER_DASHBOARD_HTML)

    def test_manual_printer_page_rejects_document_uploads_in_ui(self) -> None:
        self.assertIn(
            'accept="image/png,image/jpeg,image/webp,image/gif,image/bmp"',
            _PRINTER_DASHBOARD_HTML,
        )
        self.assertIn("PDF, DOCX, TXT", _PRINTER_DASHBOARD_HTML)
        self.assertIn('id="label-width"', _PRINTER_DASHBOARD_HTML)
        self.assertIn('id="label-height"', _PRINTER_DASHBOARD_HTML)
        self.assertIn('id="image-scale"', _PRINTER_DASHBOARD_HTML)
        self.assertIn('id="image-rotation"', _PRINTER_DASHBOARD_HTML)
        self.assertIn('id="image-crop"', _PRINTER_DASHBOARD_HTML)
        self.assertIn('id="image-x"', _PRINTER_DASHBOARD_HTML)
        self.assertIn('id="image-y"', _PRINTER_DASHBOARD_HTML)
        self.assertIn('id="image-width"', _PRINTER_DASHBOARD_HTML)
        self.assertIn('id="image-height"', _PRINTER_DASHBOARD_HTML)
        self.assertIn('id="fit-selected-image"', _PRINTER_DASHBOARD_HTML)
        self.assertIn("URL.createObjectURL(file)", _PRINTER_DASHBOARD_HTML)
        self.assertIn("URL.revokeObjectURL", _PRINTER_DASHBOARD_HTML)
        self.assertIn("renderCanvasImages(imageLayer, options)", _PRINTER_DASHBOARD_HTML)
        self.assertIn('multiple>', _PRINTER_DASHBOARD_HTML)
        self.assertIn('id="canvas-image-layer"', _PRINTER_DASHBOARD_HTML)
        self.assertIn('id="label-resize-handle"', _PRINTER_DASHBOARD_HTML)
        self.assertIn('data-reprint-history', _PRINTER_DASHBOARD_HTML)
        self.assertIn('id="manual-history-list"', _PRINTER_DASHBOARD_HTML)


class DashboardHealthTest(unittest.TestCase):
    def test_health_is_ok_only_when_service_and_bluetooth_are_running(self) -> None:
        payload, status = _build_health_response(
            {
                "service": {"level": "healthy", "label": "running"},
                "bluetooth": {"status": "connected", "updated_at": "2026-06-05T20:43:56+09:00"},
            }
        )

        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "healthy")
        self.assertTrue(payload["service"]["running"])
        self.assertTrue(payload["bluetooth"]["running"])

    def test_health_is_unhealthy_when_bluetooth_is_not_connected(self) -> None:
        payload, status = _build_health_response(
            {
                "service": {"level": "healthy", "label": "running"},
                "bluetooth": {"status": "retrying", "last_error": "Timed out connecting"},
            }
        )

        self.assertEqual(status, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "unhealthy")
        self.assertTrue(payload["service"]["running"])
        self.assertFalse(payload["bluetooth"]["running"])
        self.assertEqual(payload["bluetooth"]["status"], "retrying")
        self.assertEqual(payload["bluetooth"]["last_error"], "Timed out connecting")


class DashboardSnapshotBuilderTest(unittest.TestCase):
    def test_snapshot_filters_previews_by_selected_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)
            _write_log(root / "logs" / "callroo-printer.log")

            _create_job(
                config.output.outputs_dir / "jobs" / "20260414-090000-aa11bb22",
                triggered_at="2026-04-14T09:00:00+09:00",
                status="printed",
                fortune="첫 번째 운세",
                profile_name="default",
                tag="여유",
                asset_name="room.png",
            )
            _create_job(
                config.output.outputs_dir / "jobs" / "20260413-101500-cc33dd44",
                triggered_at="2026-04-13T10:15:00+09:00",
                status="failed",
                fortune="두 번째 운세",
                profile_name="night",
                tag="발끈",
                asset_name="night.png",
                include_preview=False,
                error="llm timeout",
            )

            builder = DashboardSnapshotBuilder(config)

            with patch(
                "callroo_printer.dashboard.subprocess.run",
                return_value=_systemd_status("active", "running"),
            ):
                snapshot = builder.build_snapshot(selected_date="2026-04-14")

            self.assertEqual(snapshot["filtered_jobs"], 1)
            self.assertEqual(snapshot["previews"][0]["job_id"], "20260414-090000-aa11bb22")
            self.assertEqual(snapshot["previews"][0]["image_url"], "/preview/20260414-090000-aa11bb22")
            self.assertEqual(snapshot["status_counts"], {"printed": 1, "failed": 1})
            self.assertEqual(
                snapshot["available_dates"],
                [
                    {"date": "2026-04-14", "count": 1},
                    {"date": "2026-04-13", "count": 1},
                ],
            )

    def test_snapshot_includes_llm_prompt_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)
            _write_log(root / "logs" / "callroo-printer.log")
            builder = DashboardSnapshotBuilder(config)

            with patch(
                "callroo_printer.dashboard.subprocess.run",
                return_value=_systemd_status("inactive", "dead"),
            ):
                snapshot = builder.build_snapshot()

            self.assertEqual(len(snapshot["llm_profiles"]), 2)
            first = snapshot["llm_profiles"][0]
            self.assertEqual(first["name"], "default")
            self.assertEqual(first["model"], "dashboard-model")
            self.assertTrue(first["api_key_configured"])
            self.assertNotIn("api_key", first)
            self.assertEqual(first["api_key_env"], "DASHBOARD_KEY")
            self.assertEqual(len(first["models"]), 1)
            self.assertEqual(first["models"][0]["model"], "dashboard-model")
            self.assertTrue(first["models"][0]["api_key_configured"])
            self.assertNotIn("api_key", first["models"][0])
            self.assertTrue(snapshot["dashboard"]["settings_token_required"])
            self.assertNotIn(
                "dashboard-edit-token",
                json.dumps(snapshot, ensure_ascii=False),
            )
            self.assertIn("메인 프롬프트", first["prompt"])
            self.assertEqual(first["tags"][0]["name"], "여유")
            self.assertEqual(first["tags"][0]["asset_count"], 1)

    def test_snapshot_includes_registered_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)
            builder = DashboardSnapshotBuilder(config)

            with patch(
                "callroo_printer.dashboard.subprocess.run",
                return_value=_systemd_status("active", "running"),
            ):
                snapshot = builder.build_snapshot()

            images = snapshot["artifacts"]["images"]
            audio = snapshot["artifacts"]["audio"]
            image_names = {entry["name"] for entry in images}
            audio_names = {entry["name"] for entry in audio}
            room = next(entry for entry in images if entry["name"] == "room.png")
            loose_image = next(entry for entry in images if entry["name"] == "loose.png")
            clip = next(entry for entry in audio if entry["name"] == "clip.wav")
            loose_audio = next(entry for entry in audio if entry["name"] == "loose.mp3")

            self.assertIn("room.png", image_names)
            self.assertIn("night.png", image_names)
            self.assertIn("loose.png", image_names)
            self.assertIn("clip.wav", audio_names)
            self.assertIn("connected.mp3", audio_names)
            self.assertIn("loose.mp3", audio_names)
            self.assertTrue(room["registered"])
            self.assertIn("default / 여유", room["labels"])
            self.assertFalse(loose_image["registered"])
            self.assertTrue(clip["registered"])
            self.assertIn("출력 중 반복 재생 · weight 2", clip["labels"])
            self.assertTrue(clip["url"].startswith("/asset/"))
            self.assertFalse(loose_audio["registered"])
            self.assertEqual(builder.resolve_asset_file("clip.wav"), (root / "assets" / "clip.wav").resolve())
            self.assertIsNone(builder.resolve_asset_file("../config.json"))

    def test_snapshot_includes_bluetooth_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)
            status_path = config.output.outputs_dir / "bluetooth-status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "status": "retrying",
                        "message": "Printer connection failed; retrying.",
                        "updated_at": "2026-06-05T20:05:48+09:00",
                        "backend": "timiniprint_cli_direct",
                        "mac_address": "00:11:22:33:44:55",
                        "adapter_name": "hci0",
                        "failure_count": 2,
                        "last_error": "Timed out connecting",
                        "keepalive_supported": True,
                    }
                ),
                encoding="utf-8",
            )
            builder = DashboardSnapshotBuilder(config)

            with patch(
                "callroo_printer.dashboard.subprocess.run",
                return_value=_systemd_status("active", "running"),
            ):
                snapshot = builder.build_snapshot()

            bluetooth = snapshot["bluetooth"]
            self.assertTrue(bluetooth["exists"])
            self.assertEqual(bluetooth["status"], "retrying")
            self.assertEqual(bluetooth["message"], "Printer connection failed; retrying.")
            self.assertEqual(bluetooth["backend"], "timiniprint_cli_direct")
            self.assertEqual(bluetooth["mac_address"], "00:11:22:33:44:55")
            self.assertEqual(bluetooth["adapter_name"], "hci0")
            self.assertEqual(bluetooth["failure_count"], 2)
            self.assertEqual(bluetooth["last_error"], "Timed out connecting")
            self.assertTrue(bluetooth["keepalive_supported"])

    def test_snapshot_uses_cache_until_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)
            builder = DashboardSnapshotBuilder(config, snapshot_cache_seconds=30.0)

            with patch(
                "callroo_printer.dashboard.subprocess.run",
                return_value=_systemd_status("active", "running"),
            ) as run:
                first = builder.build_snapshot()
                second = builder.build_snapshot()
                builder.clear_cache()
                third = builder.build_snapshot()

            self.assertIs(first, second)
            self.assertIsNot(second, third)
            self.assertEqual(run.call_count, 2)

    def test_snapshot_loads_only_visible_preview_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)
            _write_log(root / "logs" / "callroo-printer.log")
            for index in range(5):
                _create_job(
                    config.output.outputs_dir
                    / "jobs"
                    / f"20260414-09000{index}-aa11bb2{index}",
                    triggered_at=f"2026-04-14T09:00:0{index}+09:00",
                    status="printed",
                    fortune=f"{index}번째 운세",
                    profile_name="default",
                    tag="여유",
                    asset_name="room.png",
                )

            builder = DashboardSnapshotBuilder(config, preview_limit=1)

            with patch(
                "callroo_printer.dashboard.subprocess.run",
                return_value=_systemd_status("active", "running"),
            ):
                with patch.object(
                    builder,
                    "_load_job_summary",
                    wraps=builder._load_job_summary,
                ) as load_summary:
                    snapshot = builder.build_snapshot()

            self.assertEqual(snapshot["total_jobs"], 5)
            self.assertEqual(snapshot["filtered_jobs"], 5)
            self.assertEqual(len(snapshot["previews"]), 1)
            self.assertEqual(load_summary.call_count, 1)

    def test_log_snapshot_reads_tail_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)
            log_path = root / "logs" / "callroo-printer.log"
            log_path.write_text(
                "\n".join(f"2026-04-14 09:00:{index:02d},000 INFO message {index}" for index in range(10))
                + "\n",
                encoding="utf-8",
            )
            builder = DashboardSnapshotBuilder(config, log_lines=3)

            snapshot = builder._read_log_snapshot()

            self.assertEqual(
                snapshot["tail_text"],
                "\n".join(
                    [
                        "2026-04-14 09:00:07,000 INFO message 7",
                        "2026-04-14 09:00:08,000 INFO message 8",
                        "2026-04-14 09:00:09,000 INFO message 9",
                    ]
                ),
            )
            self.assertEqual(snapshot["line_count"], 3)

    def test_service_status_uses_stale_log_without_systemd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)
            log_path = root / "logs" / "callroo-printer.log"
            _write_log(log_path)
            stale_time = log_path.stat().st_mtime - (LOG_STALE_SECONDS + 120)
            os.utime(log_path, (stale_time, stale_time))

            _create_job(
                config.output.outputs_dir / "jobs" / "20260414-010203-aa11bb22",
                triggered_at="2026-04-14T01:02:03+09:00",
                status="printed",
                fortune="오래된 운세",
                profile_name="default",
                tag="여유",
                asset_name="room.png",
            )

            builder = DashboardSnapshotBuilder(config)

            with patch(
                "callroo_printer.dashboard.subprocess.run",
                side_effect=FileNotFoundError(),
            ):
                snapshot = builder.build_snapshot()

            self.assertEqual(snapshot["service"]["level"], "stale")
            self.assertEqual(snapshot["service"]["label"], "stale")
            self.assertFalse(snapshot["service"]["systemd"]["available"])
            self.assertGreater(snapshot["service"]["log_age_seconds"], LOG_STALE_SECONDS)

    def test_detect_service_config_path_prefers_execstart_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "installed-config.json"
            config_path.write_text("{}", encoding="utf-8")

            with patch(
                "callroo_printer.dashboard.subprocess.run",
                return_value=CompletedProcess(
                    args=["systemctl"],
                    returncode=0,
                    stdout="\n".join(
                        [
                            "LoadState=loaded",
                            f"ExecStart={{ path=/opt/callroo-printer/.venv/bin/python ; argv[]=/opt/callroo-printer/.venv/bin/python -m callroo_printer --config {config_path} --log-level INFO ; ignore_errors=no ; }}",
                            "WorkingDirectory=/opt/callroo-printer",
                        ]
                    )
                    + "\n",
                    stderr="",
                ),
            ):
                detected = detect_service_config_path()

            self.assertEqual(detected, config_path.resolve())

    def test_update_llm_profile_config_preserves_model_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)
            config_path = root / "config.json"

            result = _update_llm_profile_config(
                config_path,
                {
                    "name": "default",
                    "weight": 2.0,
                    "prompt": "수정된 프롬프트",
                    "models": [
                        {
                            "name": "edited",
                            "endpoint": "https://edited.invalid/v1/",
                            "model": "edited-model",
                            "temperature": 0.3,
                            "max_tokens": 77,
                            "timeout_seconds": 9.0,
                        }
                    ],
                },
            )

            self.assertTrue(result["ok"])
            updated = load_config(config_path)
            profile = updated.llm.profiles[0]
            self.assertEqual(profile.weight, 2.0)
            self.assertEqual(profile.prompt, "수정된 프롬프트")
            self.assertEqual(profile.variation_hints, ())
            self.assertEqual(profile.models[0].name, "edited")
            self.assertEqual(profile.models[0].model, "edited-model")
            self.assertEqual(profile.models[0].api_key, "dashboard-config-key")

    def test_write_config_payload_preserves_existing_file_if_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps({"llm": [{"name": "old"}]}),
                encoding="utf-8",
            )

            with patch.object(Path, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    _write_config_payload(
                        config_path,
                        {"llm": [{"name": "new"}]},
                    )

            self.assertEqual(
                json.loads(config_path.read_text(encoding="utf-8")),
                {"llm": [{"name": "old"}]},
            )
            self.assertEqual(list(root.glob(".config.json.*.tmp")), [])

    def test_write_config_payload_cleans_temp_file_if_fsync_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps({"llm": [{"name": "old"}]}),
                encoding="utf-8",
            )

            with patch("callroo_printer.dashboard.os.fsync", side_effect=OSError("fsync failed")):
                with self.assertRaises(OSError):
                    _write_config_payload(
                        config_path,
                        {"llm": [{"name": "new"}]},
                    )

            self.assertEqual(
                json.loads(config_path.read_text(encoding="utf-8")),
                {"llm": [{"name": "old"}]},
            )
            self.assertEqual(list(root.glob(".config.json.*.tmp")), [])

    def test_dashboard_edit_token_verification_uses_config_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)

            _verify_dashboard_edit_token(
                config,
                {"edit_token": "dashboard-edit-token"},
            )
            with self.assertRaises(PermissionError):
                _verify_dashboard_edit_token(config, {"edit_token": "wrong"})

    def test_create_and_delete_llm_profile_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root)
            config_path = root / "config.json"

            created = _create_llm_profile_config(
                config_path,
                {"name": "gigachad", "source_name": "default"},
            )

            self.assertTrue(created["ok"])
            self.assertEqual(created["profile"], "gigachad")
            updated = load_config(config_path)
            self.assertEqual(
                [profile.name for profile in updated.llm.profiles],
                ["default", "night", "gigachad"],
            )
            self.assertEqual(updated.llm.profiles[2].prompt, "메인 프롬프트")
            self.assertEqual(updated.llm.profiles[2].models[0].api_key, "dashboard-config-key")

            deleted = _delete_llm_profile_config(config_path, {"name": "gigachad"})

            self.assertTrue(deleted["ok"])
            self.assertEqual(deleted["selected_profile"], "default")
            updated_after_delete = load_config(config_path)
            self.assertEqual(
                [profile.name for profile in updated_after_delete.llm.profiles],
                ["default", "night"],
            )

    def test_upload_image_can_attach_to_profile_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)
            config_path = root / "config.json"

            result = _upload_asset(
                config_path,
                config,
                {
                    "kind": "image",
                    "filename": "../lucky.png",
                    "content_base64": "aW1hZ2U=",
                    "profile_name": "default",
                    "tag": "행운",
                },
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["filename"], "lucky.png")
            self.assertTrue((root / "assets" / "lucky.png").is_file())
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertIn("lucky.png", payload["llm"][0]["tags"]["행운"])

    def test_upload_image_does_not_write_file_when_config_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)
            config_path = root / "config.json"
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["llm"] = {"name": "legacy-shape"}
            config_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(ValueError):
                _upload_asset(
                    config_path,
                    config,
                    {
                        "kind": "image",
                        "filename": "orphan.png",
                        "content_base64": "aW1hZ2U=",
                        "profile_name": "default",
                        "tag": "행운",
                    },
                )

            self.assertFalse((root / "assets" / "orphan.png").exists())

    def test_upload_image_restores_existing_file_when_config_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)
            config_path = root / "config.json"
            target_path = root / "assets" / "room.png"
            target_path.write_bytes(b"old-room")

            with patch(
                "callroo_printer.dashboard._write_config_payload",
                side_effect=OSError("write failed"),
            ):
                with self.assertRaises(OSError):
                    _upload_asset(
                        config_path,
                        config,
                        {
                            "kind": "image",
                            "filename": "room.png",
                            "content_base64": "bmV3LXJvb20=",
                            "profile_name": "default",
                            "tag": "행운",
                        },
                    )

            self.assertEqual(target_path.read_bytes(), b"old-room")

    def test_queue_dashboard_print_appends_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)

            result = _queue_dashboard_print(config, {"note": "test"})

            self.assertTrue(result["ok"])
            trigger_path = config.output.outputs_dir / "dashboard-triggers.jsonl"
            payload = json.loads(trigger_path.read_text(encoding="utf-8").strip())
            self.assertEqual(payload["request_id"], result["request_id"])
            self.assertEqual(payload["note"], "test")

    def test_queue_dashboard_print_repairs_missing_jsonl_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)
            trigger_path = config.output.outputs_dir / "dashboard-triggers.jsonl"
            trigger_path.write_text('{"request_id": "truncated"', encoding="utf-8")

            result = _queue_dashboard_print(config, {"note": "after-truncation"})

            records = trigger_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(records[0], '{"request_id": "truncated"')
            payload = json.loads(records[1])
            self.assertEqual(payload["request_id"], result["request_id"])
            self.assertEqual(payload["note"], "after-truncation")

    def test_queue_manual_print_appends_text_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)

            result = _queue_manual_print(
                config,
                {
                    "text": "바로 출력할 문구",
                    "border_style": "double",
                    "text_align": "left",
                    "font_size": 32,
                    "label_width_px": 220,
                    "label_height_px": 96,
                    "image_scale_percent": 150,
                    "image_crop": True,
                    "image_rotation_degrees": 90,
                },
            )

            self.assertTrue(result["ok"])
            trigger_path = config.output.outputs_dir / "dashboard-triggers.jsonl"
            payload = json.loads(trigger_path.read_text(encoding="utf-8").strip())
            self.assertEqual(payload["request_id"], result["request_id"])
            self.assertEqual(payload["note"], "manual-print")
            self.assertEqual(payload["manual_print"]["text"], "바로 출력할 문구")
            self.assertEqual(payload["manual_print"]["border_style"], "double")
            self.assertEqual(payload["manual_print"]["text_align"], "left")
            self.assertEqual(payload["manual_print"]["label_width_px"], 220)
            self.assertEqual(payload["manual_print"]["label_height_px"], 96)
            self.assertEqual(payload["manual_print"]["image_scale_percent"], 150)
            self.assertTrue(payload["manual_print"]["image_crop"])
            self.assertEqual(payload["manual_print"]["image_rotation_degrees"], 90)
            self.assertFalse(payload["manual_print"]["image_path"])
            history_image = config.output.outputs_dir / "manual-history" / result["request_id"] / "composed-ticket.png"
            self.assertTrue(history_image.is_file())

    def test_queue_manual_print_saves_valid_image_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)

            result = _queue_manual_print(
                config,
                {
                    "text": "",
                    "image": {
                        "filename": "manual.png",
                        "content_base64": _png_base64(),
                    },
                },
            )

            self.assertTrue(result["has_image"])
            trigger_path = config.output.outputs_dir / "dashboard-triggers.jsonl"
            payload = json.loads(trigger_path.read_text(encoding="utf-8").strip())
            image_path = Path(payload["manual_print"]["image_path"])
            self.assertTrue(image_path.is_file())
            self.assertEqual(image_path.suffix, ".png")
            self.assertTrue(image_path.is_relative_to(config.output.outputs_dir / "manual-uploads"))
            history_dir = config.output.outputs_dir / "manual-history" / result["request_id"]
            self.assertTrue((history_dir / "composed-ticket.png").is_file())
            history_payload = json.loads((history_dir / "manual-print.json").read_text(encoding="utf-8"))
            self.assertEqual(history_payload["image_name"], "manual.png")
            history = DashboardSnapshotBuilder(config).list_manual_history()
            self.assertEqual(history[0]["output_width_px"], config.layout.paper_width_px)

    def test_queue_manual_print_saves_multiple_positioned_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)

            result = _queue_manual_print(
                config,
                {
                    "text": "두 장",
                    "label_width_px": 220,
                    "label_height_px": 120,
                    "images": [
                        {
                            "id": "first",
                            "filename": "first.png",
                            "content_base64": _png_base64(),
                            "x": 10,
                            "y": 12,
                            "width": 70,
                            "height": 50,
                            "rotation_degrees": 15,
                            "crop": True,
                        },
                        {
                            "id": "second",
                            "filename": "second.png",
                            "content_base64": _png_base64(),
                            "x": 90,
                            "y": 24,
                            "width": 60,
                            "height": 40,
                        },
                    ],
                },
            )

            self.assertTrue(result["has_image"])
            self.assertEqual(result["image_count"], 2)
            trigger_path = config.output.outputs_dir / "dashboard-triggers.jsonl"
            payload = json.loads(trigger_path.read_text(encoding="utf-8").strip())
            images = payload["manual_print"]["images"]
            self.assertEqual(len(images), 2)
            self.assertEqual(images[0]["x"], 10)
            self.assertEqual(images[0]["width"], 70)
            self.assertTrue(images[0]["crop"])
            self.assertTrue(Path(images[0]["path"]).is_file())
            history_dir = config.output.outputs_dir / "manual-history" / result["request_id"]
            history_payload = json.loads((history_dir / "manual-print.json").read_text(encoding="utf-8"))
            self.assertEqual(len(history_payload["images"]), 2)

    def test_queue_manual_print_saves_multiple_positioned_text_boxes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)

            result = _queue_manual_print(
                config,
                {
                    "text": "첫 줄\n둘째",
                    "label_width_px": 240,
                    "label_height_px": 160,
                    "text_align": "right",
                    "text_vertical_align": "bottom",
                    "text_items": [
                        {
                            "id": "title",
                            "text": "제목",
                            "x": 8,
                            "y": 10,
                            "width": 120,
                            "height": 48,
                            "font_size": 32,
                            "text_align": "center",
                            "vertical_align": "top",
                        },
                        {
                            "id": "caption",
                            "text": "하단",
                            "x": 90,
                            "y": 90,
                            "width": 100,
                            "height": 52,
                            "font_size": 24,
                            "text_align": "right",
                            "vertical_align": "bottom",
                        },
                    ],
                },
            )

            self.assertEqual(result["text_count"], 2)
            trigger_path = config.output.outputs_dir / "dashboard-triggers.jsonl"
            payload = json.loads(trigger_path.read_text(encoding="utf-8").strip())
            manual = payload["manual_print"]
            self.assertEqual(manual["text_vertical_align"], "bottom")
            self.assertEqual(len(manual["text_items"]), 2)
            self.assertEqual(manual["text_items"][0]["text"], "제목")
            self.assertEqual(manual["text_items"][0]["vertical_align"], "top")
            history_dir = config.output.outputs_dir / "manual-history" / result["request_id"]
            history_payload = json.loads((history_dir / "manual-print.json").read_text(encoding="utf-8"))
            self.assertEqual(len(history_payload["text_items"]), 2)
            self.assertTrue((history_dir / "composed-ticket.png").is_file())

    def test_queue_rest_text_print_accepts_label_and_border_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)

            result = _queue_rest_text_print(
                config,
                {
                    "text": "REST 문구",
                    "label_width": "216",
                    "label_height": "92",
                    "border": "double",
                    "align": "right",
                    "vertical_align": "bottom",
                    "font_size": "30",
                },
            )

            self.assertTrue(result["ok"])
            trigger_path = config.output.outputs_dir / "dashboard-triggers.jsonl"
            payload = json.loads(trigger_path.read_text(encoding="utf-8").strip())
            manual = payload["manual_print"]
            self.assertEqual(manual["text"], "REST 문구")
            self.assertEqual(manual["label_width_px"], 216)
            self.assertEqual(manual["label_height_px"], 92)
            self.assertEqual(manual["border_style"], "double")
            self.assertEqual(manual["text_align"], "right")
            self.assertEqual(manual["text_vertical_align"], "bottom")
            self.assertEqual(manual["font_size"], 30)

    def test_queue_rest_image_print_accepts_file_payload_and_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)

            result = _queue_rest_image_print(
                config,
                {
                    "image": {
                        "filename": "rest.png",
                        "content_base64": _png_base64(),
                    },
                    "label_width_px": "240",
                    "label_height_px": "120",
                    "border_style": "thick",
                    "image_width": "180",
                    "image_height": "80",
                    "image_x": "12",
                    "image_y": "8",
                    "rotation": "15",
                    "crop": "true",
                },
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["image_count"], 1)
            trigger_path = config.output.outputs_dir / "dashboard-triggers.jsonl"
            payload = json.loads(trigger_path.read_text(encoding="utf-8").strip())
            manual = payload["manual_print"]
            self.assertEqual(manual["label_width_px"], 240)
            self.assertEqual(manual["label_height_px"], 120)
            self.assertEqual(manual["border_style"], "thick")
            self.assertEqual(manual["images"][0]["width"], 180)
            self.assertEqual(manual["images"][0]["height"], 80)
            self.assertEqual(manual["images"][0]["x"], 12)
            self.assertEqual(manual["images"][0]["rotation_degrees"], 15)
            self.assertTrue(manual["images"][0]["crop"])
            self.assertTrue(Path(manual["images"][0]["path"]).is_file())

    def test_parse_multipart_form_extracts_image_file_and_fields(self) -> None:
        boundary = "callroo-test-boundary"
        raw_body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="label_width"\r\n'
            "\r\n"
            "200\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="image"; filename="rest.png"\r\n'
            "Content-Type: image/png\r\n"
            "\r\n"
        ).encode("utf-8") + base64.b64decode(_png_base64()) + (
            f"\r\n--{boundary}--\r\n"
        ).encode("utf-8")

        payload = _parse_multipart_form(
            f"multipart/form-data; boundary={boundary}",
            raw_body,
        )

        self.assertEqual(payload["label_width"], "200")
        self.assertEqual(payload["image"]["filename"], "rest.png")
        self.assertTrue(payload["image"]["content_base64"])

    def test_delete_manual_history_removes_only_history_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)
            result = _queue_manual_print(config, {"text": "삭제할 이력"})
            history_dir = config.output.outputs_dir / "manual-history" / result["request_id"]

            deleted = _delete_manual_history(config, result["request_id"])

            self.assertTrue(deleted["ok"])
            self.assertTrue(deleted["deleted"])
            self.assertFalse(history_dir.exists())
            self.assertTrue((config.output.outputs_dir / "dashboard-triggers.jsonl").is_file())

    def test_queue_manual_history_reprint_duplicates_history_and_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)
            original = _queue_manual_print(
                config,
                {
                    "text": "다시 출력",
                    "label_width_px": 220,
                    "label_height_px": 110,
                    "images": [
                        {
                            "id": "first",
                            "filename": "original.png",
                            "content_base64": _png_base64(),
                            "x": 10,
                            "y": 8,
                            "width": 80,
                            "height": 40,
                        },
                    ],
                },
            )

            result = _queue_manual_history_reprint(config, original["request_id"])

            self.assertTrue(result["ok"])
            self.assertEqual(result["reprinted_from"], original["request_id"])
            self.assertNotEqual(result["request_id"], original["request_id"])
            trigger_path = config.output.outputs_dir / "dashboard-triggers.jsonl"
            records = [
                json.loads(line)
                for line in trigger_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(records[-1]["note"], "manual-reprint")
            manual = records[-1]["manual_print"]
            self.assertEqual(manual["text"], "다시 출력")
            self.assertEqual(manual["label_width_px"], 220)
            self.assertEqual(manual["images"][0]["x"], 10)
            self.assertTrue(Path(manual["images"][0]["path"]).is_file())
            original_image_path = Path(records[0]["manual_print"]["images"][0]["path"])
            reprint_image_path = Path(manual["images"][0]["path"])
            self.assertNotEqual(original_image_path, reprint_image_path)
            history_dir = config.output.outputs_dir / "manual-history" / result["request_id"]
            history_payload = json.loads((history_dir / "manual-print.json").read_text(encoding="utf-8"))
            self.assertEqual(history_payload["reprinted_from"], original["request_id"])

    def test_queue_manual_print_rejects_document_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _write_config(root)

            with self.assertRaisesRegex(ValueError, "image files"):
                _queue_manual_print(
                    config,
                    {
                        "text": "문서 말고 이미지",
                        "image": {
                            "filename": "brief.pdf",
                            "content_base64": "JVBERi0xLjQ=",
                        },
                    },
                )

            self.assertFalse((config.output.outputs_dir / "dashboard-triggers.jsonl").exists())


def _png_base64() -> str:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color="black").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _write_config(root: Path):
    (root / "assets").mkdir()
    (root / "logs").mkdir()
    (root / "outputs").mkdir()
    (root / "assets" / "room.png").write_bytes(b"room")
    (root / "assets" / "night.png").write_bytes(b"night")
    (root / "assets" / "loose.png").write_bytes(b"loose")
    (root / "assets" / "clip.wav").write_bytes(b"clip")
    (root / "assets" / "connected.mp3").write_bytes(b"connected")
    (root / "assets" / "loose.mp3").write_bytes(b"loose-audio")

    config_path = root / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "assets_dir": "assets",
                "output": {
                    "logs_dir": "logs",
                    "outputs_dir": "outputs",
                    "log_filename": "callroo-printer.log",
                },
                "cooldown_seconds": 45,
                "cooldown_on_trigger": False,
                "dashboard": {
                    "edit_token": "dashboard-edit-token",
                },
                "bluetooth": {
                    "backend": "timiniprint_cli_direct",
                    "mac_address": "00:11:22:33:44:55",
                },
                "audio": {
                    "launch_sounds": [
                        {"file": "clip.wav", "weight": 2.0},
                    ],
                    "printer_connected_file": "connected.mp3",
                },
                "llm": [
                    {
                        "name": "default",
                        "weight": 1.0,
                        "endpoint": "https://example.invalid/v1/",
                        "model": "dashboard-model",
                        "system_prompt": "시스템 프롬프트",
                        "prompt": "메인 프롬프트",
                        "api_key": "dashboard-config-key",
                        "api_key_env": "DASHBOARD_KEY",
                        "tags": {
                            "여유": ["room.png"],
                        },
                    },
                    {
                        "name": "night",
                        "weight": 0.5,
                        "endpoint": "https://night.invalid/v1/",
                        "model": "night-model",
                        "system_prompt": "야간 시스템 프롬프트",
                        "prompt": "야간 메인 프롬프트",
                        "tags": {
                            "발끈": ["night.png"],
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return load_config(config_path)


def _create_job(
    job_dir: Path,
    *,
    triggered_at: str,
    status: str,
    fortune: str,
    profile_name: str,
    tag: str,
    asset_name: str,
    include_preview: bool = True,
    error: str | None = None,
) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "input.json").write_text(
        json.dumps(
            {
                "triggered_at": triggered_at,
                "raw_input": "\n",
                "dry_run": False,
                "trigger_source": "stdin",
            }
        ),
        encoding="utf-8",
    )
    result_payload = {
        "status": status,
        "triggered_at": triggered_at,
        "asset_path": str(job_dir.parent.parent.parent / "assets" / asset_name),
        "llm_profile_name": profile_name,
        "selected_tag": tag,
        "used_fallback": False,
        "dry_run": False,
    }
    if error:
        result_payload["error"] = error
    (job_dir / "result.json").write_text(
        json.dumps(result_payload),
        encoding="utf-8",
    )
    (job_dir / "selected-llm-profile.json").write_text(
        json.dumps({"profile_name": profile_name}),
        encoding="utf-8",
    )
    (job_dir / "selected-asset.json").write_text(
        json.dumps(
            {
                "asset_path": str(job_dir.parent.parent.parent / "assets" / asset_name),
                "selected_tag": tag,
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "fortune.txt").write_text(fortune + "\n", encoding="utf-8")
    (job_dir / "tag.txt").write_text(tag + "\n", encoding="utf-8")
    if include_preview:
        (job_dir / "composed-ticket.png").write_bytes(b"png")


def _write_log(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "2026-04-14 09:00:00,000 INFO callroo_printer.service: Trigger received from stdin",
                "2026-04-14 09:00:03,000 INFO callroo_printer.service: Printed ticket using asset room.png",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _systemd_status(active_state: str, sub_state: str) -> CompletedProcess[str]:
    return CompletedProcess(
        args=["systemctl"],
        returncode=0,
        stdout="\n".join(
            [
                "LoadState=loaded",
                f"ActiveState={active_state}",
                f"SubState={sub_state}",
                "MainPID=1234",
                "UnitFileState=enabled",
            ]
        )
        + "\n",
        stderr="",
    )


if __name__ == "__main__":
    unittest.main()
