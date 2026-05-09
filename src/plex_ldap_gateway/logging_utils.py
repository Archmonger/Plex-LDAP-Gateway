"""Logging configuration helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Settings


PACKAGE_LOGGER_NAME = "plex_ldap_gateway"
_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(settings: Settings) -> logging.Logger:
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    logger.setLevel(settings.log_level)
    logger.propagate = False

    formatter = logging.Formatter(_LOG_FORMAT)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    if settings.log_output in {"console", "both"}:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(settings.log_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    if settings.log_output in {"file", "both"}:
        log_path = Path(settings.log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(settings.log_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.debug(
        "Logging configured at %s with output=%s file_path=%s",
        logging.getLevelName(settings.log_level),
        settings.log_output,
        settings.log_file_path,
    )
    return logger
