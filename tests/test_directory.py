from __future__ import annotations

import pytest

from plex_ldap_gateway.config import Settings
from plex_ldap_gateway.directory import PlexDirectoryService
from plex_ldap_gateway.errors import PlexAuthenticationError
from plex_ldap_gateway.models import AuthorizedPlexUser, PlexAccount


class FakePlexClient:
    def __init__(self) -> None:
        self.owner = PlexAccount(
            plex_id=1,
            uuid="owner-uuid",
            username="owner",
            email="owner@example.com",
            title="Owner",
        )
        self.shared = [
            AuthorizedPlexUser(
                account=PlexAccount(
                    plex_id=2,
                    uuid="alice-uuid",
                    username="alice",
                    email="alice@example.com",
                    title="Alice Example",
                ),
                machine_identifiers={"machine-1"},
            ),
            AuthorizedPlexUser(
                account=PlexAccount(
                    plex_id=3,
                    uuid="charlie-uuid",
                    username="charlie",
                    email="charlie@example.com",
                    title="Charlie Example",
                ),
                machine_identifiers={"other-machine"},
            ),
        ]
        self.passwords = {
            "owner": "secret",
            "alice": "secret",
            "alice@example.com": "secret",
        }

    async def get_owner_account(self) -> PlexAccount:
        return self.owner

    async def get_shared_users(self) -> list[AuthorizedPlexUser]:
        return self.shared

    async def authenticate_user(self, login: str, password: str) -> PlexAccount:
        if self.passwords.get(login) != password:
            raise PlexAuthenticationError("invalid")
        if login.startswith("owner"):
            return self.owner
        return self.shared[0].account

    async def aclose(self) -> None:
        return None


def make_settings() -> Settings:
    return Settings.from_env(
        {
            "PLEX_OWNER_TOKEN": "owner-token",
            "PLEX_MACHINE_IDENTIFIER": "machine-1",
            "GATEWAY_LDAP_BASE_DN": "dc=plex,dc=ldap",
        }
    )


@pytest.mark.asyncio
async def test_refresh_builds_directory_filtered_by_machine_identifier() -> None:
    service = PlexDirectoryService(make_settings(), FakePlexClient())
    snapshot = await service.refresh(force=True)

    assert [user.uid for user in snapshot.users] == ["owner", "alice"]
    assert snapshot.user_count == 2


@pytest.mark.asyncio
async def test_authenticate_bind_accepts_dn_and_email() -> None:
    settings = make_settings()
    service = PlexDirectoryService(settings, FakePlexClient())
    snapshot = await service.refresh(force=True)
    alice = next(user for user in snapshot.users if user.username == "alice")

    bound_from_dn = await service.authenticate_bind(alice.dn, "secret")
    bound_from_email = await service.authenticate_bind("alice@example.com", "secret")

    assert bound_from_dn.uid == "alice"
    assert bound_from_email.uid == "alice"


@pytest.mark.asyncio
async def test_authenticate_bind_refreshes_snapshot_before_first_bind() -> None:
    service = PlexDirectoryService(make_settings(), FakePlexClient())

    user = await service.authenticate_bind("alice", "secret")

    assert user.uid == "alice"


@pytest.mark.asyncio
async def test_authenticate_bind_rejects_unknown_user() -> None:
    service = PlexDirectoryService(make_settings(), FakePlexClient())
    await service.refresh(force=True)

    with pytest.raises(PlexAuthenticationError):
        await service.authenticate_bind("unknown", "secret")
