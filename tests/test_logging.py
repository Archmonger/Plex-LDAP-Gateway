from __future__ import annotations

import logging

from plex_ldap_gateway.config import Settings
from plex_ldap_gateway.logging_utils import PACKAGE_LOGGER_NAME, configure_logging


def make_settings(**overrides: str) -> Settings:
    values = {
        "PLEX_OWNER_TOKEN": "owner-token",
        "PLEX_MACHINE_IDENTIFIER": "machine-1",
    }
    values.update(overrides)
    return Settings.from_env(values)


def test_configure_logging_emits_errors_only_to_console_by_default(capsys) -> None:
    settings = make_settings()
    logger = configure_logging(settings)
    child_logger = logging.getLogger(f"{PACKAGE_LOGGER_NAME}.tests")

    child_logger.warning("warning-hidden")
    child_logger.error("error-visible")
    for handler in logger.handlers:
        handler.flush()

    output = capsys.readouterr().err

    assert "warning-hidden" not in output
    assert "error-visible" in output


def test_configure_logging_emits_to_console_and_file_and_reconfigures(tmp_path, capsys) -> None:
    log_path = tmp_path / "logs" / "plex-ldap-gateway.log"
    both_settings = make_settings(
        GATEWAY_LOG_LEVEL="debug",
        GATEWAY_LOG_OUTPUT="both",
        GATEWAY_LOG_FILE_PATH=str(log_path),
    )
    logger = configure_logging(both_settings)
    child_logger = logging.getLogger(f"{PACKAGE_LOGGER_NAME}.tests")

    child_logger.info("both-visible")
    for handler in logger.handlers:
        handler.flush()

    output = capsys.readouterr().err
    file_output = log_path.read_text(encoding="utf-8")

    assert "both-visible" in output
    assert "both-visible" in file_output

    file_only_settings = make_settings(
        GATEWAY_LOG_LEVEL="error",
        GATEWAY_LOG_OUTPUT="file",
        GATEWAY_LOG_FILE_PATH=str(log_path),
    )
    logger = configure_logging(file_only_settings)

    child_logger.error("file-only-visible")
    for handler in logger.handlers:
        handler.flush()

    output = capsys.readouterr().err
    file_output = log_path.read_text(encoding="utf-8")

    assert "file-only-visible" not in output
    assert "file-only-visible" in file_output
