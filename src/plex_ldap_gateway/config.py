"""Environment-driven configuration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from os import environ

from .__init__ import __version__

_LOG_LEVELS = {
    "critical": logging.CRITICAL,
    "fatal": logging.CRITICAL,
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}
_LOG_OUTPUTS = frozenset({"console", "file", "both"})


def _require(name: str, values: Mapping[str, str]) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _get_bool(name: str, values: Mapping[str, str], default: bool) -> bool:
    raw = values.get(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _get_int(name: str, values: Mapping[str, str], default: int) -> int:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _get_float(name: str, values: Mapping[str, str], default: float) -> float:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _get_log_level(name: str, values: Mapping[str, str], default: int) -> int:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default

    normalized = raw.strip().casefold()
    try:
        return _LOG_LEVELS[normalized]
    except KeyError as error:
        allowed = ", ".join(sorted(_LOG_LEVELS))
        raise ValueError(f"Invalid value for {name}: {raw!r}. Expected one of: {allowed}") from error


def _get_log_output(name: str, values: Mapping[str, str], default: str) -> str:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default

    normalized = raw.strip().casefold()
    if normalized not in _LOG_OUTPUTS:
        allowed = ", ".join(sorted(_LOG_OUTPUTS))
        raise ValueError(f"Invalid value for {name}: {raw!r}. Expected one of: {allowed}")
    return normalized


def _get_str(name: str, values: Mapping[str, str], default: str) -> str:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


@dataclass(frozen=True, slots=True)
class Settings:
    plex_owner_token: str
    plex_machine_identifier: str
    plex_base_url: str
    plex_client_identifier: str
    plex_client_product: str
    plex_client_version: str
    plex_timeout_seconds: float
    strict_machine_match: bool
    directory_refresh_seconds: int
    ldap_base_dn: str
    ldap_host: str
    ldap_port: int
    http_host: str
    http_port: int
    log_level: int
    log_output: str
    log_file_path: str

    @property
    def users_dn(self) -> str:
        return f"ou=users,{self.ldap_base_dn}"

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> Settings:
        source = environ if values is None else values
        machine_identifier = _require("PLEX_MACHINE_IDENTIFIER", source)
        client_identifier = source.get(
            "PLEX_CLIENT_IDENTIFIER",
            f"plex-ldap-gateway-{machine_identifier}",
        ).strip()
        return cls(
            plex_owner_token=_require("PLEX_OWNER_TOKEN", source),
            plex_machine_identifier=machine_identifier,
            plex_base_url=source.get("PLEX_BASE_URL", "https://plex.tv").rstrip("/"),
            plex_client_identifier=client_identifier,
            plex_client_product=source.get("PLEX_CLIENT_PRODUCT", "Plex LDAP Gateway").strip(),
            plex_client_version=source.get("PLEX_CLIENT_VERSION", __version__).strip(),
            plex_timeout_seconds=_get_float("PLEX_TIMEOUT_SECONDS", source, 10.0),
            strict_machine_match=_get_bool("GATEWAY_LDAP_STRICT_MACHINE_MATCH", source, True),
            directory_refresh_seconds=_get_int("GATEWAY_LDAP_REFRESH_SECONDS", source, 300),
            ldap_base_dn=source.get("GATEWAY_LDAP_BASE_DN", "dc=plex,dc=ldap").strip(),
            ldap_host=source.get("GATEWAY_LDAP_HOST", "0.0.0.0").strip(),
            ldap_port=_get_int("GATEWAY_LDAP_PORT", source, 1389),
            http_host=source.get("GATEWAY_HTTP_HOST", "0.0.0.0").strip(),
            http_port=_get_int("GATEWAY_HTTP_PORT", source, 7576),
            log_level=_get_log_level("GATEWAY_LOG_LEVEL", source, logging.ERROR),
            log_output=_get_log_output("GATEWAY_LOG_OUTPUT", source, "console"),
            log_file_path=_get_str("GATEWAY_LOG_FILE_PATH", source, "plex-ldap-gateway.log"),
        )
