from __future__ import annotations

from starlette.testclient import TestClient

from plex_ldap_gateway.app import create_app
from plex_ldap_gateway.config import Settings
from plex_ldap_gateway.directory import PlexDirectoryService
from plex_ldap_gateway.models import AuthorizedPlexUser, PlexAccount


class FakePlexClient:
    async def get_owner_account(self) -> PlexAccount:
        return PlexAccount(1, "owner-uuid", "owner", "owner@example.com", "Owner")

    async def get_shared_users(self) -> list[AuthorizedPlexUser]:
        return [
            AuthorizedPlexUser(
                account=PlexAccount(2, "alice-uuid", "alice", "alice@example.com", "Alice Example"),
                machine_identifiers={"machine-1"},
            )
        ]

    async def authenticate_user(self, login: str, password: str) -> PlexAccount:
        return PlexAccount(2, "alice-uuid", "alice", "alice@example.com", "Alice Example")

    async def aclose(self) -> None:
        return None


class StubLDAPListener:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    @property
    def is_listening(self) -> bool:
        return self.started and not self.stopped

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def make_settings() -> Settings:
    return Settings.from_env(
        {
            "PLEX_OWNER_TOKEN": "owner-token",
            "PLEX_MACHINE_IDENTIFIER": "machine-1",
        }
    )


def test_health_and_readiness_endpoints() -> None:
    settings = make_settings()
    directory_service = PlexDirectoryService(settings, FakePlexClient())
    listener = StubLDAPListener()
    app = create_app(settings=settings, directory_service=directory_service, ldap_listener=listener)

    with TestClient(app) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz?force=1")

    assert health.status_code == 200
    assert ready.status_code == 200
    assert health.json()["ldap"]["listening"] is True
    assert ready.json()["directory_users"] == 2
    assert listener.started is True
    assert listener.stopped is True
