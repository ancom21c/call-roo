#!/usr/bin/env python3
"""Keep an MXW01 direct session open and print on Enter."""
from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path
from typing import Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from timiniprint import reporting
from timiniprint.app.diagnostics import emit_startup_warnings
from timiniprint.transport.bluetooth.mxw01_direct import (
    Mxw01DirectSession,
    build_page_buffer,
    darkness_to_intensity,
    load_pages,
    scale_pages_vertically,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Keep MXW01/C15 connected and print the selected image when Enter is pressed."
    )
    parser.add_argument("path", help="Initial image/text/pdf path to print")
    parser.add_argument(
        "--bluetooth",
        required=True,
        help="Printer MAC address (for example 48:0F:57:2B:CB:24)",
    )
    parser.add_argument("--darkness", type=int, choices=range(1, 6), help="Print darkness (1-5)")
    parser.add_argument(
        "--y-scale",
        type=float,
        default=1.0,
        help="Vertical scale factor applied before printing",
    )
    parser.add_argument(
        "--keepalive-seconds",
        type=float,
        default=15.0,
        help="How often to ping printer status while idle; 0 disables keepalive",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logs")
    return parser.parse_args()


def build_reporter(verbose: bool) -> reporting.Reporter:
    levels = {"warning", "error", "debug"} if verbose else {"warning", "error"}
    return reporting.Reporter([reporting.StderrSink(levels=levels)])


class EnterPrintLoop:
    def __init__(
        self,
        *,
        address: str,
        path: str,
        darkness: Optional[int],
        y_scale: float,
        keepalive_seconds: float,
        reporter: reporting.Reporter,
    ) -> None:
        self._address = address
        self._path = path
        self._darkness = darkness
        self._intensity = darkness_to_intensity(darkness)
        self._y_scale = y_scale
        self._keepalive_seconds = max(0.0, keepalive_seconds)
        self._reporter = reporter
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._keepalive_thread: Optional[threading.Thread] = None
        self._session: Optional[Mxw01DirectSession] = None

    def start(self) -> None:
        with self._lock:
            self._connect_locked()
        if self._keepalive_seconds > 0:
            self._keepalive_thread = threading.Thread(
                target=self._keepalive_loop,
                name="mxw01-keepalive",
                daemon=True,
            )
            self._keepalive_thread.start()

    def close(self) -> None:
        self._stop_event.set()
        if self._keepalive_thread is not None:
            self._keepalive_thread.join(timeout=2.0)
        with self._lock:
            self._close_session_locked()

    def set_path(self, path: str) -> Path:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {resolved}")
        self._path = str(resolved)
        return resolved

    def print_once(self) -> Path:
        with self._lock:
            return self._print_once_locked(retry_allowed=True)

    def _print_once_locked(self, *, retry_allowed: bool) -> Path:
        try:
            self._ensure_connected_locked()
            assert self._session is not None
            pages, source_path = load_pages(self._path)
            pages = scale_pages_vertically(pages, self._y_scale)
            self._session.query_status()
            for index, page in enumerate(pages, start=1):
                buffer, height = build_page_buffer(page)
                self._reporter.debug(
                    short="MXW01",
                    detail=(
                        f"loop print page {index}/{len(pages)} "
                        f"height={height} raster_bytes={len(buffer)} source={source_path}"
                    ),
                )
                self._session.print_page(buffer, height, self._intensity)
            self._session.query_status()
            return source_path
        except Exception as exc:
            if not retry_allowed:
                raise
            self._reporter.warning(
                short="MXW01",
                detail=f"Print failed, reconnecting once: {exc}",
            )
            self._connect_locked()
            return self._print_once_locked(retry_allowed=False)

    def _ensure_connected_locked(self) -> None:
        if self._session is None:
            self._connect_locked()

    def _connect_locked(self) -> None:
        self._close_session_locked()
        session = Mxw01DirectSession(address=self._address, reporter=self._reporter)
        try:
            session.connect()
            session.enable_notifications()
            session.query_status()
        except Exception:
            session.close()
            raise
        self._session = session

    def _close_session_locked(self) -> None:
        if self._session is None:
            return
        try:
            self._session.close()
        finally:
            self._session = None

    def _keepalive_loop(self) -> None:
        while not self._stop_event.wait(self._keepalive_seconds):
            with self._lock:
                if self._session is None:
                    continue
                try:
                    self._session.query_status()
                except Exception as exc:
                    self._reporter.warning(
                        short="MXW01",
                        detail=f"Keepalive failed, reconnecting: {exc}",
                    )
                    try:
                        self._connect_locked()
                    except Exception as reconnect_exc:
                        self._reporter.warning(
                            short="MXW01",
                            detail=f"Reconnect failed: {reconnect_exc}",
                        )
                        self._close_session_locked()


def main() -> int:
    args = parse_args()
    reporter = build_reporter(args.verbose)
    emit_startup_warnings(reporter)

    loop = EnterPrintLoop(
        address=args.bluetooth,
        path=args.path,
        darkness=args.darkness,
        y_scale=args.y_scale,
        keepalive_seconds=args.keepalive_seconds,
        reporter=reporter,
    )
    current_path = loop.set_path(args.path)
    loop.start()

    print(f"Connected to {args.bluetooth}")
    print(f"Current file: {current_path}")
    print("Press Enter to print, type a new file path to switch, or type q to quit.")

    try:
        while True:
            try:
                command = input("> ")
            except EOFError:
                print()
                break
            stripped = command.strip()
            if stripped.lower() in {"q", "quit", "exit"}:
                break
            if stripped:
                current_path = loop.set_path(stripped)
                print(f"Updated file: {current_path}")
                continue
            printed_path = loop.print_once()
            print(f"Printed: {printed_path}")
    except KeyboardInterrupt:
        print()
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
