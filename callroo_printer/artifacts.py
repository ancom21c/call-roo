from __future__ import annotations

import json
import os
import tempfile
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
        profile_name: str | None = None,
    ) -> tuple[str, ...]:
        fortunes: list[str] = []
        for job_dir in sorted(self.jobs_dir.iterdir(), reverse=True):
            if not job_dir.is_dir():
                continue
            if exclude_root is not None and job_dir == exclude_root:
                continue
            if profile_name is not None:
                job_profile_name = _load_job_profile_name(job_dir)
                if job_profile_name != profile_name:
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
        _atomic_write_text(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
        return path

    def write_text(self, filename: str, text: str) -> Path:
        path = self.root / filename
        _atomic_write_text(path, text)
        return path

    def write_bytes(self, filename: str, payload: bytes) -> Path:
        path = self.root / filename
        _atomic_write_bytes(path, payload)
        return path

    def save_image(self, filename: str, image: Image.Image) -> Path:
        path = self.root / filename
        _atomic_save_image(path, image)
        return path

    def write_result(self, **payload: Any) -> Path:
        return self.write_json("result.json", dict(payload))


def _load_job_profile_name(job_dir: Path) -> str | None:
    result_payload = _load_json_file(job_dir / "result.json")
    profile_name = result_payload.get("llm_profile_name")
    if isinstance(profile_name, str) and profile_name.strip():
        return profile_name.strip()

    selected_payload = _load_json_file(job_dir / "selected-llm-profile.json")
    profile_name = selected_payload.get("profile_name")
    if isinstance(profile_name, str) and profile_name.strip():
        return profile_name.strip()
    return None


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    except Exception:
        _unlink_if_exists(tmp_path)
        raise


def _atomic_save_image(path: Path, image: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.",
        suffix=path.suffix or ".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        tmp_path = Path(handle.name)

    try:
        image.save(tmp_path)
        tmp_path.replace(path)
    except Exception:
        _unlink_if_exists(tmp_path)
        raise


def _unlink_if_exists(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
