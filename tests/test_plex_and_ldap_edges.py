from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from ldaptor.protocols import pureldap
from ldaptor.protocols.ldap import ldaperrors
from ldaptor.protocols.ldap.ldapserver import LDAPServer

from plex_ldap_gateway.config import Settings
from plex_ldap_gateway.errors import PlexAPIError, PlexAuthenticationError
from plex_ldap_gateway.ldap_server import PlexLDAPServer, PlexLDAPServerFactory
from plex_ldap_gateway.models import AuthorizedPlexUser, DirectorySnapshot, PlexAccount
from plex_ldap_gateway.plex import AsyncPlexClient, _int_or_none


def make_settings() -> Settings:
    return Settings.from_env(
        {
            "PLEX_OWNER_TOKEN": "owner-token",
            "PLEX_MACHINE_IDENTIFIER": "machine-1",
        }
    )


def test_int_or_none_handles_blank_values() -> None:
    assert _int_or_none(None) is None
    assert _int_or_none("") is None
    assert _int_or_none("5") == 5


@pytest.mark.asyncio
async def test_async_plex_client_reuses_passed_client_and_default_headers() -> None:
    class ProvidedClient:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    provided = ProvidedClient()
    client = AsyncPlexClient(make_settings(), client=provided)

    assert client._headers() == {"Accept": "application/json"}

    await client.aclose()

    assert provided.closed is False


@pytest.mark.asyncio
async def test_authenticate_user_raises_for_invalid_credentials() -> None:
    client = AsyncPlexClient(
        make_settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(401)),
    )

    with pytest.raises(PlexAuthenticationError, match="Invalid Plex credentials"):
        await client.authenticate_user("alice", "secret")

    await client.aclose()


@pytest.mark.asyncio
async def test_authenticate_user_raises_for_missing_user_payload() -> None:
    client = AsyncPlexClient(
        make_settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"user": []})),
    )

    with pytest.raises(PlexAPIError, match="did not return a user payload"):
        await client.authenticate_user("alice", "secret")

    await client.aclose()


@pytest.mark.asyncio
async def test_get_owner_account_raises_for_invalid_token_and_payload() -> None:
    forbidden = AsyncPlexClient(
        make_settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(403)),
    )

    with pytest.raises(PlexAuthenticationError, match="Owner token is not valid"):
        await forbidden.get_owner_account()

    await forbidden.aclose()

    malformed = AsyncPlexClient(
        make_settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
    )

    with pytest.raises(PlexAPIError, match="did not return a JSON object"):
        await malformed.get_owner_account()

    await malformed.aclose()


@pytest.mark.asyncio
async def test_get_shared_users_raises_for_invalid_token() -> None:
    client = AsyncPlexClient(
        make_settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(403)),
    )

    with pytest.raises(PlexAuthenticationError, match="Owner token is not valid"):
        await client.get_shared_users()

    await client.aclose()


@pytest.mark.asyncio
async def test_get_shared_users_uses_title_fallback_and_ignores_missing_machine_ids() -> None:
    client = AsyncPlexClient(
        make_settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=(
                    "<MediaContainer>"
                    "<User id='201' uuid='u-201' email='alice@example.com' title='Alice Example'>"
                    "<Server />"
                    "<Server machineIdentifier='machine-1' />"
                    "</User>"
                    "</MediaContainer>"
                ),
            )
        ),
    )

    users = await client.get_shared_users()
    await client.aclose()

    assert users == [
        AuthorizedPlexUser(
            account=PlexAccount(
                plex_id=201,
                uuid="u-201",
                username="Alice Example",
                email="alice@example.com",
                title="Alice Example",
                thumb=None,
            ),
            machine_identifiers=frozenset({"machine-1"}),
            home_user=False,
            restricted=False,
        )
    ]


class RaisingDirectoryService:
    current_snapshot = DirectorySnapshot(root="root", users=(), alias_index={}, dn_index={})

    async def authenticate_bind(self, bind_identity: str, password: str):
        raise PlexAuthenticationError("bad credentials")

    async def refresh(self) -> None:
        return None


def test_handle_bind_request_rejects_unsupported_versions() -> None:
    server = PlexLDAPServer()

    with pytest.raises(ldaperrors.LDAPProtocolError, match="Version 2 not supported"):
        server.handle_LDAPBindRequest(SimpleNamespace(version=2, dn="uid=alice", auth="secret"), None, None)


def test_handle_bind_request_allows_anonymous_binds(monkeypatch: pytest.MonkeyPatch) -> None:
    server = PlexLDAPServer()
    monkeypatch.setattr(server, "checkControls", lambda controls: None)

    response = server.handle_LDAPBindRequest(SimpleNamespace(version=3, dn="", auth=""), None, None)

    assert isinstance(response, pureldap.LDAPBindResponse)
    assert response.resultCode == ldaperrors.Success.resultCode
    assert server.boundUser is None


@pytest.mark.asyncio
async def test_bind_against_plex_maps_invalid_credentials() -> None:
    server = PlexLDAPServer()
    server.factory = SimpleNamespace(directory_service=RaisingDirectoryService())

    with pytest.raises(ldaperrors.LDAPInvalidCredentials, match="bad credentials"):
        await server._bind_against_plex(SimpleNamespace(dn="uid=alice", auth="secret"))


@pytest.mark.asyncio
async def test_bind_against_plex_maps_unexpected_errors_to_unavailable() -> None:
    class ExplodingDirectoryService:
        async def authenticate_bind(self, bind_identity: str, password: str):
            raise RuntimeError("backend down")

    server = PlexLDAPServer()
    server.factory = SimpleNamespace(directory_service=ExplodingDirectoryService())

    with pytest.raises(ldaperrors.LDAPUnavailable, match="Authentication backend unavailable"):
        await server._bind_against_plex(SimpleNamespace(dn="uid=alice", auth="secret"))


@pytest.mark.asyncio
async def test_refresh_then_search_refreshes_directory_and_exposes_root(monkeypatch: pytest.MonkeyPatch) -> None:
    class RefreshingDirectoryService:
        def __init__(self) -> None:
            self.refreshed = False
            self.current_snapshot = DirectorySnapshot(root="root-entry", users=(), alias_index={}, dn_index={})

        async def refresh(self) -> None:
            self.refreshed = True

    directory_service = RefreshingDirectoryService()
    factory = PlexLDAPServerFactory(directory_service)
    server = PlexLDAPServer()
    server.factory = factory
    monkeypatch.setattr(LDAPServer, "handle_LDAPSearchRequest", lambda self, request, controls, reply: "search-result")

    result = await server._refresh_then_search(SimpleNamespace(), None, None)

    assert factory.root == "root-entry"
    assert directory_service.refreshed is True
    assert result == "search-result"


def test_handle_search_request_wraps_refresh_coroutine(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    server = PlexLDAPServer()

    async def fake_refresh_then_search(request, controls, reply):
        return None

    monkeypatch.setattr(server, "_refresh_then_search", fake_refresh_then_search)

    def wrap(coro):
        coro.close()
        return sentinel

    import plex_ldap_gateway.ldap_server as ldap_server_module

    monkeypatch.setattr(ldap_server_module, "deferred_from_coro", wrap)

    assert server.handle_LDAPSearchRequest("request", "controls", "reply") is sentinel
