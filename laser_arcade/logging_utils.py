from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .constants import LOG_DIR


def setup_logging(level: int = logging.INFO) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "laser_arcade.log"

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    handlers = []
    file_handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3)
    file_handler.setFormatter(formatter)
    handlers.append(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    handlers.append(console_handler)

    logging.basicConfig(level=level, handlers=handlers)
    logging.getLogger("pygame").setLevel(logging.WARNING)
