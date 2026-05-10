"""Plex API integration."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from defusedxml import ElementTree

from .config import Settings
from .errors import PlexAPIError, PlexAuthenticationError
from .models import AuthorizedPlexUser, PlexAccount

logger = logging.getLogger(__name__)


def _int_or_none(value: str | None) -> int | None:
    return int(value) if value else None


def _merge_account_profile(sign_in_account: PlexAccount, canonical_account: PlexAccount) -> PlexAccount:
    return PlexAccount(
        plex_id=canonical_account.plex_id if canonical_account.plex_id is not None else sign_in_account.plex_id,
        uuid=canonical_account.uuid or sign_in_account.uuid,
        username=canonical_account.username or sign_in_account.username,
        email=canonical_account.email or sign_in_account.email,
        title=canonical_account.title or sign_in_account.title,
        auth_token=canonical_account.auth_token or sign_in_account.auth_token,
        thumb=canonical_account.thumb or sign_in_account.thumb,
    )


class AsyncPlexClient:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._owns_client = client is None
        if client is not None:
            self._client = client
            logger.debug("Using provided async Plex HTTP client")
        else:
            self._client = httpx.AsyncClient(
                base_url=settings.plex_base_url,
                timeout=settings.plex_timeout_seconds,
                transport=transport,
                headers={
                    "Accept": "application/json",
                    "X-Plex-Product": settings.plex_client_product,
                    "X-Plex-Version": settings.plex_client_version,
                    "X-Plex-Client-Identifier": settings.plex_client_identifier,
                },
            )
            logger.debug("Created async Plex HTTP client for %s", settings.plex_base_url)

    async def aclose(self) -> None:
        if self._owns_client:
            logger.debug("Closing owned async Plex HTTP client")
            await self._client.aclose()

    def _headers(self, *, token: str | None = None, accept: str = "application/json") -> dict[str, str]:
        headers = {"Accept": accept}
        if token:
            headers["X-Plex-Token"] = token
        return headers

    async def authenticate_user(self, login: str, password: str) -> PlexAccount:
        logger.debug("Authenticating Plex user for login %r with password_length=%s", login, len(password))
        response = await self._client.post(
            "/users/sign_in.json",
            headers=self._headers(),
            data={
                "user[login]": login,
                "user[password]": password,
            },
        )
        if response.status_code in {401, 403}:
            logger.warning("Plex rejected credentials for login %s", login)
            raise PlexAuthenticationError("Invalid Plex credentials")
        response.raise_for_status()
        payload = response.json().get("user")
        if not isinstance(payload, dict):
            logger.error("Plex sign-in response for login %s did not include a user payload", login)
            raise PlexAPIError("Plex sign-in did not return a user payload")
        account = self._parse_account(payload)
        account = await self._canonicalize_authenticated_account(login, account)
        logger.info("Plex authentication succeeded for login %s", login)
        return account

    async def _canonicalize_authenticated_account(self, login: str, account: PlexAccount) -> PlexAccount:
        if not account.auth_token:
            logger.debug("Plex sign-in response for login %s did not include an auth token", login)
            return account

        try:
            canonical_account = await self._get_authenticated_account(account.auth_token)
        except (httpx.HTTPError, PlexAPIError, PlexAuthenticationError) as error:
            logger.debug(
                "Unable to resolve canonical Plex account for login %s; using sign-in response: %s",
                login,
                error,
            )
            return account

        merged_account = _merge_account_profile(account, canonical_account)
        logger.debug(
            "Canonicalized Plex account for login %s from plex_id=%s uuid=%s username=%s email=%s title=%s to plex_id=%s uuid=%s username=%s email=%s title=%s",
            login,
            account.plex_id,
            account.uuid,
            account.username,
            account.email,
            account.title,
            merged_account.plex_id,
            merged_account.uuid,
            merged_account.username,
            merged_account.email,
            merged_account.title,
        )
        return merged_account

    async def _get_authenticated_account(self, token: str) -> PlexAccount:
        response = await self._client.get(
            "/api/v2/user",
            headers=self._headers(token=token),
        )
        if response.status_code in {401, 403}:
            logger.warning("Authenticated Plex token was rejected while resolving canonical account")
            raise PlexAuthenticationError("Authenticated Plex token is not valid")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            logger.error("Canonical Plex account lookup returned a non-object payload")
            raise PlexAPIError("Canonical Plex account lookup did not return a JSON object")
        account = self._parse_account(payload)
        logger.debug("Fetched canonical Plex account profile")
        return account

    async def get_owner_account(self) -> PlexAccount:
        logger.debug("Fetching Plex owner account")
        response = await self._client.get(
            "/api/v2/user",
            headers=self._headers(token=self.settings.plex_owner_token),
        )
        if response.status_code in {401, 403}:
            logger.error("Plex owner token was rejected")
            raise PlexAuthenticationError("Owner token is not valid")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            logger.error("Plex owner lookup returned a non-object payload")
            raise PlexAPIError("Plex owner lookup did not return a JSON object")
        account = self._parse_account(payload)
        logger.info("Fetched Plex owner account successfully")
        return account

    async def get_shared_users(self) -> list[AuthorizedPlexUser]:
        logger.debug("Fetching Plex shared users")
        response = await self._client.get(
            "/api/users",
            headers=self._headers(token=self.settings.plex_owner_token, accept="application/xml"),
        )
        if response.status_code in {401, 403}:
            logger.error("Plex owner token was rejected while listing shared users")
            raise PlexAuthenticationError("Owner token is not valid")
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        users: list[AuthorizedPlexUser] = []
        for element in root.findall(".//User"):
            account = PlexAccount(
                plex_id=_int_or_none(element.get("id")),
                uuid=element.get("uuid"),
                username=element.get("username") or element.get("title"),
                email=element.get("email"),
                title=element.get("title"),
                thumb=element.get("thumb"),
            )
            machine_identifiers = frozenset(
                child.get("machineIdentifier")
                for child in element.findall(".//Server")
                if child.get("machineIdentifier")
            )
            users.append(
                AuthorizedPlexUser(
                    account=account,
                    machine_identifiers=machine_identifiers,
                    home_user=element.get("home") == "1",
                    restricted=element.get("restricted") == "1",
                )
            )
        logger.info("Fetched %s Plex shared users", len(users))
        return users

    @staticmethod
    def _parse_account(payload: dict[str, Any]) -> PlexAccount:
        return PlexAccount(
            plex_id=_int_or_none(str(payload["id"])) if payload.get("id") is not None else None,
            uuid=payload.get("uuid"),
            username=payload.get("username"),
            email=payload.get("email"),
            title=payload.get("title"),
            auth_token=payload.get("authToken"),
            thumb=payload.get("thumb"),
        )
