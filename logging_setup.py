"""Logging setup used by manual runs and launchd runs."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_file: Path, verbose: bool = False) -> None:
    """Send logs to both the terminal and a rotating log file."""

    log_file.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=1_000_000,
        backupCount=5,
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
