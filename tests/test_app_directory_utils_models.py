from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from plex_ldap_gateway.app import create_app
from plex_ldap_gateway.config import Settings
from plex_ldap_gateway.directory import PlexDirectoryService, build_directory_snapshot
from plex_ldap_gateway.errors import PlexAuthenticationError, PlexLDAPError
from plex_ldap_gateway.models import (
    AuthorizedPlexUser,
    DirectorySnapshot,
    DirectoryUser,
    PlexAccount,
)
from plex_ldap_gateway.utils import (
    await_maybe_deferred,
    coerce_text,
    deferred_from_coro,
    derive_surname,
    escape_rdn_value,
    normalize_dn,
    normalize_identity,
)


def make_settings(*, strict_machine_match: bool = True) -> Settings:
    values = {
        "PLEX_OWNER_TOKEN": "owner-token",
        "PLEX_MACHINE_IDENTIFIER": "machine-1",
        "PLEX_LDAP_BASE_DN": "dc=plex,dc=ldap",
    }
    if not strict_machine_match:
        values["PLEX_LDAP_STRICT_MACHINE_MATCH"] = "false"
    return Settings.from_env(values)


def snapshot_with(count: int) -> DirectorySnapshot:
    return DirectorySnapshot(
        root="root",
        users=tuple(SimpleNamespace(index=index) for index in range(count)),
        alias_index={},
        dn_index={},
    )


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


class SequencedDirectoryService:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.current_snapshot = responses[0] if isinstance(responses[0], DirectorySnapshot) else snapshot_with(0)
        self.closed = False

    async def refresh(self, *, force: bool = False) -> DirectorySnapshot:
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        self.current_snapshot = result
        return result

    async def aclose(self) -> None:
        self.closed = True


def test_readyz_reports_plex_errors() -> None:
    directory_service = SequencedDirectoryService([snapshot_with(1), PlexLDAPError("directory failed")])
    listener = StubLDAPListener()
    app = create_app(settings=make_settings(), directory_service=directory_service, ldap_listener=listener)

    with TestClient(app) as client:
        response = client.get("/readyz?force=1")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "detail": "directory failed"}
    assert directory_service.closed is True


def test_readyz_reports_unexpected_errors() -> None:
    directory_service = SequencedDirectoryService([snapshot_with(1), RuntimeError("boom")])
    app = create_app(
        settings=make_settings(),
        directory_service=directory_service,
        ldap_listener=StubLDAPListener(),
    )

    with TestClient(app) as client:
        response = client.get("/readyz?force=1")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "detail": "RuntimeError"}


@pytest.mark.asyncio
async def test_utils_and_model_helpers_cover_edge_cases() -> None:
    fallback_account = PlexAccount(None, None, None, None, None)
    alias_account = PlexAccount(1, "UUID-1", "Alice", "alice@example.com", "Alice Example")
    user = DirectoryUser(
        dn="uid=alice,ou=users,dc=plex,dc=ldap",
        uid="alice",
        role="shared",
        username="alice",
        email="alice@example.com",
        display_name="Alice Example",
        plex_id=1,
        plex_uuid="uuid-1",
        search_aliases=frozenset({"alice", "alice@example.com"}),
        bind_logins=("alice", "alice@example.com"),
    )
    sparse_user = DirectoryUser(
        dn="uid=fallback,ou=users,dc=plex,dc=ldap",
        uid="fallback",
        role="owner",
        username=None,
        email=None,
        display_name="",
        plex_id=None,
        plex_uuid=None,
        search_aliases=frozenset(),
        bind_logins=(),
    )

    assert fallback_account.display_name == "plex-user"
    assert fallback_account.aliases == frozenset()
    assert user.matches_account(alias_account) is True
    assert user.matches_account(PlexAccount(7, "other", "other", "other@example.com", "Other")) is False
    assert sparse_user.ldap_attributes["sn"] == ["fallback"]
    assert coerce_text(b"value") == "value"
    assert normalize_identity(" Value ") == "value"
    assert normalize_dn("CN=Alice,DC=Example") == "cn=alice,dc=example"
    assert normalize_dn("not a valid dn") == "not a valid dn"
    assert escape_rdn_value("#ldap ") == "\\#ldap\\ "
    assert escape_rdn_value("a+b,=") == "a\\+b\\,\\="
    assert derive_surname("", "fallback") == "fallback"
    assert await await_maybe_deferred(deferred_from_coro(asyncio.sleep(0, result="done"))) == "done"
    assert await await_maybe_deferred("plain") == "plain"


