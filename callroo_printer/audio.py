from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Callable, Protocol

LOGGER = logging.getLogger(__name__)
_APLAY_DEVICE_LINE = re.compile(
    r"^\s*card\s+\d+:\s+(?P<card>[^\[]+?)\s+\[(?P<card_desc>[^\]]+)\],\s+"
    r"device\s+(?P<device>\d+):\s+(?P<device_name>[^\[]+?)\s+\[(?P<device_desc>[^\]]+)\]"
)


class _PlayableProcess(Protocol):
    def wait(self, timeout: float | None = None) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class LoopingWavePlayer:
    def __init__(
        self,
        clip_path: Path,
        volume: float = 1.0,
        device: str | None = None,
        runner: Callable[[list[str]], _PlayableProcess] | None = None,
    ):
        self.clip_path = clip_path
        self.volume = volume
        self.device = device
        self._runner = runner or _spawn_process
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: _PlayableProcess | None = None
        self._prepared_clip_path: Path | None = None
        self._commands: list[list[str]] | None = None
        self._warning_emitted = False

    def prime(self) -> bool:
        with self._lock:
            return self._ensure_commands_locked() is not None

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True

            commands = self._ensure_commands_locked()
            if commands is None:
                return False

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop,
                args=(commands,),
                name="clip-loop-player",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            process = self._process
            thread = self._thread

        if process is not None:
            try:
                process.terminate()
            except Exception:
                pass

        if thread is not None:
            thread.join(timeout=1.0)

        with self._lock:
            process = self._process
            if process is not None:
                try:
                    process.kill()
                except Exception:
                    pass
            self._process = None
            self._thread = None

    def close(self) -> None:
        self.stop()
        with self._lock:
            self._cleanup_prepared_clip_locked()
            self._commands = None

    def _loop(self, commands: list[list[str]]) -> None:
        command_index = 0
        while not self._stop_event.is_set():
            command = commands[command_index]
            try:
                process = self._runner(command)
            except Exception as exc:
                LOGGER.warning("Failed to start looping audio playback: %s", exc)
                return

            with self._lock:
                self._process = process

            if self._stop_event.is_set():
                try:
                    process.terminate()
                except Exception:
                    pass
                with self._lock:
                    if self._process is process:
                        self._process = None
                return

            return_code: int | None
            while not self._stop_event.is_set():
                try:
                    return_code = process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    continue
                break
            else:
                return_code = None

            with self._lock:
                if self._process is process:
                    self._process = None

            if self._stop_event.is_set():
                return

            if return_code not in (0, None):
                if command_index + 1 < len(commands):
                    failed_device = _command_device_label(command)
                    next_command = commands[command_index + 1]
                    next_device = _command_device_label(next_command)
                    LOGGER.warning(
                        "Looping audio playback failed on %s with status %s. Retrying on %s.",
                        failed_device,
                        return_code,
                        next_device,
                    )
                    command_index += 1
                    continue
                LOGGER.warning(
                    "Looping audio playback exited unexpectedly with status %s.",
                    return_code,
                )
                return

    def _warn_once(self, message: str) -> bool:
        if self._warning_emitted:
            return False
        LOGGER.warning(message)
        self._warning_emitted = True
        return True

    def _ensure_commands_locked(self) -> list[list[str]] | None:
        if self._commands is not None:
            return [command[:] for command in self._commands]

        if self.volume <= 0.0:
            LOGGER.info("Looping audio playback disabled because audio.clip_volume is 0.0.")
            return None

        self._cleanup_prepared_clip_locked()
        playback_path = self.clip_path
        try:
            playback_path = _prepare_clip_for_aplay(self.clip_path, self.volume)
        except Exception as exc:
            self._warn_once(
                f"Failed to prepare looping audio clip {self.clip_path}: {exc}"
            )
            return None
        if playback_path != self.clip_path:
            self._prepared_clip_path = playback_path

        commands = _resolve_commands(playback_path, self.device)
        if commands is None:
            self._cleanup_prepared_clip_locked()
            return None
        self._commands = [command[:] for command in commands]
        return [command[:] for command in self._commands]

    def _cleanup_prepared_clip_locked(self) -> None:
        if self._prepared_clip_path is None:
            self._commands = None
            return
        try:
            os.unlink(self._prepared_clip_path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            LOGGER.debug("Failed to remove temporary audio clip %s: %s", self._prepared_clip_path, exc)
        finally:
            self._prepared_clip_path = None
            self._commands = None


class OneShotWavePlayer:
    def __init__(
        self,
        clip_path: Path,
        volume: float = 1.0,
        device: str | None = None,
        runner: Callable[[list[str]], _PlayableProcess] | None = None,
    ):
        self.clip_path = clip_path
        self.volume = volume
        self.device = device
        self._runner = runner or _spawn_process
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: _PlayableProcess | None = None
        self._prepared_clip_path: Path | None = None
        self._commands: list[list[str]] | None = None
        self._warning_emitted = False

    def prime(self) -> bool:
        with self._lock:
            return self._ensure_commands_locked() is not None

    def play(self, delay_seconds: float = 0.0) -> bool:
        self.stop()
        with self._lock:
            commands = self._ensure_commands_locked()
            if commands is None:
                return False

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._play_once,
                args=(commands, max(0.0, delay_seconds)),
                name="clip-one-shot-player",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            process = self._process
            thread = self._thread

        if process is not None:
            try:
                process.terminate()
            except Exception:
                pass

        if thread is not None:
            thread.join(timeout=1.0)

        with self._lock:
            process = self._process
            if process is not None:
                try:
                    process.kill()
                except Exception:
                    pass
            self._process = None
            self._thread = None

    def close(self) -> None:
        self.stop()
        with self._lock:
            self._cleanup_prepared_clip_locked()
            self._commands = None

    def _play_once(self, commands: list[list[str]], delay_seconds: float) -> None:
        if delay_seconds > 0.0 and self._stop_event.wait(delay_seconds):
            return

        for index, command in enumerate(commands):
            try:
                process = self._runner(command)
            except Exception as exc:
                LOGGER.warning("Failed to start one-shot audio playback: %s", exc)
                return

            with self._lock:
                self._process = process

            return_code: int | None
            while not self._stop_event.is_set():
                try:
                    return_code = process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    continue
                break
            else:
                try:
                    process.terminate()
                except Exception:
                    pass
                with self._lock:
                    if self._process is process:
                        self._process = None
                return

            with self._lock:
                if self._process is process:
                    self._process = None

            if return_code in (0, None):
                return
            if index + 1 < len(commands):
                failed_device = _command_device_label(command)
                next_command = commands[index + 1]
                next_device = _command_device_label(next_command)
                LOGGER.warning(
                    "One-shot audio playback failed on %s with status %s. Retrying on %s.",
                    failed_device,
                    return_code,
                    next_device,
                )
                continue
            LOGGER.warning(
                "One-shot audio playback exited unexpectedly with status %s.",
                return_code,
            )
            return

    def _warn_once(self, message: str) -> bool:
        if self._warning_emitted:
            return False
        LOGGER.warning(message)
        self._warning_emitted = True
        return True

    def _ensure_commands_locked(self) -> list[list[str]] | None:
        if self._commands is not None:
            return [command[:] for command in self._commands]

        if self.volume <= 0.0:
            LOGGER.info("One-shot audio playback disabled because audio.event_volume is 0.0.")
            return None

        self._cleanup_prepared_clip_locked()
        playback_path = self.clip_path
        try:
            playback_path = _prepare_clip_for_aplay(self.clip_path, self.volume)
        except Exception as exc:
            self._warn_once(
                f"Failed to prepare one-shot audio clip {self.clip_path}: {exc}"
            )
            return None
        if playback_path != self.clip_path:
            self._prepared_clip_path = playback_path

        commands = _resolve_commands(playback_path, self.device)
        if commands is None:
            self._cleanup_prepared_clip_locked()
            return None
        self._commands = [command[:] for command in commands]
        return [command[:] for command in self._commands]

    def _cleanup_prepared_clip_locked(self) -> None:
        if self._prepared_clip_path is None:
            self._commands = None
            return
        try:
            os.unlink(self._prepared_clip_path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            LOGGER.debug("Failed to remove temporary audio clip %s: %s", self._prepared_clip_path, exc)
        finally:
            self._prepared_clip_path = None
            self._commands = None


def _resolve_commands(clip_path: Path, device: str | None = None) -> list[list[str]] | None:
    if not clip_path.exists():
        LOGGER.warning("Looping audio clip not found: %s", clip_path)
        return None

    aplay_path = shutil.which("aplay")
    if aplay_path is None:
        LOGGER.warning("Looping audio playback skipped because 'aplay' was not found.")
        return None

    device_candidates = [device] if device else _detect_aplay_device_candidates(aplay_path)
    if not device_candidates:
        device_candidates = [None]
    elif device is None:
        LOGGER.info("Auto-selected ALSA playback device %s", device_candidates[0])

    commands: list[list[str]] = []
    seen: set[str | None] = set()
    for candidate in [*device_candidates, None]:
        if candidate in seen:
            continue
        seen.add(candidate)
        command = [aplay_path, "-q"]
        if candidate:
            command.extend(["-D", candidate])
        command.append(str(clip_path))
        commands.append(command)
    return commands


def _prepare_clip_for_aplay(clip_path: Path, volume: float) -> Path:
    suffix = clip_path.suffix.lower()
    if suffix == ".wav":
        if volume == 1.0:
            return clip_path
        return _render_volume_adjusted_clip(clip_path, volume)
    return _transcode_audio_to_wav(clip_path, volume)


def _detect_aplay_device_candidates(aplay_path: str) -> list[str]:
    try:
        result = subprocess.run(
            [aplay_path, "-l"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        LOGGER.debug("Failed to inspect ALSA playback devices: %s", exc)
        return []

    if result.returncode != 0:
        LOGGER.debug("Failed to list ALSA playback devices: %s", result.stderr.strip())
        return []
    return _parse_aplay_device_candidates(result.stdout)


def _parse_aplay_device_candidates(output: str) -> list[str]:
    scored_candidates: list[tuple[int, str, str]] = []
    seen: set[str] = set()

    for line in output.splitlines():
        match = _APLAY_DEVICE_LINE.match(line)
        if not match:
            continue

        card = match.group("card").strip()
        device = match.group("device").strip()
        text = " ".join(
            (
                card,
                match.group("card_desc").strip(),
                match.group("device_name").strip(),
                match.group("device_desc").strip(),
            )
        ).lower()
        candidate = f"plughw:CARD={card},DEV={device}"
        if candidate in seen:
            continue
        seen.add(candidate)
        scored_candidates.append((_alsa_device_score(text), text, candidate))

    scored_candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return [candidate for _, _, candidate in scored_candidates]


def _alsa_device_score(text: str) -> int:
    score = 100
    if any(token in text for token in ("usb", "dac", "uac", "external")):
        score -= 50
    if any(token in text for token in ("headphone", "headphones", "speaker", "analog", "line out", "lineout")):
        score -= 20
    if any(token in text for token in ("bcm2835", "onboard", "builtin", "built-in")):
        score += 10
    if any(token in text for token in ("hdmi", "display", "iec958", "spdif")):
        score += 40
    if any(token in text for token in ("loopback", "dummy", "null")):
        score += 80
    return score


def _command_device_label(command: list[str]) -> str:
    if "-D" in command:
        index = command.index("-D")
        if index + 1 < len(command):
            return command[index + 1]
    return "default ALSA device"


def _spawn_process(command: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _transcode_audio_to_wav(clip_path: Path, volume: float) -> Path:
    if not clip_path.exists():
        raise FileNotFoundError(clip_path)

    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        raise RuntimeError(
            f"ffmpeg is required to play non-WAV clips such as {clip_path.name}"
        )

    with tempfile.NamedTemporaryFile(
        prefix="callroo-clip-",
        suffix=".wav",
        delete=False,
    ) as tmp:
        temp_path = Path(tmp.name)

    command = [
        ffmpeg_path,
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(clip_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
    ]
    if volume != 1.0:
        command.extend(["-filter:a", f"volume={volume:g}"])
    command.append(str(temp_path))

    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise

    if result.returncode != 0:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise RuntimeError(f"ffmpeg failed to decode {clip_path.name}: {detail}")

    return temp_path


def _render_volume_adjusted_clip(clip_path: Path, volume: float) -> Path:
    if not clip_path.exists():
        raise FileNotFoundError(clip_path)

    with wave.open(str(clip_path), "rb") as source:
        with tempfile.NamedTemporaryFile(
            prefix="callroo-clip-",
            suffix=".wav",
            delete=False,
        ) as tmp:
            temp_path = Path(tmp.name)

        try:
            with wave.open(str(temp_path), "wb") as target:
                target.setparams(source.getparams())
                while True:
                    frames = source.readframes(4096)
                    if not frames:
                        break
                    target.writeframes(_scale_pcm_frames(frames, source.getsampwidth(), volume))
        except Exception:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            raise

    return temp_path


def _scale_pcm_frames(frames: bytes, sample_width: int, volume: float) -> bytes:
    if sample_width == 1:
        output = bytearray(len(frames))
        for index, raw in enumerate(frames):
            signed = raw - 128
            scaled = _clamp_sample(int(round(signed * volume)), bits=8)
            output[index] = scaled + 128
        return bytes(output)

    if sample_width not in {2, 3, 4}:
        raise RuntimeError(f"Unsupported WAV sample width for volume scaling: {sample_width}")

    output = bytearray(len(frames))
    for offset in range(0, len(frames), sample_width):
        chunk = frames[offset : offset + sample_width]
        if len(chunk) < sample_width:
            break

        sample = _decode_signed_sample(chunk, sample_width)
        bits = sample_width * 8
        scaled = _clamp_sample(int(round(sample * volume)), bits=bits)
        output[offset : offset + sample_width] = _encode_signed_sample(scaled, sample_width)
    return bytes(output)


def _clamp_sample(value: int, bits: int) -> int:
    minimum = -(1 << (bits - 1))
    maximum = (1 << (bits - 1)) - 1
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def _decode_signed_sample(chunk: bytes, sample_width: int) -> int:
    if sample_width == 3:
        sign_byte = b"\xff" if chunk[-1] & 0x80 else b"\x00"
        return int.from_bytes(chunk + sign_byte, byteorder="little", signed=True)
    return int.from_bytes(chunk, byteorder="little", signed=True)


def _encode_signed_sample(value: int, sample_width: int) -> bytes:
    if sample_width == 3:
        return value.to_bytes(4, byteorder="little", signed=True)[:3]
    return value.to_bytes(sample_width, byteorder="little", signed=True)
