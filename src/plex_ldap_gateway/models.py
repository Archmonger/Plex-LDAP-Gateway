"""Core data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .utils import derive_surname, normalize_identity


@dataclass(frozen=True, slots=True)
class PlexAccount:
    plex_id: int | None
    uuid: str | None
    username: str | None
    email: str | None
    title: str | None
    auth_token: str | None = None
    thumb: str | None = None

    @property
    def display_name(self) -> str:
        return self.title or self.username or self.email or self.uuid or "plex-user"

    @property
    def aliases(self) -> frozenset[str]:
        values = [self.username, self.email, self.title]
        return frozenset(normalize_identity(value) for value in values if value)


@dataclass(frozen=True, slots=True)
class AuthorizedPlexUser:
    account: PlexAccount
    machine_identifiers: frozenset[str] = field(default_factory=frozenset)
    home_user: bool = False
    restricted: bool = False


@dataclass(frozen=True, slots=True)
class DirectoryUser:
    dn: str
    uid: str
    role: str
    username: str | None
    email: str | None
    display_name: str
    plex_id: int | None
    plex_uuid: str | None
    search_aliases: frozenset[str]
    bind_logins: tuple[str, ...]

    @property
    def ldap_attributes(self) -> dict[str, list[str]]:
        attributes: dict[str, list[str]] = {
            "objectClass": ["top", "person", "organizationalPerson", "inetOrgPerson"],
            "uid": [self.uid],
            "cn": [self.display_name],
            "sn": [derive_surname(self.display_name, self.uid)],
            "displayName": [self.display_name],
            "employeeType": [self.role],
        }
        if self.username:
            attributes["plexUsername"] = [self.username]
        if self.email:
            attributes["mail"] = [self.email]
            attributes["userPrincipalName"] = [self.email]
        return attributes

    def matches_account(self, account: PlexAccount) -> bool:
        return bool(
            (self.plex_id is not None and account.plex_id is not None and self.plex_id == account.plex_id)
            or (
                self.plex_uuid
                and account.uuid
                and normalize_identity(self.plex_uuid) == normalize_identity(account.uuid)
            )
            or (self.search_aliases & account.aliases)
        )


@dataclass(slots=True)
class DirectorySnapshot:
    root: Any
    users: tuple[DirectoryUser, ...]
    alias_index: dict[str, DirectoryUser]
    dn_index: dict[str, DirectoryUser]
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def user_count(self) -> int:
        return len(self.users)

    @property
    def age_seconds(self) -> float:
        delta = datetime.now(UTC) - self.generated_at
        return delta.total_seconds()
