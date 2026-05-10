from __future__ import annotations

import httpx
import pytest

from plex_ldap_gateway.config import Settings
from plex_ldap_gateway.models import PlexAccount
from plex_ldap_gateway.plex import AsyncPlexClient


def make_settings() -> Settings:
    return Settings.from_env(
        {
            "PLEX_OWNER_TOKEN": "owner-token",
            "PLEX_MACHINE_IDENTIFIER": "machine-1",
        }
    )


@pytest.mark.asyncio
async def test_authenticate_user_parses_sign_in_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users/sign_in.json"
        return httpx.Response(
            200,
            json={
                "user": {
                    "id": 101,
                    "uuid": "user-uuid",
                    "username": "alice",
                    "email": "alice@example.com",
                    "title": "Alice Example",
                }
            },
        )

    client = AsyncPlexClient(make_settings(), transport=httpx.MockTransport(handler))
    account = await client.authenticate_user("alice", "secret")
    await client.aclose()

    assert account == PlexAccount(
        plex_id=101,
        uuid="user-uuid",
        username="alice",
        email="alice@example.com",
        title="Alice Example",
        auth_token=None,
        thumb=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "json_payload"),
    [
        (500, {"error": "upstream error"}),
        (403, None),
        (200, []),
    ],
)
async def test_authenticate_user_falls_back_to_sign_in_identity_when_canonical_lookup_fails(
    status_code: int,
    json_payload: object | None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/sign_in.json":
            return httpx.Response(
                200,
                json={
                    "user": {
                        "id": 101,
                        "uuid": "sign-in-uuid",
                        "username": "alice",
                        "email": "alice@example.com",
                        "title": "Alice Example",
                        "authToken": "user-token",
                    }
                },
            )

        if request.url.path == "/api/v2/user":
            assert request.headers["X-Plex-Token"] == "user-token"
            if json_payload is None:
                return httpx.Response(status_code)
            return httpx.Response(status_code, json=json_payload)

        raise AssertionError(f"Unexpected Plex path: {request.url.path}")

    client = AsyncPlexClient(make_settings(), transport=httpx.MockTransport(handler))
    account = await client.authenticate_user("alice", "secret")
    await client.aclose()

    assert account == PlexAccount(
        plex_id=101,
        uuid="sign-in-uuid",
        username="alice",
        email="alice@example.com",
        title="Alice Example",
        auth_token="user-token",
        thumb=None,
    )


@pytest.mark.asyncio
async def test_get_shared_users_parses_machine_access() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Plex-Token"] == "owner-token"
        return httpx.Response(
            200,
            text="""
<MediaContainer>
  <User id="201" uuid="u-201" username="alice" email="alice@example.com" title="Alice Example" home="1" restricted="0">
    <Server machineIdentifier="machine-1" />
    <Server machineIdentifier="machine-2" />
  </User>
  <User id="202" uuid="u-202" username="bob" email="bob@example.com" title="Bob Example" home="0" restricted="1">
    <Server machineIdentifier="machine-3" />
  </User>
</MediaContainer>
""",
        )

    client = AsyncPlexClient(make_settings(), transport=httpx.MockTransport(handler))
    users = await client.get_shared_users()
    await client.aclose()

    assert [user.account.username for user in users] == ["alice", "bob"]
    assert users[0].machine_identifiers == {"machine-1", "machine-2"}
    assert users[0].home_user is True
    assert users[1].restricted is True


@pytest.mark.asyncio
async def test_get_owner_account_parses_owner_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/user"
        assert request.headers["X-Plex-Token"] == "owner-token"
        return httpx.Response(
            200,
            json={
                "id": 1,
                "uuid": "owner-uuid",
                "username": "owner",
                "email": "owner@example.com",
                "title": "Owner",
                "authToken": "owner-auth-token",
            },
        )

    client = AsyncPlexClient(make_settings(), transport=httpx.MockTransport(handler))
    account = await client.get_owner_account()
    await client.aclose()

    assert account == PlexAccount(
        plex_id=1,
        uuid="owner-uuid",
        username="owner",
        email="owner@example.com",
        title="Owner",
        auth_token="owner-auth-token",
        thumb=None,
    )
