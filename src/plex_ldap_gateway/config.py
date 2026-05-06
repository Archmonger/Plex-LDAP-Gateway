"""Environment-driven configuration."""

from __future__ import annotations

from dataclasses import dataclass
from os import environ
from typing import Mapping

from .__init__ import __version__


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

    @property
    def users_dn(self) -> str:
        return f"ou=users,{self.ldap_base_dn}"

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> "Settings":
        source = values or environ
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
            strict_machine_match=_get_bool("PLEX_LDAP_STRICT_MACHINE_MATCH", source, True),
            directory_refresh_seconds=_get_int("PLEX_LDAP_REFRESH_SECONDS", source, 300),
            ldap_base_dn=source.get("PLEX_LDAP_BASE_DN", "dc=plex,dc=ldap").strip(),
            ldap_host=source.get("PLEX_LDAP_HOST", "0.0.0.0").strip(),
            ldap_port=_get_int("PLEX_LDAP_PORT", source, 1389),
            http_host=source.get("PLEX_HTTP_HOST", "127.0.0.1").strip(),
            http_port=_get_int("PLEX_HTTP_PORT", source, 8000),
        )
