from __future__ import annotations

import json
import logging
import queue
import random
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from callroo_printer.audio import LoopingWavePlayer, OneShotWavePlayer
from callroo_printer.artifacts import ArtifactManager, JobArtifacts
from callroo_printer.config import (
    AppConfig,
    BluetoothConfig,
    LLMProfileConfig,
    WeightedAudioFileConfig,
)
from callroo_printer.input_sources import TriggerEvent, TriggerSourceMonitor
from callroo_printer.layout import compose_ticket
from callroo_printer.llm_client import LLMCallResult, OpenAICompatClient, sanitize_text
from callroo_printer.printer import create_printer, resolve_bluetooth_adapter_names

LOGGER = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
RECENT_FORTUNE_HISTORY = 6


class FortunePrinterService:
    def __init__(self, config: AppConfig, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.clients = {
            profile.name: OpenAICompatClient(profile)
            for profile in config.llm.profiles
        }
        self.printer = create_printer(config.bluetooth)
        self.artifacts = ArtifactManager(config.output.outputs_dir)
        self._launch_sound_players = _build_launch_sound_players(
            config.audio.launch_sounds,
            volume=config.audio.launch_sound_volume,
            device=config.audio.aplay_device,
        )
        self._event_audio_players = {
            "printer_connected": _build_event_player(
                config.audio.printer_connected_file,
                volume=config.audio.event_volume,
                device=config.audio.aplay_device,
            ),
            "printer_failed": _build_event_player(
                config.audio.printer_failed_file,
                volume=config.audio.event_volume,
                device=config.audio.aplay_device,
            ),
            "print_completed": _build_event_player(
                config.audio.print_completed_file,
                volume=config.audio.event_volume,
                device=config.audio.aplay_device,
            ),
        }
        self._stop_event = threading.Event()
        self._cooldown_until = 0.0
        self._consecutive_bluetooth_failures = 0
        self._last_bluetooth_reset_at = 0.0
        self._printer_failure_announced = False
        self._printer_connected_once = False

    def run(self) -> None:
        LOGGER.info("Discovered %s asset(s)", len(self._discover_assets(self.config.assets_dir)))
        input_monitor: TriggerSourceMonitor | None = None
        dashboard_monitor: DashboardTriggerMonitor | None = None
        keepalive_thread: threading.Thread | None = None
        try:
            self._prime_event_audio()
            self._prime_launch_sounds()
            dashboard_monitor = DashboardTriggerMonitor(
                self.config.output.outputs_dir / "dashboard-triggers.jsonl"
            )
            dashboard_monitor.start()
            if not self.dry_run:
                _disable_configured_bluetooth_adapters(self.config.bluetooth)
                self._wait_for_printer_ready()
                if self.printer.keepalive_supported:
                    keepalive_thread = threading.Thread(
                        target=self._keepalive_loop,
                        name="bluetooth-keepalive",
                        daemon=True,
                    )
                    keepalive_thread.start()
                    LOGGER.info("Bluetooth keep-alive started.")

            active_inputs = ["dashboard"]
            input_monitor = TriggerSourceMonitor(self.config.input)
            try:
                active_inputs.extend(input_monitor.start())
            except RuntimeError as exc:
                LOGGER.warning("Configured trigger inputs are unavailable: %s", exc)
            LOGGER.info("Active trigger inputs: %s", ", ".join(active_inputs))
            LOGGER.info(self._trigger_instructions(active_inputs))

            while True:
                trigger = input_monitor.next_trigger(timeout_seconds=0.25)
                if trigger is None and dashboard_monitor is not None:
                    trigger = dashboard_monitor.next_trigger(timeout_seconds=0.0)
                if trigger is None:
                    continue
                LOGGER.info("Trigger received from %s", self._format_trigger(trigger))
                self._handle_trigger(trigger)
        except KeyboardInterrupt:
            LOGGER.info("Shutdown requested, closing service.")
        finally:
            self._stop_event.set()
            if input_monitor is not None:
                input_monitor.close()
            if dashboard_monitor is not None:
                dashboard_monitor.close()
            for _, player in self._launch_sound_players:
                player.close()
            for player in self._event_audio_players.values():
                if player is not None:
                    player.close()
            if keepalive_thread is not None:
                keepalive_thread.join(timeout=2.0)
            self.printer.close()

    def _wait_for_printer_ready(self) -> None:
        LOGGER.info("Waiting for printer connection before enabling triggers.")
        retry_delay = self.config.bluetooth.reconnect_delay_seconds
        while True:
            try:
                self.printer.connect_if_needed()
            except Exception as exc:
                failure_count = self._record_bluetooth_failure(startup=True)
                LOGGER.warning(
                    "Printer connection failed: %s. Consecutive Bluetooth failures: %s. Retrying in %.1f seconds.",
                    exc,
                    failure_count,
                    retry_delay,
                )
                time.sleep(retry_delay)
                continue

            self._note_bluetooth_success()
            LOGGER.info("Printer connection ready.")
            return

    def _handle_trigger(self, trigger: TriggerEvent) -> None:
        triggered_at = datetime.now().astimezone()
        now = time.monotonic()
        if now < self._cooldown_until:
            job = self.artifacts.create_job(
                triggered_at=triggered_at,
                raw_input=trigger.raw_input,
                dry_run=self.dry_run,
                trigger_source=trigger.source,
                trigger_details=trigger.details,
            )
            remaining = int(self._cooldown_until - now)
            LOGGER.info("Cooldown active. Wait %s more second(s).", remaining)
            job.write_result(
                status="cooldown_rejected",
                triggered_at=triggered_at.isoformat(),
                remaining_seconds=remaining,
            )
            return

        if self.config.cooldown_on_trigger:
            self._cooldown_until = now + self.config.cooldown_seconds

        launch_sound_player = self._select_launch_sound_player()
        audio_started = launch_sound_player.start() if launch_sound_player is not None else False
        play_completion_sound = False
        if audio_started and launch_sound_player is not None:
            LOGGER.info(
                "Launch sound playback started from %s",
                launch_sound_player.clip_path,
            )

        job: JobArtifacts | None = None
        try:
            job = self.artifacts.create_job(
                triggered_at=triggered_at,
                raw_input=trigger.raw_input,
                dry_run=self.dry_run,
                trigger_source=trigger.source,
                trigger_details=trigger.details,
            )
            LOGGER.info("Generating fortune ticket in %s", job.root)
            llm_profile = self._select_llm_profile()
            job.write_json(
                "selected-llm-profile.json",
                {
                    "profile_name": llm_profile.name,
                    "profile_weight": llm_profile.weight,
                    "models": [
                        {
                            "name": model.name,
                            "endpoint": model.endpoint,
                            "model": model.model,
                            "api_key_configured": bool(model.api_key),
                            "api_key_env": model.api_key_env or "",
                        }
                        for model in llm_profile.models
                    ],
                    "tags": sorted(llm_profile.tags.keys()),
                },
            )

            fortune, selected_tag, used_fallback = self._generate_fortune(
                job,
                profile=llm_profile,
                current_time_hint=_format_llm_time_hint(
                    triggered_at,
                    llm_profile.current_time_hint_format,
                ),
            )
            asset_path = self._select_asset_for_profile(
                llm_profile,
                selected_tag=selected_tag,
            )
            job.write_json(
                "selected-asset.json",
                {
                    "asset_path": str(asset_path),
                    "asset_name": asset_path.name,
                    "profile_name": llm_profile.name,
                    "selected_tag": selected_tag,
                },
            )
            ticket = compose_ticket(
                asset_path=asset_path,
                fortune_text=fortune,
                printed_at=triggered_at,
                config=self.config.layout,
                fortune_tag=selected_tag,
            )
            ticket_path = job.save_image("composed-ticket.png", ticket)

            for artifact in self.printer.build_artifacts(
                image_path=ticket_path,
                image=ticket,
                threshold=self.config.layout.threshold,
                trailing_feed_lines=self.config.trailing_feed_lines,
            ):
                job.write_bytes(artifact.filename, artifact.payload)

            if self.dry_run:
                status = "dry_run_completed"
                LOGGER.info("Dry run complete. Inspect %s", job.root)
            else:
                self.printer.print_saved_image(
                    image_path=ticket_path,
                    image=ticket,
                    threshold=self.config.layout.threshold,
                    trailing_feed_lines=self.config.trailing_feed_lines,
                )
                status = "printed"
                play_completion_sound = True
                LOGGER.info("Printed ticket using asset %s", asset_path.name)

            if not self.config.cooldown_on_trigger:
                self._cooldown_until = time.monotonic() + self.config.cooldown_seconds
            job.write_result(
                status=status,
                triggered_at=triggered_at.isoformat(),
                asset_path=str(asset_path),
                llm_profile_name=llm_profile.name,
                selected_tag=selected_tag,
                used_fallback=used_fallback,
                dry_run=self.dry_run,
            )
        except Exception as exc:
            if job is not None:
                job.write_result(
                    status="failed",
                    triggered_at=triggered_at.isoformat(),
                    error=str(exc),
                    dry_run=self.dry_run,
                )
            LOGGER.exception("Failed to generate or print ticket")
        finally:
            if audio_started and launch_sound_player is not None:
                launch_sound_player.stop()
            if play_completion_sound:
                self._play_event_sound("print_completed", delay_seconds=1.0)

    def _generate_fortune(
        self,
        job: JobArtifacts,
        *,
        profile: LLMProfileConfig,
        current_time_hint: str | None = None,
    ) -> tuple[str, str | None, bool]:
        result = self.clients[profile.name].generate_fortune(
            max_chars=self.config.layout.max_fortune_chars,
            current_time_hint=current_time_hint,
            allowed_tags=tuple(profile.tags.keys()),
            recent_fortunes=self.artifacts.recent_fortunes(
                RECENT_FORTUNE_HISTORY,
                exclude_root=job.root,
                profile_name=profile.name,
            ),
        )
        self._write_llm_artifacts(job, result)
        selected_tag = self._resolve_selected_tag(profile, result.tag)
        if selected_tag:
            job.write_text("tag.txt", selected_tag + "\n")
        if result.error:
            LOGGER.warning("LLM call failed, using fallback text: %s", result.error)
            fallback = sanitize_text(
                profile.fallback_text,
                max_chars=self.config.layout.max_fortune_chars,
            )
            job.write_text("fortune.txt", fallback + "\n")
            return fallback, selected_tag, True
        job.write_text("fortune.txt", result.text + "\n")
        return result.text, selected_tag, False

    def _keepalive_loop(self) -> None:
        interval = self.config.bluetooth.keepalive_interval_seconds
        while not self._stop_event.is_set():
            try:
                self.printer.keep_alive()
                self._note_bluetooth_success()
                LOGGER.debug("Bluetooth keep-alive sent")
            except Exception as exc:
                failure_count = self._record_bluetooth_failure()
                LOGGER.warning(
                    "Bluetooth keep-alive failed: %s. Consecutive Bluetooth failures: %s. Retrying in %.1f seconds.",
                    exc,
                    failure_count,
                    self.config.bluetooth.reconnect_delay_seconds,
                )
                self.printer.close()
                if self._stop_event.wait(
                    self.config.bluetooth.reconnect_delay_seconds
                ):
                    return
                continue
            if self._stop_event.wait(interval):
                return

    def _note_bluetooth_success(self) -> None:
        self._consecutive_bluetooth_failures = 0
        should_play_connected_sound = (
            not self._printer_connected_once or self._printer_failure_announced
        )
        self._printer_connected_once = True
        self._printer_failure_announced = False
        if should_play_connected_sound:
            self._play_event_sound("printer_connected")

    def _record_bluetooth_failure(self, *, startup: bool = False) -> int:
        if not self._printer_failure_announced:
            self._printer_failure_announced = True
            self._play_event_sound("printer_failed")
        self._consecutive_bluetooth_failures += 1
        failure_count = self._consecutive_bluetooth_failures
        threshold = self.config.bluetooth.adapter_reset_after_failures
        self._maybe_reset_bluetooth_adapter(
            failure_count,
            threshold=threshold,
            startup=startup,
        )
        return failure_count

    def _maybe_reset_bluetooth_adapter(
        self,
        failure_count: int,
        *,
        threshold: int | None = None,
        startup: bool = False,
    ) -> None:
        if threshold is None:
            threshold = self.config.bluetooth.adapter_reset_after_failures
        if failure_count < threshold:
            return

        now = time.monotonic()
        cooldown = self.config.bluetooth.adapter_reset_cooldown_seconds
        context = (
            "startup connection failure"
            if startup
            else f"{failure_count} consecutive failures"
        )
        if self._last_bluetooth_reset_at > 0.0:
            remaining = cooldown - (now - self._last_bluetooth_reset_at)
            if remaining > 0:
                LOGGER.warning(
                    "Bluetooth adapter reset deferred for %.1f more seconds after %s.",
                    remaining,
                    context,
                )
                return

        adapter_names = resolve_bluetooth_adapter_names(self.config.bluetooth)
        if not adapter_names:
            configured = tuple(
                name
                for name in (
                    part.strip()
                    for part in self.config.bluetooth.adapter_name.split(",")
                )
                if name and name.lower() != "auto"
            )
            adapter_names = configured or ("hci0",)
        LOGGER.warning(
            "Resetting Bluetooth adapter(s) %s after %s.",
            ", ".join(adapter_names),
            context,
        )
        self.printer.close()
        self._last_bluetooth_reset_at = now
        try:
            _reset_bluetooth_adapters(adapter_names)
        except Exception as exc:
            LOGGER.warning("Bluetooth adapter reset failed: %s", exc)
        else:
            LOGGER.info(
                "Bluetooth adapter reset complete for %s.",
                ", ".join(adapter_names),
            )
        self._consecutive_bluetooth_failures = 0

    @staticmethod
    def _write_llm_artifacts(job: JobArtifacts, result: LLMCallResult) -> None:
        job.write_json(
            "llm-request.json",
            {
                "url": result.request_url,
                "payload": result.request_payload,
            },
        )
        if result.response_payload is not None:
            job.write_json("llm-response.json", result.response_payload)
        if result.raw_text is not None:
            job.write_text("llm-raw.txt", result.raw_text + "\n")
        if result.parsed_json is not None:
            job.write_json("llm-parsed.json", result.parsed_json)
        if result.tag is not None:
            job.write_json("llm-tag.json", {"tag": result.tag})
        if result.model_name is not None:
            job.write_json("llm-model.json", {"name": result.model_name})
        if result.attempts:
            job.write_json("llm-attempts.json", list(result.attempts))
        if result.error:
            job.write_json("llm-error.json", {"error": result.error})

    @staticmethod
    def _discover_assets(assets_dir: Path) -> list[Path]:
        assets = sorted(
            path
            for path in assets_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )
        if not assets:
            raise RuntimeError(f"No printable assets found in {assets_dir}")
        return assets

    def _select_llm_profile(self) -> LLMProfileConfig:
        weighted_profiles = [
            profile for profile in self.config.llm.profiles if profile.weight > 0.0
        ]
        if not weighted_profiles:
            return random.choice(list(self.config.llm.profiles))
        return random.choices(
            weighted_profiles,
            weights=[profile.weight for profile in weighted_profiles],
            k=1,
        )[0]

    def _select_launch_sound_player(self) -> LoopingWavePlayer | None:
        weighted_players = [
            (launch_sound.weight, player)
            for launch_sound, player in self._launch_sound_players
            if launch_sound.weight > 0.0 and player.prime()
        ]
        if not weighted_players:
            return None
        if len(weighted_players) == 1:
            return weighted_players[0][1]
        return random.choices(
            [player for _, player in weighted_players],
            weights=[weight for weight, _ in weighted_players],
            k=1,
        )[0]

    @staticmethod
    def _resolve_selected_tag(
        profile: LLMProfileConfig,
        selected_tag: str | None,
    ) -> str | None:
        if selected_tag:
            return selected_tag
        if profile.tags:
            return random.choice(list(profile.tags.keys()))
        return None

    def _select_asset_for_profile(
        self,
        profile: LLMProfileConfig,
        *,
        selected_tag: str | None,
    ) -> Path:
        if selected_tag:
            tagged_pool = profile.tags.get(selected_tag, ())
            if tagged_pool:
                return random.choice(list(tagged_pool))
            LOGGER.warning(
                "No tag asset pool configured for tag %s in LLM profile %s. Falling back to all assets.",
                selected_tag,
                profile.name,
            )
        return random.choice(self._discover_assets(self.config.assets_dir))

    def _trigger_instructions(self, active_inputs: list[str]) -> str:
        action = "generate artifacts" if self.dry_run else "print"
        has_stdin = any(source == "stdin" for source in active_inputs)
        has_linux_input = any(source.startswith("linux-input:") for source in active_inputs)
        has_dashboard = any(source == "dashboard" for source in active_inputs)

        if has_stdin and has_linux_input:
            return (
                f"Press Enter in this terminal or use the configured Linux input device to {action}. "
                "Stop with Ctrl+C."
            )
        if has_stdin:
            return f"Press Enter in this terminal to {action}. Stop with Ctrl+C."
        if has_dashboard and not has_linux_input:
            return f"Use the dashboard print button to {action}. Stop with Ctrl+C."
        if has_dashboard:
            return f"Use the dashboard print button or configured Linux input device to {action}. Stop with Ctrl+C."
        return f"Waiting for configured Linux input device to {action}. Stop with Ctrl+C."

    def _prime_event_audio(self) -> None:
        for event_name, player in self._event_audio_players.items():
            if player is None:
                continue
            if player.prime():
                LOGGER.info(
                    "Prepared %s audio clip %s",
                    event_name.replace("_", " "),
                    player.clip_path,
                )

    def _prime_launch_sounds(self) -> None:
        for launch_sound, player in self._launch_sound_players:
            if launch_sound.weight <= 0.0:
                continue
            if player.prime():
                LOGGER.info(
                    "Prepared launch sound %s (weight %.3f)",
                    player.clip_path,
                    launch_sound.weight,
                )

    def _play_event_sound(self, event_name: str, *, delay_seconds: float = 0.0) -> None:
        player = self._event_audio_players.get(event_name)
        if player is None:
            return
        player.play(delay_seconds=delay_seconds)

    @staticmethod
    def _format_trigger(trigger: TriggerEvent) -> str:
        key_name = trigger.details.get("key_name")
        device_path = trigger.details.get("device_path")
        if key_name and device_path:
            return f"{trigger.source} {key_name} via {device_path}"
        return trigger.source


class DashboardTriggerMonitor:
    def __init__(self, trigger_path: Path):
        self.trigger_path = trigger_path
        self.jobs_dir = self.trigger_path.parent / "jobs"
        self._queue: queue.Queue[TriggerEvent] = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen_request_ids: set[str] = set()

    def start(self) -> None:
        self.trigger_path.parent.mkdir(parents=True, exist_ok=True)
        self.trigger_path.touch(exist_ok=True)
        self._thread = threading.Thread(
            target=self._loop,
            name="dashboard-trigger-reader",
            daemon=True,
        )
        self._thread.start()
        LOGGER.info("Monitoring dashboard trigger file %s", self.trigger_path)

    def next_trigger(self, timeout_seconds: float = 0.0) -> TriggerEvent | None:
        try:
            return self._queue.get(timeout=timeout_seconds)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)

    def _loop(self) -> None:
        position = self._enqueue_existing_unprocessed_triggers()
        while not self._stop_event.is_set():
            try:
                with self.trigger_path.open("r", encoding="utf-8", errors="replace") as handle:
                    if position > self.trigger_path.stat().st_size:
                        position = 0
                    handle.seek(position)
                    while not self._stop_event.is_set():
                        line_start = handle.tell()
                        line = handle.readline()
                        if not line:
                            position = handle.tell()
                            break
                        if not line.endswith("\n"):
                            position = line_start
                            break
                        self._enqueue_line(line)
                        position = handle.tell()
            except FileNotFoundError:
                self.trigger_path.touch(exist_ok=True)
                position = 0
            except OSError as exc:
                LOGGER.warning("Dashboard trigger file read failed: %s", exc)
            self._stop_event.wait(0.25)

    def _enqueue_existing_unprocessed_triggers(self) -> int:
        self._seen_request_ids.update(_load_processed_dashboard_request_ids(self.jobs_dir))
        position = 0
        try:
            with self.trigger_path.open("r", encoding="utf-8", errors="replace") as handle:
                while True:
                    line_start = handle.tell()
                    line = handle.readline()
                    if not line:
                        return handle.tell()
                    if not line.endswith("\n"):
                        return line_start
                    self._enqueue_line(line)
                    position = handle.tell()
        except FileNotFoundError:
            self.trigger_path.touch(exist_ok=True)
            return 0
        except OSError as exc:
            LOGGER.warning("Dashboard trigger file bootstrap read failed: %s", exc)
            return position

    def _enqueue_line(self, line: str) -> None:
        event = self._parse_line(line)
        if event is None:
            return
        request_id = str(event.details.get("request_id", "")).strip()
        if request_id:
            if request_id in self._seen_request_ids:
                return
            self._seen_request_ids.add(request_id)
        self._queue.put(event)

    @staticmethod
    def _parse_line(line: str) -> TriggerEvent | None:
        text = line.strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            LOGGER.warning("Ignoring malformed dashboard trigger line.")
            return None
        if not isinstance(payload, dict):
            LOGGER.warning("Ignoring non-object dashboard trigger line.")
            return None
        request_id = str(payload.get("request_id", "")).strip()
        if not request_id:
            LOGGER.warning("Ignoring dashboard trigger line without request_id.")
            return None
        return TriggerEvent(
            raw_input=str(payload.get("raw_input", "\n")),
            source="dashboard",
            details={
                "requested_at": str(payload.get("requested_at", "")),
                "request_id": request_id,
                "note": str(payload.get("note", "")),
            },
        )


