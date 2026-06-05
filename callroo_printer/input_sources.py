from __future__ import annotations

import logging
import os
import queue
import select
import struct
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from callroo_printer.config import InputConfig

LOGGER = logging.getLogger(__name__)

EV_KEY = 0x01
KEY_PRESSED = 1
INPUT_EVENT = struct.Struct("llHHi")
KEY_NAMES = {
    28: "KEY_ENTER",
    96: "KEY_KPENTER",
}


@dataclass(frozen=True)
class TriggerEvent:
    raw_input: str
    source: str
    details: dict[str, Any]


class TriggerSourceMonitor:
    def __init__(self, config: InputConfig):
        self.config = config
        self._queue: queue.Queue[TriggerEvent] = queue.Queue()
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._device_fds: dict[int, Path] = {}

    def start(self) -> list[str]:
        descriptions: list[str] = []

        if self.config.stdin_enabled and _stdin_is_interactive():
            thread = threading.Thread(
                target=self._stdin_loop,
                name="stdin-trigger-reader",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
            descriptions.append("stdin")
        elif self.config.stdin_enabled:
            LOGGER.info("Skipping stdin trigger source because no interactive TTY is attached.")

        if self.config.linux_event_enabled:
            opened_paths = self._open_linux_input_devices()
            if not opened_paths:
                raise RuntimeError(
                    "Linux input monitoring is enabled, but no event devices could be opened."
                )

            thread = threading.Thread(
                target=self._linux_input_loop,
                name="linux-input-trigger-reader",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
            descriptions.extend(f"linux-input:{path}" for path in opened_paths)

        if not descriptions:
            raise RuntimeError(
                "No trigger input sources are enabled. Enable stdin or Linux input devices."
            )

        return descriptions

    def next_trigger(self, timeout_seconds: float = 0.25) -> TriggerEvent | None:
        try:
            return self._queue.get(timeout=timeout_seconds)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._stop_event.set()

        for fd in list(self._device_fds):
            self._close_linux_device(fd)

        for thread in self._threads:
            thread.join(timeout=0.5)

    def _stdin_loop(self) -> None:
        while not self._stop_event.is_set():
            line = sys.stdin.readline()
            if line == "":
                if self._stop_event.wait(0.25):
                    return
                continue
            self._queue.put(
                TriggerEvent(
                    raw_input=line,
                    source="stdin",
                    details={},
                )
            )

    def _open_linux_input_devices(self) -> list[Path]:
        opened_paths: list[Path] = []
        key_names = ", ".join(
            format_key_name(code) for code in self.config.linux_event_keycodes
        )

        for path in self.config.linux_event_paths:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except FileNotFoundError:
                LOGGER.warning("Configured Linux input device not found: %s", path)
            except PermissionError:
                LOGGER.warning(
                    "Permission denied opening Linux input device %s. Add the runtime user to the input group or run with sufficient privileges.",
                    path,
                )
            except OSError as exc:
                LOGGER.warning("Failed opening Linux input device %s: %s", path, exc)
            else:
                self._device_fds[fd] = path
                opened_paths.append(path)
                LOGGER.info("Monitoring Linux input device %s for %s", path, key_names)

        return opened_paths

    def _linux_input_loop(self) -> None:
        while not self._stop_event.is_set():
            fds = list(self._device_fds)
            if not fds:
                if self._stop_event.wait(0.25):
                    return
                continue

            try:
                readable, _, _ = select.select(fds, [], [], 0.25)
            except OSError as exc:
                if self._stop_event.is_set():
                    return
                LOGGER.warning("Linux input device poll failed: %s", exc)
                continue

            for fd in readable:
                for event in self._read_linux_events(fd):
                    self._queue.put(event)

    def _read_linux_events(self, fd: int) -> list[TriggerEvent]:
        path = self._device_fds.get(fd)
        if path is None:
            return []

        events: list[TriggerEvent] = []
        while True:
            try:
                payload = os.read(fd, INPUT_EVENT.size * 64)
            except BlockingIOError:
                return events
            except OSError as exc:
                LOGGER.warning("Failed reading Linux input device %s: %s", path, exc)
                self._close_linux_device(fd)
                return events

            if not payload:
                LOGGER.warning("Linux input device %s closed and will be ignored.", path)
                self._close_linux_device(fd)
                return events

            events.extend(
                parse_linux_input_events(
                    payload,
                    device_path=path,
                    trigger_keycodes=set(self.config.linux_event_keycodes),
                )
            )

            if len(payload) < INPUT_EVENT.size * 64:
                return events

    def _close_linux_device(self, fd: int) -> None:
        self._device_fds.pop(fd, None)
        try:
            os.close(fd)
        except OSError:
            pass


def parse_linux_input_events(
    payload: bytes,
    device_path: Path,
    trigger_keycodes: set[int],
) -> list[TriggerEvent]:
    events: list[TriggerEvent] = []
    remainder = len(payload) % INPUT_EVENT.size
    if remainder:
        payload = payload[:-remainder]

    for offset in range(0, len(payload), INPUT_EVENT.size):
        _, _, event_type, code, value = INPUT_EVENT.unpack_from(payload, offset)
        if event_type != EV_KEY or value != KEY_PRESSED or code not in trigger_keycodes:
            continue

        key_name = format_key_name(code)
        raw_input = "\n" if code in {28, 96} else f"{key_name}\n"
        events.append(
            TriggerEvent(
                raw_input=raw_input,
                source="linux-input",
                details={
                    "device_path": str(device_path),
                    "key_code": code,
                    "key_name": key_name,
                },
            )
        )

    return events


def format_key_name(code: int) -> str:
    return KEY_NAMES.get(code, f"KEY_{code}")


def _stdin_is_interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False
