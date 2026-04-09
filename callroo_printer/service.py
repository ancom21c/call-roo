from __future__ import annotations

import logging
import random
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from callroo_printer.audio import LoopingWavePlayer, OneShotWavePlayer
from callroo_printer.artifacts import ArtifactManager, JobArtifacts
from callroo_printer.config import AppConfig, LLMProfileConfig
from callroo_printer.input_sources import TriggerEvent, TriggerSourceMonitor
from callroo_printer.layout import compose_ticket
from callroo_printer.llm_client import LLMCallResult, OpenAICompatClient, sanitize_text
from callroo_printer.printer import create_printer

LOGGER = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
RECENT_FORTUNE_HISTORY = 6
FORTUNE_VARIATION_PROFILES = (
    "편의점 냉장고 불빛, 건조하고 선명한 어조, 사소한 타이밍의 행운",
    "엘리베이터 문틈, 무심하지만 정확한 어조, 작은 결심의 징조",
    "주전자 김, 따뜻하지만 군더더기 없는 어조, 미뤘던 일의 실마리",
    "지하철 손잡이, 약간 장난스러운 어조, 뜻밖의 연락 한 번",
    "동전과 영수증, 낮고 차분한 어조, 작지만 분명한 이득",
    "세탁기 회전음, 기계적인 리듬, 묵은 고민이 풀리는 조짐",
    "복도 형광등, 서늘하고 또렷한 어조, 오늘의 선택 하나를 밀어주는 운",
    "자판기 버튼, 짧고 단단한 리듬, 우연한 발견의 예감",
    "계단참 창문, 담백한 어조, 방향을 바꾸면 열리는 하루",
    "우산 끝 물방울, 느리고 조용한 어조, 늦게 도착하는 좋은 소식",
)

GIGACHAD_VARIATION_PROFILES = (
    "벽 앞에서 멈추지 않는 어조, 오늘 바로 실행할 한 가지 행동",
    "핑계를 끊어내는 리듬, 짧고 단단한 command tone",
    "자기 통제와 기강, 몸을 먼저 움직이게 하는 조언",
    "겁을 인정하되 밀어붙이는 어조, 한 걸음 전진",
    "루틴과 discipline, 지금 당장 가능한 행동 지시",
    "차갑지만 신뢰 가는 어조, 변명보다 action",
)


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
        self.audio_player = LoopingWavePlayer(
            config.audio.clip_file,
            volume=config.audio.clip_volume,
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
        keepalive_thread: threading.Thread | None = None
        try:
            self._prime_event_audio()
            if not self.dry_run:
                self._wait_for_printer_ready()
                if self.printer.keepalive_supported:
                    keepalive_thread = threading.Thread(
                        target=self._keepalive_loop,
                        name="bluetooth-keepalive",
                        daemon=True,
                    )
                    keepalive_thread.start()
                    LOGGER.info("Bluetooth keep-alive started.")

            if self.audio_player.prime():
                LOGGER.info("Prepared looping audio clip %s", self.audio_player.clip_path)

            input_monitor = TriggerSourceMonitor(self.config.input)
            active_inputs = input_monitor.start()
            LOGGER.info("Active trigger inputs: %s", ", ".join(active_inputs))
            LOGGER.info(self._trigger_instructions(active_inputs))

            while True:
                trigger = input_monitor.next_trigger(timeout_seconds=0.25)
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
            self.audio_player.close()
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

        audio_started = self.audio_player.start()
        play_completion_sound = False
        if audio_started:
            LOGGER.info("Looping audio playback started from %s", self.audio_player.clip_path)

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
                variation_hint=self._select_variation_hint(llm_profile),
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
            if audio_started:
                self.audio_player.stop()
            if play_completion_sound:
                self._play_event_sound("print_completed", delay_seconds=1.0)

    def _generate_fortune(
        self,
        job: JobArtifacts,
        *,
        profile: LLMProfileConfig,
        current_time_hint: str | None = None,
        variation_hint: str | None = None,
    ) -> tuple[str, str | None, bool]:
        result = self.clients[profile.name].generate_fortune(
            max_chars=self.config.layout.max_fortune_chars,
            current_time_hint=current_time_hint,
            variation_hint=variation_hint,
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
        threshold = 1 if startup else self.config.bluetooth.adapter_reset_after_failures
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

        adapter_name = self.config.bluetooth.adapter_name
        LOGGER.warning(
            "Resetting Bluetooth adapter %s after %s.",
            adapter_name,
            context,
        )
        self.printer.close()
        self._last_bluetooth_reset_at = now
        try:
            _reset_bluetooth_adapter(adapter_name)
        except Exception as exc:
            LOGGER.warning("Bluetooth adapter reset failed: %s", exc)
        else:
            LOGGER.info("Bluetooth adapter %s reset complete.", adapter_name)
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

    @staticmethod
    def _select_variation_hint(profile: LLMProfileConfig) -> str | None:
        if profile.variation_hints:
            return random.choice(list(profile.variation_hints))
        if profile.name.casefold() == "gigachad":
            return random.choice(GIGACHAD_VARIATION_PROFILES)
        return random.choice(FORTUNE_VARIATION_PROFILES)

    def _trigger_instructions(self, active_inputs: list[str]) -> str:
        action = "generate artifacts" if self.dry_run else "print"
        has_stdin = any(source == "stdin" for source in active_inputs)
        has_linux_input = any(source.startswith("linux-input:") for source in active_inputs)

        if has_stdin and has_linux_input:
            return (
                f"Press Enter in this terminal or use the configured Linux input device to {action}. "
                "Stop with Ctrl+C."
            )
        if has_stdin:
            return f"Press Enter in this terminal to {action}. Stop with Ctrl+C."
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


def _reset_bluetooth_adapter(adapter_name: str) -> None:
    try:
        _run_checked_command(["sudo", "-n", "hciconfig", adapter_name, "reset"])
    except RuntimeError:
        _run_checked_command(["sudo", "-n", "hciconfig", adapter_name, "down"])
        _run_checked_command(["sudo", "-n", "rfkill", "unblock", "bluetooth"])
        _run_checked_command(["sudo", "-n", "hciconfig", adapter_name, "up"])
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
