from __future__ import annotations

import importlib
import json
import logging
import shutil
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from callroo_printer.config import BluetoothConfig

LOGGER = logging.getLogger(__name__)

ESC = 0x1B
GS = 0x1D


@dataclass(frozen=True)
class PrinterArtifact:
    filename: str
    payload: bytes


@dataclass(frozen=True)
class TiMiniPrintDirectRuntime:
    reporting: Any
    session_cls: Any
    load_pages: Any
    scale_pages_vertically: Any
    build_page_buffer: Any
    darkness_to_intensity: Any


def create_printer(config: BluetoothConfig):
    backend = config.backend.strip().lower()
    if backend in {"timiniprint_cli_direct", "timiniprint-mxw01-direct", "timiniprint"}:
        return TiMiniPrintDirectPrinter(config)
    if backend != "rfcomm":
        raise RuntimeError(
            "Unsupported bluetooth backend "
            f"{config.backend!r}. Expected 'rfcomm' or 'timiniprint_cli_direct'."
        )
    return BluetoothPrinter(config)


class BluetoothPrinter:
    keepalive_supported = True

    def __init__(self, config: BluetoothConfig):
        self.config = config
        self._lock = threading.RLock()
        self._sock: socket.socket | None = None
        self._active_channel: int | None = None

    def connect_if_needed(self) -> None:
        with self._lock:
            if self._sock is not None:
                return
            self._sock, self._active_channel = self._connect()
            LOGGER.info(
                "Bluetooth printer connected to %s on RFCOMM channel %s",
                self.config.mac_address,
                self._active_channel,
            )

    def keep_alive(self) -> None:
        command = bytes.fromhex(self.config.keepalive_hex)
        self.transceive(command, response_bytes=self.config.keepalive_response_bytes)

    def build_artifacts(
        self,
        *,
        image_path: Path,
        image: Image.Image,
        threshold: int,
        trailing_feed_lines: int,
    ) -> list[PrinterArtifact]:
        del image_path
        job = self.build_print_job(
            image=image,
            threshold=threshold,
            trailing_feed_lines=trailing_feed_lines,
        )
        return [PrinterArtifact(filename="print-job.bin", payload=job)]

    def print_saved_image(
        self,
        *,
        image_path: Path,
        image: Image.Image,
        threshold: int,
        trailing_feed_lines: int,
    ) -> None:
        del image_path
        self.print_image(
            image=image,
            threshold=threshold,
            trailing_feed_lines=trailing_feed_lines,
        )

    def transceive(self, payload: bytes, response_bytes: int = 0) -> bytes:
        with self._lock:
            self.connect_if_needed()
            assert self._sock is not None
            try:
                self._sock.sendall(payload)
                if response_bytes <= 0:
                    return b""
                return self._recv_exact(response_bytes)
            except (OSError, RuntimeError) as exc:
                self._close_locked()
                raise RuntimeError(f"Bluetooth communication failed: {exc}") from exc

    def print_image(
        self,
        image: Image.Image,
        threshold: int,
        trailing_feed_lines: int,
    ) -> bytes:
        job = self.build_print_job(
            image=image,
            threshold=threshold,
            trailing_feed_lines=trailing_feed_lines,
        )
        self.send_job(job)
        return job

    def build_print_job(
        self,
        image: Image.Image,
        threshold: int,
        trailing_feed_lines: int,
    ) -> bytes:
        prepared = _prepare_image_for_raster(image)
        commands: list[bytes] = [bytes([ESC, 0x40])]
        for chunk in _iter_chunks(prepared, chunk_height=255):
            commands.append(_build_raster_command(chunk, threshold=threshold))
        if trailing_feed_lines > 0:
            commands.append(bytes([ESC, 0x64, trailing_feed_lines]))
        return b"".join(commands)

    def send_job(self, payload: bytes) -> None:
        self.transceive(payload)

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _connect(self) -> tuple[socket.socket, int]:
        if not hasattr(socket, "AF_BLUETOOTH"):
            raise RuntimeError(
                "Python Bluetooth sockets are not available on this platform. "
                "Run this app on Raspberry Pi OS or another Linux build with BlueZ."
            )

        channels = self._channel_candidates()
        errors: list[str] = []
        for channel in channels:
            sock: socket.socket | None = None
            try:
                sock = socket.socket(
                    socket.AF_BLUETOOTH,
                    socket.SOCK_STREAM,
                    socket.BTPROTO_RFCOMM,
                )
                sock.settimeout(self.config.connect_timeout_seconds)
                sock.connect((self.config.mac_address, channel))
                sock.settimeout(self.config.connect_timeout_seconds)
                return sock, channel
            except OSError as exc:
                errors.append(f"channel {channel}: {exc}")
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass

        raise RuntimeError(
            "Failed to connect to Bluetooth printer. Tried channels "
            f"{channels}. Details: {'; '.join(errors)}"
        )

    def _channel_candidates(self) -> list[int]:
        candidates: list[int] = []
        if self.config.channel is not None:
            candidates.append(self.config.channel)
        if self.config.auto_detect_channel:
            candidates.extend(_discover_rfcomm_channels(self.config.mac_address))
        candidates.extend(self.config.channel_candidates)

        deduped: list[int] = []
        seen: set[int] = set()
        for channel in candidates:
            if channel in seen:
                continue
            seen.add(channel)
            deduped.append(channel)
        return deduped or [1, 6]

    def _recv_exact(self, size: int) -> bytes:
        assert self._sock is not None
        chunks = bytearray()
        while len(chunks) < size:
            packet = self._sock.recv(size - len(chunks))
            if not packet:
                raise RuntimeError("Bluetooth printer disconnected during receive")
            chunks.extend(packet)
        return bytes(chunks)

    def _close_locked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._active_channel = None


