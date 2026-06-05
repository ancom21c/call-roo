from __future__ import annotations

import argparse
import logging
import signal
from pathlib import Path

from callroo_printer.config import AppConfig, load_config
from callroo_printer.dashboard import (
    DEFAULT_DASHBOARD_HOST,
    DEFAULT_DASHBOARD_PORT,
    detect_service_config_path,
    serve_dashboard,
)
from callroo_printer.service import FortunePrinterService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="callroo-printer")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.json"),
        help="Path to the JSON config file.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Python logging level.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate artifacts without sending anything to the Bluetooth printer.",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Run the web dashboard instead of the printer service.",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=DEFAULT_DASHBOARD_PORT,
        help="Port for the web dashboard server.",
    )
    parser.add_argument(
        "--dashboard-host",
        default=DEFAULT_DASHBOARD_HOST,
        help="Bind host for the web dashboard server.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = args.config
    if args.dashboard:
        service_config_path = detect_service_config_path()
        default_config_path = Path("config.json").resolve()
        requested_config_path = args.config.resolve()
        should_follow_service_config = (
            service_config_path is not None
            and requested_config_path == default_config_path
            and service_config_path != requested_config_path
        )
        if should_follow_service_config:
            logging.basicConfig(
                level=getattr(logging, args.log_level),
                format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                handlers=[logging.StreamHandler()],
                force=True,
            )
            logging.getLogger(__name__).info(
                "Dashboard will read runtime data from %s",
                service_config_path,
            )
            config_path = service_config_path

    config = load_config(config_path)
    configure_logging(
        config,
        args.log_level,
        include_file_handler=not args.dashboard,
    )
    install_signal_handlers()

    if args.dashboard:
        try:
            serve_dashboard(
                config,
                config_path=config_path,
                host=args.dashboard_host,
                port=args.dashboard_port,
            )
        except KeyboardInterrupt:
            logging.getLogger(__name__).info("Dashboard shutdown requested.")
        return

    service = FortunePrinterService(config, dry_run=args.dry_run)
    service.run()


def configure_logging(
    config: AppConfig,
    log_level: str,
    *,
    include_file_handler: bool = True,
) -> None:
    logs_dir = config.output.logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / config.output.log_filename

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if include_file_handler:
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def install_signal_handlers() -> None:
    def _handle_termination(signum: int, _frame) -> None:
        raise KeyboardInterrupt(f"Received signal {signum}")

    signal.signal(signal.SIGTERM, _handle_termination)


if __name__ == "__main__":
    main()
