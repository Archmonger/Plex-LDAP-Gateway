from __future__ import annotations

from dataclasses import dataclass
from os import environ

import pytest

from plex_ldap_gateway.config import Settings


@dataclass(frozen=True, slots=True)
class LivePlexInputs:
    settings: Settings
    bind_login: str
    bind_password: str
    bind_identity: str
    expected_username: str | None
    expected_email: str | None


def _live_value(name: str) -> str | None:
    value = environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@pytest.fixture
def live_plex_inputs() -> LivePlexInputs:
    required = (
        "PLEX_OWNER_TOKEN",
        "PLEX_MACHINE_IDENTIFIER",
        "PLEX_TEST_BIND_LOGIN",
        "PLEX_TEST_BIND_PASSWORD",
    )
    missing = [name for name in required if _live_value(name) is None]
    if missing:
        pytest.skip("Live Plex tests require these environment variables: " + ", ".join(missing))

    settings = Settings.from_env(environ)
    bind_login = _live_value("PLEX_TEST_BIND_LOGIN")
    bind_password = _live_value("PLEX_TEST_BIND_PASSWORD")
    assert bind_login is not None
    assert bind_password is not None

    bind_identity = _live_value("PLEX_TEST_BIND_IDENTITY") or bind_login
    return LivePlexInputs(
        settings=settings,
        bind_login=bind_login,
        bind_password=bind_password,
        bind_identity=bind_identity,
        expected_username=_live_value("PLEX_TEST_EXPECTED_USERNAME"),
        expected_email=_live_value("PLEX_TEST_EXPECTED_EMAIL"),
    )
