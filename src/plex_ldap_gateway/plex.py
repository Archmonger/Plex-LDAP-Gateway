"""Plex API integration."""

from __future__ import annotations

from typing import Any

import httpx
from defusedxml import ElementTree

from .config import Settings
from .errors import PlexAPIError, PlexAuthenticationError
from .models import AuthorizedPlexUser, PlexAccount


def _int_or_none(value: str | None) -> int | None:
    if not value:
        return None
    return int(value)


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

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self, *, token: str | None = None, accept: str = "application/json") -> dict[str, str]:
        headers = {"Accept": accept}
        if token:
            headers["X-Plex-Token"] = token
        return headers

    async def authenticate_user(self, login: str, password: str) -> PlexAccount:
        response = await self._client.post(
            "/users/sign_in.json",
            headers=self._headers(),
            data={
                "user[login]": login,
                "user[password]": password,
            },
        )
        if response.status_code in {401, 403}:
            raise PlexAuthenticationError("Invalid Plex credentials")
        response.raise_for_status()
        payload = response.json().get("user")
        if not isinstance(payload, dict):
            raise PlexAPIError("Plex sign-in did not return a user payload")
        return self._parse_account(payload)

    async def get_owner_account(self) -> PlexAccount:
        response = await self._client.get(
            "/api/v2/user",
            headers=self._headers(token=self.settings.plex_owner_token),
        )
        if response.status_code in {401, 403}:
            raise PlexAuthenticationError("Owner token is not valid")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise PlexAPIError("Plex owner lookup did not return a JSON object")
        return self._parse_account(payload)  # pragma: no cover

    async def get_shared_users(self) -> list[AuthorizedPlexUser]:
        response = await self._client.get(
            "/api/users",
            headers=self._headers(token=self.settings.plex_owner_token, accept="application/xml"),
        )
        if response.status_code in {401, 403}:
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
