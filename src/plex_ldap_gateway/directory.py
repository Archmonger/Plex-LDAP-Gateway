"""Directory snapshot generation and bind authentication."""

from __future__ import annotations

import asyncio
from typing import Protocol

from ldaptor import inmemory
from ldaptor.protocols.ldap import distinguishedname

from .config import Settings
from .errors import PlexAuthenticationError
from .models import AuthorizedPlexUser, DirectorySnapshot, DirectoryUser, PlexAccount
from .utils import escape_rdn_value, normalize_dn, normalize_identity


class PlexClientProtocol(Protocol):
    async def authenticate_user(self, login: str, password: str) -> PlexAccount: ...

    async def get_owner_account(self) -> PlexAccount: ...

    async def get_shared_users(self) -> list[AuthorizedPlexUser]: ...

    async def aclose(self) -> None: ...


def _decode_credential_value(value: str | bytes) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PlexAuthenticationError("Invalid credential encoding") from error
    return value


def _identity_keys(account: PlexAccount) -> set[str]:
    keys = set()
    if account.plex_id is not None:
        keys.add(f"id:{account.plex_id}")
    if account.uuid:
        keys.add(f"uuid:{normalize_identity(account.uuid)}")
    if account.username:
        keys.add(f"username:{normalize_identity(account.username)}")
    if account.email:
        keys.add(f"email:{normalize_identity(account.email)}")
    return keys


def _root_attributes(base_dn: str) -> dict[str, list[str]]:
    first_rdn = distinguishedname.DistinguishedName(stringValue=base_dn).split()[0].getText()
    attribute_name, _, attribute_value = first_rdn.partition("=")
    object_classes = ["top", "domain"] if attribute_name.casefold() == "dc" else ["top", "organization"]
    return {
        "objectClass": object_classes,
        attribute_name: [attribute_value],
        "description": ["Plex-backed LDAP directory"],
    }


def _normalize_aliases(user: DirectoryUser) -> frozenset[str]:
    values = [user.uid, user.username, user.email, user.display_name]
    return frozenset(normalize_identity(value) for value in values if value)


def _preferred_uid(account: PlexAccount, used_uids: set[str]) -> str:
    candidates = [account.username, account.email, account.title]
    base_value = next((candidate for candidate in candidates if candidate and candidate.strip()), None)
    if base_value is None:
        if account.plex_id is not None:
            base_value = f"plex-user-{account.plex_id}"
        elif account.uuid:
            base_value = f"plex-user-{account.uuid}"
        else:
            base_value = "plex-user"

    candidate = base_value.strip()
    suffix = 1
    while normalize_identity(candidate) in used_uids:
        suffix += 1
        candidate = f"{base_value}-{suffix}"

    used_uids.add(normalize_identity(candidate))
    return candidate


def build_directory_snapshot(
    settings: Settings,
    owner_account: PlexAccount,
    shared_users: list[AuthorizedPlexUser],
) -> DirectorySnapshot:
    root = inmemory.ReadOnlyInMemoryLDAPEntry(
        dn=settings.ldap_base_dn,
        attributes=_root_attributes(settings.ldap_base_dn),
    )
    users_entry = root.addChild(
        rdn="ou=users",
        attributes={
            "objectClass": ["top", "organizationalUnit"],
            "ou": ["users"],
        },
    )

    selected_accounts: list[tuple[PlexAccount, str]] = [(owner_account, "owner")]
    for shared_user in shared_users:
        if settings.strict_machine_match and settings.plex_machine_identifier not in shared_user.machine_identifiers:
            continue
        selected_accounts.append((shared_user.account, "shared"))

    seen_identities: set[str] = set()
    used_uids: set[str] = set()
    users: list[DirectoryUser] = []
    alias_buckets: dict[str, list[DirectoryUser]] = {}
    dn_index: dict[str, DirectoryUser] = {}

    for account, role in selected_accounts:
        identity_keys = _identity_keys(account)
        if identity_keys and identity_keys & seen_identities:
            continue
        seen_identities.update(identity_keys)
        uid = _preferred_uid(account, used_uids)
        dn = f"uid={escape_rdn_value(uid)},{settings.users_dn}"
        bind_logins = tuple(login for login in (account.username, account.email, uid) if login and login.strip())
        user = DirectoryUser(
            dn=dn,
            uid=uid,
            role=role,
            username=account.username,
            email=account.email,
            display_name=account.display_name,
            plex_id=account.plex_id,
            plex_uuid=account.uuid,
            search_aliases=frozenset(),
            bind_logins=bind_logins,
        )
        user = DirectoryUser(
            dn=user.dn,
            uid=user.uid,
            role=user.role,
            username=user.username,
            email=user.email,
            display_name=user.display_name,
            plex_id=user.plex_id,
            plex_uuid=user.plex_uuid,
            search_aliases=_normalize_aliases(user),
            bind_logins=user.bind_logins,
        )
        users_entry.addChild(rdn=f"uid={escape_rdn_value(uid)}", attributes=user.ldap_attributes)
        users.append(user)
        dn_index[normalize_dn(user.dn)] = user
        for alias in user.search_aliases:
            alias_buckets.setdefault(alias, []).append(user)

    alias_index = {alias: grouped_users[0] for alias, grouped_users in alias_buckets.items() if len(grouped_users) == 1}
    return DirectorySnapshot(
        root=root,
        users=tuple(users),
        alias_index=alias_index,
        dn_index=dn_index,
    )