class TiMiniPrintDirectPrinter:
    keepalive_supported = True

    def __init__(self, config: BluetoothConfig):
        self.config = config
        self._lock = threading.RLock()
        self._runtime: TiMiniPrintDirectRuntime | None = None
        self._session: Any | None = None

    def connect_if_needed(self) -> None:
        with self._lock:
            self._ensure_connected_locked()

    def keep_alive(self) -> None:
        with self._lock:
            try:
                session = self._ensure_connected_locked()
                session.query_status()
            except Exception as exc:
                self._close_locked()
                raise RuntimeError(f"TiMini-Print direct keepalive failed: {exc}") from exc

    def build_artifacts(
        self,
        *,
        image_path: Path,
        image: Image.Image,
        threshold: int,
        trailing_feed_lines: int,
    ) -> list[PrinterArtifact]:
        del image
        del threshold
        del trailing_feed_lines
        command, cwd = self._build_command(image_path)
        payload = {
            "backend": "timiniprint_cli_direct",
            "cwd": str(cwd),
            "command": command,
            "mac_address": self.config.mac_address,
            "timiniprint_direct_y_scale": self.config.timiniprint_direct_y_scale,
            "timiniprint_darkness": self.config.timiniprint_darkness,
        }
        return [
            PrinterArtifact(
                filename="print-command.json",
                payload=(json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
                    "utf-8"
                ),
            )
        ]

    def print_saved_image(
        self,
        *,
        image_path: Path,
        image: Image.Image,
        threshold: int,
        trailing_feed_lines: int,
    ) -> None:
        del image
        del threshold
        del trailing_feed_lines
        with self._lock:
            self._print_saved_image_locked(image_path, retry_allowed=True)

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _build_command(self, image_path: Path) -> tuple[list[str], Path]:
        python_path, cli_path, cwd = self._resolve_runtime()
        command = [
            str(python_path),
            str(cli_path),
            "--bluetooth",
            self.config.mac_address,
            "--mxw01-direct",
            "--mxw01-direct-y-scale",
            f"{self.config.timiniprint_direct_y_scale:g}",
            str(image_path),
        ]
        if self.config.timiniprint_darkness is not None:
            command.extend(["--darkness", str(self.config.timiniprint_darkness)])
        return command, cwd

    def _resolve_runtime(self) -> tuple[Path, Path, Path]:
        repo_path = self.config.timiniprint_repo
        cli_path = self.config.timiniprint_cli
        if cli_path is None:
            if repo_path is None:
                raise RuntimeError(
                    "TiMini-Print backend requires bluetooth.timiniprint_repo or "
                    "bluetooth.timiniprint_cli in config.json"
                )
            cli_path = repo_path / "timiniprint_command_line.py"

        python_path = self.config.timiniprint_python
        if python_path is None and repo_path is not None:
            venv_python = repo_path / ".venv" / "bin" / "python"
            if venv_python.exists():
                python_path = venv_python
        if python_path is None:
            python_path = Path(sys.executable)

        if repo_path is None:
            repo_path = cli_path.parent
        if not repo_path.exists():
            raise RuntimeError(f"TiMini-Print repo path does not exist: {repo_path}")
        if not cli_path.exists():
            raise RuntimeError(f"TiMini-Print CLI was not found: {cli_path}")
        if not python_path.exists():
            raise RuntimeError(f"Python executable for TiMini-Print was not found: {python_path}")
        return python_path, cli_path, repo_path

    def _ensure_connected_locked(self):
        if self._session is not None:
            return self._session

        runtime = self._load_runtime()
        session = runtime.session_cls(
            address=self.config.mac_address,
            reporter=runtime.reporting.DUMMY_REPORTER,
            connect_timeout=self.config.connect_timeout_seconds,
            status_timeout=self.config.keepalive_timeout_seconds,
        )
        try:
            session.connect()
            session.enable_notifications()
            session.query_status()
        except Exception:
            try:
                session.close()
            except Exception:
                pass
            raise

        self._session = session
        LOGGER.info(
            "TiMini-Print direct session connected to %s",
            self.config.mac_address,
        )
        return session

    def _print_saved_image_locked(self, image_path: Path, retry_allowed: bool) -> None:
        runtime = self._load_runtime()
        try:
            session = self._ensure_connected_locked()
            pages, _ = runtime.load_pages(str(image_path))
            pages = runtime.scale_pages_vertically(
                pages,
                self.config.timiniprint_direct_y_scale,
            )
            intensity = runtime.darkness_to_intensity(self.config.timiniprint_darkness)

            session.query_status()
            for page in pages:
                buffer, height = runtime.build_page_buffer(page)
                session.print_page(buffer, height, intensity)
            session.query_status()
        except Exception as exc:
            if not retry_allowed:
                raise RuntimeError(f"TiMini-Print direct print failed: {exc}") from exc

            LOGGER.warning(
                "TiMini-Print direct print failed, reconnecting once: %s",
                exc,
            )
            self._close_locked()
            self._print_saved_image_locked(image_path, retry_allowed=False)
            return

        LOGGER.info(
            "Printed ticket using TiMini-Print direct backend for %s",
            self.config.mac_address,
        )

    def _close_locked(self) -> None:
        if self._session is None:
            return
        try:
            self._session.close()
        finally:
            self._session = None

    def _load_runtime(self) -> TiMiniPrintDirectRuntime:
        if self._runtime is not None:
            return self._runtime

        _, _, repo_path = self._resolve_runtime()
        self._extend_sys_path(repo_path)

        try:
            reporting = importlib.import_module("timiniprint.reporting")
            mxw01_direct = importlib.import_module(
                "timiniprint.transport.bluetooth.mxw01_direct"
            )
        except ImportError as exc:
            raise RuntimeError(
                "Failed to import TiMini-Print direct runtime. "
                f"Checked repo {repo_path}, any bundled site-packages, "
                f"and the active Python environment: {exc}"
            ) from exc

        self._runtime = TiMiniPrintDirectRuntime(
            reporting=reporting,
            session_cls=mxw01_direct.Mxw01DirectSession,
            load_pages=mxw01_direct.load_pages,
            scale_pages_vertically=mxw01_direct.scale_pages_vertically,
            build_page_buffer=mxw01_direct.build_page_buffer,
            darkness_to_intensity=mxw01_direct.darkness_to_intensity,
        )
        return self._runtime

    @staticmethod
    def _extend_sys_path(repo_path: Path) -> None:
        candidates = [repo_path]
        venv_dir = repo_path / ".venv"
        if venv_dir.exists():
            candidates.extend(sorted(venv_dir.glob("lib/python*/site-packages")))

        for candidate in reversed(candidates):
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)


