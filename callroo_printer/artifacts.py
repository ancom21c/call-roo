from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image


class ArtifactManager:
    def __init__(self, output_root: Path):
        self.output_root = output_root
        self.jobs_dir = self.output_root / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def create_job(
        self,
        triggered_at: datetime,
        raw_input: str,
        dry_run: bool,
        trigger_source: str | None = None,
        trigger_details: dict[str, Any] | None = None,
    ) -> "JobArtifacts":
        job_id = f"{triggered_at.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        root = self.jobs_dir / job_id
        root.mkdir(parents=True, exist_ok=True)

        input_payload: dict[str, Any] = {
            "triggered_at": triggered_at.isoformat(),
            "raw_input": raw_input,
            "dry_run": dry_run,
        }
        if trigger_source is not None:
            input_payload["trigger_source"] = trigger_source
        if trigger_details:
            input_payload["trigger_details"] = trigger_details

        job = JobArtifacts(root=root)
        job.write_json("input.json", input_payload)
        return job

    def recent_fortunes(
        self,
        limit: int,
        *,
        exclude_root: Path | None = None,
    ) -> tuple[str, ...]:
        fortunes: list[str] = []
        for job_dir in sorted(self.jobs_dir.iterdir(), reverse=True):
            if not job_dir.is_dir():
                continue
            if exclude_root is not None and job_dir == exclude_root:
                continue
            fortune_path = job_dir / "fortune.txt"
            if not fortune_path.is_file():
                continue
            text = fortune_path.read_text(encoding="utf-8").strip()
            if not text:
                continue
            fortunes.append(text)
            if len(fortunes) >= limit:
                break
        return tuple(fortunes)


@dataclass(frozen=True)
class JobArtifacts:
    root: Path

    def write_json(self, filename: str, payload: dict[str, Any]) -> Path:
        path = self.root / filename
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def write_text(self, filename: str, text: str) -> Path:
        path = self.root / filename
        path.write_text(text, encoding="utf-8")
        return path

    def write_bytes(self, filename: str, payload: bytes) -> Path:
        path = self.root / filename
        path.write_bytes(payload)
        return path

    def save_image(self, filename: str, image: Image.Image) -> Path:
        path = self.root / filename
        image.save(path)
        return path

    def write_result(self, **payload: Any) -> Path:
        return self.write_json("result.json", dict(payload))