class PlexDirectoryService:
    def __init__(self, settings: Settings, plex_client: PlexClientProtocol) -> None:
        self.settings = settings
        self.plex_client = plex_client
        self._refresh_lock = asyncio.Lock()
        self._snapshot_loaded = False
        self._snapshot = build_directory_snapshot(settings, PlexAccount(None, None, "owner", None, "Plex Owner"), [])

    @property
    def current_snapshot(self) -> DirectorySnapshot:
        return self._snapshot

    async def aclose(self) -> None:
        await self.plex_client.aclose()

    async def refresh(self, *, force: bool = False) -> DirectorySnapshot:
        if self._snapshot_loaded and not force and self._snapshot.age_seconds < self.settings.directory_refresh_seconds:
            return self._snapshot

        async with self._refresh_lock:
            if (
                self._snapshot_loaded
                and not force
                and self._snapshot.age_seconds < self.settings.directory_refresh_seconds
            ):
                return self._snapshot
            owner_account, shared_users = await asyncio.gather(
                self.plex_client.get_owner_account(),
                self.plex_client.get_shared_users(),
            )
            self._snapshot = build_directory_snapshot(self.settings, owner_account, shared_users)
            self._snapshot_loaded = True
            return self._snapshot

    async def authenticate_bind(self, bind_identity: str | bytes, password: str | bytes) -> DirectoryUser:
        if not password:
            raise PlexAuthenticationError("Empty passwords are not accepted")

        snapshot = await self.refresh()
        user = self._resolve_user(snapshot, bind_identity)
        if user is None:
            raise PlexAuthenticationError("Unknown Plex directory identity")

        password_text = _decode_credential_value(password)
        bind_candidates = self._bind_candidates(bind_identity, user)
        for login in bind_candidates:
            try:
                account = await self.plex_client.authenticate_user(login, password_text)
            except PlexAuthenticationError:
                continue
            if user.matches_account(account):
                return user

        raise PlexAuthenticationError("Invalid Plex credentials")

    def _bind_candidates(self, bind_identity: str | bytes, user: DirectoryUser) -> tuple[str, ...]:
        raw = _decode_credential_value(bind_identity)
        raw = raw.strip()
        candidates: list[str] = []
        if raw and "," not in raw and "=" not in raw:
            candidates.append(raw)
        elif raw and "," not in raw and "=" in raw:
            _, _, value = raw.partition("=")
            if value:
                candidates.append(value)
        for candidate in user.bind_logins:
            if candidate not in candidates:
                candidates.append(candidate)
        return tuple(candidates)

    def _resolve_user(self, snapshot: DirectorySnapshot, bind_identity: str | bytes) -> DirectoryUser | None:
        raw = _decode_credential_value(bind_identity)
        raw = raw.strip()
        if not raw:
            return None

        user = snapshot.dn_index.get(normalize_dn(raw))
        if user is not None:
            return user

        direct = snapshot.alias_index.get(normalize_identity(raw))
        if direct is not None:
            return direct

        if "," not in raw and "=" in raw:
            _, _, value = raw.partition("=")
            return snapshot.alias_index.get(normalize_identity(value))

        return None
