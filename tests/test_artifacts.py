from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image

from callroo_printer.artifacts import ArtifactManager


class ArtifactManagerTest(unittest.TestCase):
    def test_create_job_writes_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = ArtifactManager(Path(tmp))
            job = manager.create_job(
                triggered_at=datetime(2026, 4, 1, 20, 0, 0),
                raw_input="\n",
                dry_run=True,
            )
            job.write_json("meta.json", {"status": "ok"})
            job.write_text("fortune.txt", "봄빛이 머문다\n")
            job.write_bytes("print-job.bin", b"\x1b@")
            job.save_image("composed-ticket.png", Image.new("L", (8, 8), color=255))

            self.assertTrue((job.root / "input.json").exists())
            self.assertTrue((job.root / "meta.json").exists())
            self.assertTrue((job.root / "fortune.txt").exists())
            self.assertTrue((job.root / "print-job.bin").exists())
            self.assertTrue((job.root / "composed-ticket.png").exists())

            input_payload = json.loads((job.root / "input.json").read_text("utf-8"))
            self.assertEqual(input_payload["raw_input"], "\n")
            self.assertTrue(input_payload["dry_run"])

    def test_create_job_writes_trigger_metadata_when_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = ArtifactManager(Path(tmp))
            job = manager.create_job(
                triggered_at=datetime(2026, 4, 1, 20, 0, 0),
                raw_input="\n",
                dry_run=True,
                trigger_source="linux-input",
                trigger_details={
                    "device_path": "/dev/input/by-id/usb-INSTANT_USB_Keyboard-event-kbd",
                    "key_code": 28,
                    "key_name": "KEY_ENTER",
                },
            )

            input_payload = json.loads((job.root / "input.json").read_text("utf-8"))
            self.assertEqual(input_payload["trigger_source"], "linux-input")
            self.assertEqual(input_payload["trigger_details"]["key_code"], 28)
            self.assertEqual(input_payload["trigger_details"]["key_name"], "KEY_ENTER")

    def test_recent_fortunes_returns_latest_non_empty_texts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = ArtifactManager(Path(tmp))
            first = manager.create_job(
                triggered_at=datetime(2026, 4, 1, 20, 0, 0),
                raw_input="\n",
                dry_run=True,
            )
            second = manager.create_job(
                triggered_at=datetime(2026, 4, 1, 20, 1, 0),
                raw_input="\n",
                dry_run=True,
            )
            third = manager.create_job(
                triggered_at=datetime(2026, 4, 1, 20, 2, 0),
                raw_input="\n",
                dry_run=True,
            )

            first.write_text("fortune.txt", "첫 번째 운세\n")
            second.write_text("fortune.txt", "\n")
            third.write_text("fortune.txt", "세 번째 운세\n")

            self.assertEqual(
                manager.recent_fortunes(2),
                ("세 번째 운세", "첫 번째 운세"),
            )
            self.assertEqual(
                manager.recent_fortunes(2, exclude_root=third.root),
                ("첫 번째 운세",),
            )


if __name__ == "__main__":
    unittest.main()
