from __future__ import annotations

import argparse
import logging
import signal
from pathlib import Path

from callroo_printer.config import AppConfig, load_config
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    configure_logging(config, args.log_level)
    install_signal_handlers()

    service = FortunePrinterService(config, dry_run=args.dry_run)
    service.run()


def configure_logging(config: AppConfig, log_level: str) -> None:
    logs_dir = config.output.logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / config.output.log_filename

    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
        force=True,
    )


def install_signal_handlers() -> None:
    def _handle_termination(signum: int, _frame) -> None:
        raise KeyboardInterrupt(f"Received signal {signum}")

    signal.signal(signal.SIGTERM, _handle_termination)


if __name__ == "__main__":
    main()
