from __future__ import annotations

import logging
import signal
import threading

from dotenv import load_dotenv

load_dotenv()

from app.core.symbols import configured_symbol_config
from app.services.oracle_scheduler import start_oracle_scheduler, stop_oracle_scheduler

logger = logging.getLogger("app.worker.oracle_worker")


def _register_signal_handlers(stop_event: threading.Event) -> None:
    def _handle_signal(signum, _frame) -> None:
        logger.info("Received signal=%s, shutting down oracle worker.", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    stop_event = threading.Event()
    _register_signal_handlers(stop_event)

    symbol_config = configured_symbol_config()
    logger.info(
        "Starting oracle worker scheduler process resolved_path=%s raw_symbols=%s parsed_symbols=%s",
        symbol_config.resolved_path,
        symbol_config.raw_env_value,
        ",".join(symbol_config.symbols),
    )
    start_oracle_scheduler()

    try:
        while not stop_event.is_set():
            stop_event.wait(timeout=1.0)
    finally:
        stop_oracle_scheduler()
        logger.info("Oracle worker stopped.")


if __name__ == "__main__":
    main()
