from __future__ import annotations

import subprocess
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from callroo_printer.audio import (
    LoopingWavePlayer,
    OneShotWavePlayer,
    _parse_aplay_device_candidates,
    _prepare_clip_for_aplay,
)


class AudioTest(unittest.TestCase):
    def test_looping_wave_player_restarts_after_clip_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip_path = Path(tmp) / "clip.wav"
            clip_path.write_bytes(b"RIFF")
            started_twice = threading.Event()
            call_count = 0

            def runner(command: list[str]):
                nonlocal call_count
                self.assertEqual(command[-1], str(clip_path))
                call_count += 1
                if call_count >= 2:
                    started_twice.set()
                return _ImmediateProcess()

            player = LoopingWavePlayer(clip_path=clip_path, runner=runner)

            self.assertTrue(player.start())
            self.assertTrue(started_twice.wait(1.0))
            player.stop()
            self.assertGreaterEqual(call_count, 2)

    def test_looping_wave_player_uses_configured_aplay_device(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip_path = Path(tmp) / "clip.wav"
            clip_path.write_bytes(b"RIFF")
            observed_command: list[str] = []

            def runner(command: list[str]):
                observed_command[:] = command
                return _ImmediateProcess()

            player = LoopingWavePlayer(
                clip_path=clip_path,
                device="plughw:CARD=Headphones,DEV=0",
                runner=runner,
            )

            self.assertTrue(player.start())
            player.stop()
            self.assertEqual(
                observed_command,
                [
                    "/usr/bin/aplay",
                    "-q",
                    "-D",
                    "plughw:CARD=Headphones,DEV=0",
                    str(clip_path),
                ],
            )

    def test_parse_aplay_device_candidates_prefers_non_hdmi(self) -> None:
        output = """
**** List of PLAYBACK Hardware Devices ****
card 0: vc4hdmi [vc4-hdmi], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]
card 1: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]
"""
        self.assertEqual(
            _parse_aplay_device_candidates(output),
            [
                "plughw:CARD=Headphones,DEV=0",
                "plughw:CARD=vc4hdmi,DEV=0",
            ],
        )

    def test_parse_aplay_device_candidates_prefers_usb_audio_over_builtin(self) -> None:
        output = """
**** List of PLAYBACK Hardware Devices ****
card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]
card 2: Device [USB Audio Device], device 0: USB Audio [USB Audio]
"""
        self.assertEqual(
            _parse_aplay_device_candidates(output),
            [
                "plughw:CARD=Device,DEV=0",
                "plughw:CARD=Headphones,DEV=0",
            ],
        )

    def test_looping_wave_player_auto_uses_detected_device(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip_path = Path(tmp) / "clip.wav"
            clip_path.write_bytes(b"RIFF")
            observed_command: list[str] = []

            def runner(command: list[str]):
                observed_command[:] = command
                return _ImmediateProcess()

            with patch(
                "callroo_printer.audio._detect_aplay_device_candidates",
                return_value=["plughw:CARD=Headphones,DEV=0"],
            ):
                player = LoopingWavePlayer(
                    clip_path=clip_path,
                    runner=runner,
                )
                self.assertTrue(player.start())
                player.stop()

            self.assertEqual(
                observed_command,
                [
                    "/usr/bin/aplay",
                    "-q",
                    "-D",
                    "plughw:CARD=Headphones,DEV=0",
                    str(clip_path),
                ],
            )

    def test_looping_wave_player_stops_running_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip_path = Path(tmp) / "clip.wav"
            clip_path.write_bytes(b"RIFF")
            process = _BlockingProcess()
            process_started = threading.Event()

            def runner(command: list[str]):
                self.assertEqual(command[-1], str(clip_path))
                process_started.set()
                return process

            player = LoopingWavePlayer(clip_path=clip_path, runner=runner)

            self.assertTrue(player.start())
            self.assertTrue(process_started.wait(1.0))
            player.stop()

            self.assertTrue(process.terminated)

    def test_looping_wave_player_applies_configured_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip_path = Path(tmp) / "clip.wav"
            _write_wave_file(clip_path, [2000, -2000])
            process = _BlockingProcess()
            process_started = threading.Event()
            observed_frames: list[bytes] = []
            observed_path: list[Path] = []

            def runner(command: list[str]):
                prepared_path = Path(command[-1])
                observed_path.append(prepared_path)
                with wave.open(str(prepared_path), "rb") as clip:
                    observed_frames.append(clip.readframes(clip.getnframes()))
                process_started.set()
                return process

            player = LoopingWavePlayer(
                clip_path=clip_path,
                volume=0.5,
                runner=runner,
            )

            self.assertTrue(player.start())
            self.assertTrue(process_started.wait(1.0))
            player.stop()

            self.assertNotEqual(observed_path[0], clip_path)
            self.assertEqual(observed_frames[0], _samples_to_frames([1000, -1000]))

    def test_looping_wave_player_skips_when_volume_is_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip_path = Path(tmp) / "clip.wav"
            _write_wave_file(clip_path, [2000, -2000])
            started = False

            def runner(command: list[str]):
                nonlocal started
                started = True
                return _ImmediateProcess()

            player = LoopingWavePlayer(
                clip_path=clip_path,
                volume=0.0,
                runner=runner,
            )

            self.assertFalse(player.start())
            self.assertFalse(started)

    def test_prepare_clip_for_aplay_uses_original_wav_without_volume_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip_path = Path(tmp) / "clip.wav"
            clip_path.write_bytes(b"RIFF")
            self.assertEqual(_prepare_clip_for_aplay(clip_path, 1.0), clip_path)

    def test_prepare_clip_for_aplay_transcodes_mp3_before_playback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip_path = Path(tmp) / "clip.mp3"
            clip_path.write_bytes(b"ID3")
            converted_path = Path(tmp) / "converted.wav"
            converted_path.write_bytes(b"RIFF")

            with patch(
                "callroo_printer.audio._transcode_audio_to_wav",
                return_value=converted_path,
            ) as transcode:
                prepared = _prepare_clip_for_aplay(clip_path, 0.5)

            self.assertEqual(prepared, converted_path)
            transcode.assert_called_once_with(clip_path, 0.5)

    def test_looping_wave_player_reuses_prepared_mp3_between_starts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip_path = Path(tmp) / "clip.mp3"
            clip_path.write_bytes(b"ID3")
            prepared_path = Path(tmp) / "prepared.wav"
            prepared_path.write_bytes(b"RIFF")
            observed_paths: list[str] = []
            process_started = threading.Event()
            processes = [_BlockingProcess(), _BlockingProcess()]

            def runner(command: list[str]):
                observed_paths.append(command[-1])
                process_started.set()
                return processes.pop(0)

            with patch(
                "callroo_printer.audio._transcode_audio_to_wav",
                return_value=prepared_path,
            ) as transcode:
                player = LoopingWavePlayer(clip_path=clip_path, runner=runner)
                self.assertTrue(player.prime())
                self.assertTrue(player.start())
                self.assertTrue(process_started.wait(1.0))
                player.stop()
                process_started.clear()
                self.assertTrue(player.start())
                self.assertTrue(process_started.wait(1.0))
                player.close()

            self.assertEqual(observed_paths, [str(prepared_path), str(prepared_path)])
            transcode.assert_called_once_with(clip_path, 1.0)

    def test_one_shot_wave_player_uses_configured_aplay_device(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip_path = Path(tmp) / "event.wav"
            clip_path.write_bytes(b"RIFF")
            observed_command: list[str] = []
            started = threading.Event()

            def runner(command: list[str]):
                observed_command[:] = command
                started.set()
                return _ImmediateProcess()

            player = OneShotWavePlayer(
                clip_path=clip_path,
                device="plughw:CARD=Headphones,DEV=0",
                runner=runner,
            )

            self.assertTrue(player.play())
            self.assertTrue(started.wait(1.0))
            player.close()
            self.assertEqual(
                observed_command,
                [
                    "/usr/bin/aplay",
                    "-q",
                    "-D",
                    "plughw:CARD=Headphones,DEV=0",
                    str(clip_path),
                ],
            )

    def test_one_shot_wave_player_respects_delay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip_path = Path(tmp) / "event.wav"
            clip_path.write_bytes(b"RIFF")
            started = threading.Event()

            def runner(command: list[str]):
                started.set()
                return _ImmediateProcess()

            player = OneShotWavePlayer(
                clip_path=clip_path,
                runner=runner,
            )

            self.assertTrue(player.play(delay_seconds=0.05))
            self.assertFalse(started.wait(0.01))
            self.assertTrue(started.wait(0.5))
            player.close()


class _ImmediateProcess:
    def wait(self, timeout: float | None = None) -> int | None:
        return 0

    def terminate(self) -> None:
        return

    def kill(self) -> None:
        return


class _BlockingProcess:
    def __init__(self) -> None:
        self.terminated = False
        self._stopped = threading.Event()

    def wait(self, timeout: float | None = None) -> int | None:
        if self._stopped.wait(timeout):
            return 0
        raise subprocess.TimeoutExpired(cmd="aplay", timeout=timeout or 0.0)

    def terminate(self) -> None:
        self.terminated = True
        self._stopped.set()

    def kill(self) -> None:
        self._stopped.set()


def _write_wave_file(path: Path, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(44100)
        clip.writeframes(_samples_to_frames(samples))


def _samples_to_frames(samples: list[int]) -> bytes:
    return b"".join(sample.to_bytes(2, byteorder="little", signed=True) for sample in samples)
