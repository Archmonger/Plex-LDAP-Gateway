"""Shared helpers."""

from __future__ import annotations

import asyncio
from typing import Any

from ldaptor.protocols.ldap import distinguishedname
from twisted.internet import defer


def coerce_text(value: str | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def normalize_identity(value: str | bytes) -> str:
    return coerce_text(value).strip().casefold()


def normalize_dn(value: str | bytes) -> str:
    text = coerce_text(value).strip()
    try:
        return distinguishedname.DistinguishedName(stringValue=text).getText().casefold()
    except Exception:
        return text.casefold()


def escape_rdn_value(value: str) -> str:
    escaped: list[str] = []
    last_index = len(value) - 1
    for index, char in enumerate(value):
        if char in {",", "+", '"', "\\", "<", ">", ";", "="}:
            escaped.append(f"\\{char}")
        elif index == 0 and char in {" ", "#"}:
            escaped.append(f"\\{char}")
        elif index == last_index and char == " ":
            escaped.append("\\ ")
        else:
            escaped.append(char)
    return "".join(escaped)


def derive_surname(display_name: str, fallback: str) -> str:
    pieces = [piece for piece in display_name.strip().split(" ") if piece]
    if not pieces:
        return fallback
    return pieces[-1]


def deferred_from_coro(coro: Any) -> defer.Deferred[Any]:
    return defer.Deferred.fromFuture(asyncio.create_task(coro))


async def await_maybe_deferred(value: Any) -> Any:
    if isinstance(value, defer.Deferred):
        return await value.asFuture(asyncio.get_running_loop())
    return value