def test_build_directory_snapshot_uses_fallback_uids_and_skips_duplicate_identities() -> None:
    settings = make_settings(strict_machine_match=False)
    owner = PlexAccount(None, None, None, None, None)
    shared_users = [
        AuthorizedPlexUser(account=PlexAccount(11, None, None, None, None), machine_identifiers=frozenset()),
        AuthorizedPlexUser(account=PlexAccount(None, "uuid-only", None, None, None), machine_identifiers=frozenset()),
        AuthorizedPlexUser(account=PlexAccount(None, None, None, None, None), machine_identifiers=frozenset()),
        AuthorizedPlexUser(account=PlexAccount(11, "duplicate", None, None, None), machine_identifiers=frozenset()),
    ]

    snapshot = build_directory_snapshot(settings, owner, shared_users)

    assert [user.uid for user in snapshot.users] == [
        "plex-user",
        "plex-user-11",
        "plex-user-uuid-only",
        "plex-user-2",
    ]


class MatchingPlexClient:
    def __init__(self) -> None:
        self.owner = PlexAccount(1, "owner-uuid", "owner", "owner@example.com", "Owner")
        self.alice = PlexAccount(2, "alice-uuid", "alice", "alice@example.com", "Alice Example")
        self.calls: list[tuple[str, str]] = []

    async def get_owner_account(self) -> PlexAccount:
        return self.owner

    async def get_shared_users(self) -> list[AuthorizedPlexUser]:
        return [AuthorizedPlexUser(account=self.alice, machine_identifiers={"machine-1"})]

    async def authenticate_user(self, login: str, password: str) -> PlexAccount:
        self.calls.append((login, password))
        if password != "secret":
            raise PlexAuthenticationError("invalid")
        return self.alice

    async def aclose(self) -> None:
        return None


class MismatchedPlexClient(MatchingPlexClient):
    async def authenticate_user(self, login: str, password: str) -> PlexAccount:
        self.calls.append((login, password))
        return PlexAccount(99, "other-uuid", "other", "other@example.com", "Other")


class EmailFallbackPlexClient(MatchingPlexClient):
    async def authenticate_user(self, login: str, password: str) -> PlexAccount:
        self.calls.append((login, password))
        if login == "alice":
            raise PlexAuthenticationError("invalid")
        return self.alice


@pytest.mark.asyncio
async def test_authenticate_bind_supports_bytes_and_internal_identity_helpers() -> None:
    client = MatchingPlexClient()
    service = PlexDirectoryService(make_settings(), client)

    user = await service.authenticate_bind(b"uid=alice", b"secret")
    snapshot = service.current_snapshot

    assert user.uid == "alice"
    assert service._resolve_user(snapshot, "alice") is user
    assert service._resolve_user(snapshot, "alice@example.com") is user
    assert service._resolve_user(snapshot, " ") is None
    assert service._resolve_user(snapshot, "uid=missing") is None
    assert service._bind_candidates("uid=alice", user) == ("alice", "alice@example.com")
    assert service._bind_candidates("alice", user) == ("alice", "alice@example.com")
    assert service._bind_candidates("uid=", user) == ("alice", "alice@example.com")
    assert client.calls == [("alice", "secret")]


@pytest.mark.asyncio
async def test_authenticate_bind_rejects_mismatched_account() -> None:
    service = PlexDirectoryService(make_settings(), MismatchedPlexClient())

    with pytest.raises(PlexAuthenticationError, match="Invalid Plex credentials"):
        await service.authenticate_bind("alice", "secret")


@pytest.mark.asyncio
async def test_authenticate_bind_rejects_empty_passwords() -> None:
    service = PlexDirectoryService(make_settings(), MatchingPlexClient())

    with pytest.raises(PlexAuthenticationError, match="Empty passwords are not accepted"):
        await service.authenticate_bind("alice", "")


@pytest.mark.asyncio
async def test_authenticate_bind_rejects_invalid_utf8_password_bytes() -> None:
    service = PlexDirectoryService(make_settings(), MatchingPlexClient())

    with pytest.raises(PlexAuthenticationError, match="Invalid credential encoding"):
        await service.authenticate_bind("alice", b"\xff")


@pytest.mark.asyncio
async def test_authenticate_bind_retries_after_invalid_candidate() -> None:
    client = EmailFallbackPlexClient()
    service = PlexDirectoryService(make_settings(), client)

    user = await service.authenticate_bind("uid=alice", "secret")

    assert user.uid == "alice"
    assert client.calls == [("alice", "secret"), ("alice@example.com", "secret")]


@pytest.mark.asyncio
async def test_refresh_returns_cached_snapshot_after_waiting_for_lock() -> None:
    service = PlexDirectoryService(make_settings(), MatchingPlexClient())
    service._snapshot_loaded = False
    await service._refresh_lock.acquire()

    cached_snapshot = build_directory_snapshot(
        service.settings,
        PlexAccount(1, "owner-uuid", "owner", "owner@example.com", "Owner"),
        [],
    )
    refresh_task = asyncio.create_task(service.refresh())
    await asyncio.sleep(0)
    service._snapshot = cached_snapshot
    service._snapshot_loaded = True
    service._refresh_lock.release()

    assert await refresh_task is cached_snapshot