def _load_processed_dashboard_request_ids(jobs_dir: Path) -> set[str]:
    if not jobs_dir.is_dir():
        return set()

    request_ids: set[str] = set()
    for job_dir in jobs_dir.iterdir():
        if not job_dir.is_dir():
            continue
        payload = _load_json_object(job_dir / "input.json")
        details = payload.get("trigger_details")
        if not isinstance(details, dict):
            continue
        request_id = details.get("request_id")
        if isinstance(request_id, str) and request_id.strip():
            request_ids.add(request_id.strip())
    return request_ids


def _load_json_object(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _format_llm_time_hint(triggered_at: datetime, format_string: str) -> str:
    return triggered_at.strftime(format_string)


def _build_event_player(
    clip_path: Path | None,
    *,
    volume: float,
    device: str | None,
) -> OneShotWavePlayer | None:
    if clip_path is None:
        return None
    return OneShotWavePlayer(
        clip_path=clip_path,
        volume=volume,
        device=device,
    )


def _build_launch_sound_players(
    launch_sounds: tuple[WeightedAudioFileConfig, ...],
    *,
    volume: float,
    device: str | None,
) -> tuple[tuple[WeightedAudioFileConfig, LoopingWavePlayer], ...]:
    return tuple(
        (
            launch_sound,
            LoopingWavePlayer(
                launch_sound.file,
                volume=volume,
                device=device,
            ),
        )
        for launch_sound in launch_sounds
    )


def _reset_bluetooth_adapter(adapter_name: str) -> None:
    try:
        _run_checked_command(["sudo", "-n", "hciconfig", adapter_name, "reset"])
    except RuntimeError:
        _run_checked_command(["sudo", "-n", "hciconfig", adapter_name, "down"])
        _run_checked_command(["sudo", "-n", "rfkill", "unblock", "bluetooth"])
        _run_checked_command(["sudo", "-n", "hciconfig", adapter_name, "up"])


def _reset_bluetooth_adapters(adapter_names: tuple[str, ...]) -> None:
    for adapter_name in adapter_names:
        _reset_bluetooth_adapter(adapter_name)


def _disable_configured_bluetooth_adapters(config: BluetoothConfig) -> None:
    for adapter_name in config.disabled_adapter_names:
        try:
            _run_checked_command(["sudo", "-n", "hciconfig", adapter_name, "down"])
        except Exception as exc:
            LOGGER.warning(
                "Failed to disable Bluetooth adapter %s: %s",
                adapter_name,
                exc,
            )
        else:
            LOGGER.info("Disabled Bluetooth adapter %s by configuration.", adapter_name)
    time.sleep(2.0)


def _run_checked_command(command: list[str]) -> None:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Command not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Timed out running {' '.join(command)}") from exc

    if result.returncode == 0:
        return

    detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
    raise RuntimeError(f"{' '.join(command)} failed: {detail}")