def _discover_rfcomm_channels(mac_address: str) -> list[int]:
    if shutil.which("sdptool") is None:
        return []

    try:
        result = subprocess.run(
            ["sdptool", "browse", mac_address],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []

    channels: list[int] = []
    for line in result.stdout.splitlines():
        cleaned = line.strip()
        if not cleaned.startswith("Channel:"):
            continue
        try:
            channels.append(int(cleaned.split(":", 1)[1].strip()))
        except ValueError:
            continue
    return channels


def _prepare_image_for_raster(image: Image.Image) -> Image.Image:
    grayscale = image.convert("L")
    padded_width = ((grayscale.width + 7) // 8) * 8
    if padded_width == grayscale.width:
        return grayscale

    canvas = Image.new("L", (padded_width, grayscale.height), color=255)
    canvas.paste(grayscale, (0, 0))
    return canvas


def _iter_chunks(image: Image.Image, chunk_height: int):
    top = 0
    while top < image.height:
        bottom = min(top + chunk_height, image.height)
        yield image.crop((0, top, image.width, bottom))
        top = bottom


def _build_raster_command(image: Image.Image, threshold: int) -> bytes:
    width = image.width
    height = image.height
    byte_width = width // 8

    output = bytearray([GS, 0x76, 0x30, 0x00])
    output.extend(byte_width.to_bytes(2, byteorder="little"))
    output.extend(height.to_bytes(2, byteorder="little"))

    pixels = image.load()
    for y in range(height):
        for byte_index in range(byte_width):
            value = 0
            for bit in range(8):
                x = byte_index * 8 + bit
                if pixels[x, y] < threshold:
                    value |= 1 << (7 - bit)
            output.append(value)
    return bytes(output)
