from __future__ import annotations

import asyncio

import pytest
from ldaptor.protocols import pureldap
from ldaptor.protocols.ldap import ldaperrors
from twisted.test import proto_helpers

from plex_ldap_gateway.config import Settings
from plex_ldap_gateway.directory import PlexDirectoryService
from plex_ldap_gateway.errors import PlexAuthenticationError
from plex_ldap_gateway.ldap_server import PlexLDAPServer, PlexLDAPServerFactory
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
        if password != "secret":
            raise PlexAuthenticationError("invalid")
        return PlexAccount(2, "alice-uuid", "alice", "alice@example.com", "Alice Example")

    async def aclose(self) -> None:
        return None


def make_settings() -> Settings:
    return Settings.from_env(
        {
            "PLEX_OWNER_TOKEN": "owner-token",
            "PLEX_MACHINE_IDENTIFIER": "machine-1",
        }
    )


@pytest.mark.asyncio
async def test_ldap_bind_succeeds_for_directory_dn() -> None:
    directory_service = PlexDirectoryService(make_settings(), FakePlexClient())
    snapshot = await directory_service.refresh(force=True)
    alice = next(user for user in snapshot.users if user.username == "alice")

    server = PlexLDAPServer()
    server.factory = PlexLDAPServerFactory(directory_service)
    server.transport = proto_helpers.StringTransport()
    server.connectionMade()

    server.dataReceived(
        pureldap.LDAPMessage(
            pureldap.LDAPBindRequest(dn=alice.dn, auth="secret"),
            id=4,
        ).toWire()
    )

    for _ in range(10):
        if server.transport.value():
            break
        await asyncio.sleep(0)

    assert server.transport.value() == pureldap.LDAPMessage(
        pureldap.LDAPBindResponse(
            resultCode=ldaperrors.Success.resultCode,
            matchedDN=alice.dn,
        ),
        id=4,
    ).toWire()
