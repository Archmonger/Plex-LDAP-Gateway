from __future__ import annotations

import asyncio

import pytest
from ldaptor.protocols import pureldap
from ldaptor.protocols.ldap import ldaperrors
from twisted.test import proto_helpers

from plex_ldap_gateway.directory import PlexDirectoryService
from plex_ldap_gateway.ldap_server import PlexLDAPServer, PlexLDAPServerFactory
from plex_ldap_gateway.plex import AsyncPlexClient


@pytest.mark.asyncio
@pytest.mark.live_plex
async def test_live_directory_refresh_and_bind(live_plex_inputs) -> None:
    plex_client = AsyncPlexClient(live_plex_inputs.settings)
    directory_service = PlexDirectoryService(live_plex_inputs.settings, plex_client)

    try:
        snapshot = await directory_service.refresh(force=True)
        assert snapshot.user_count >= 1

        user = await directory_service.authenticate_bind(
            live_plex_inputs.bind_identity,
            live_plex_inputs.bind_password,
        )

        assert user.dn
        assert user.bind_logins

        if live_plex_inputs.expected_username is not None:
            assert user.username == live_plex_inputs.expected_username
        if live_plex_inputs.expected_email is not None:
            assert user.email == live_plex_inputs.expected_email
    finally:
        await directory_service.aclose()


@pytest.mark.asyncio
@pytest.mark.live_plex
async def test_live_ldap_bind_roundtrip(live_plex_inputs) -> None:
    plex_client = AsyncPlexClient(live_plex_inputs.settings)
    directory_service = PlexDirectoryService(live_plex_inputs.settings, plex_client)

    try:
        user = await directory_service.authenticate_bind(
            live_plex_inputs.bind_identity,
            live_plex_inputs.bind_password,
        )

        server = PlexLDAPServer()
        server.factory = PlexLDAPServerFactory(directory_service)
        server.transport = proto_helpers.StringTransport()
        server.connectionMade()

        server.dataReceived(
            pureldap.LDAPMessage(
                pureldap.LDAPBindRequest(
                    dn=live_plex_inputs.bind_identity,
                    auth=live_plex_inputs.bind_password,
                ),
                id=7,
            ).toWire()
        )

        for _ in range(50):
            if server.transport.value():
                break
            await asyncio.sleep(0.1)

        assert server.transport.value() == pureldap.LDAPMessage(
            pureldap.LDAPBindResponse(
                resultCode=ldaperrors.Success.resultCode,
                matchedDN=user.dn,
            ),
            id=7,
        ).toWire()
    finally:
        await directory_service.aclose()
