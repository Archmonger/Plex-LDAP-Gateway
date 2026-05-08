"""Runtime helpers for sharing asyncio with Twisted."""

from __future__ import annotations

import asyncio
import sys
from contextlib import suppress
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plex_ldap_gateway.config import Settings
    from plex_ldap_gateway.directory import PlexDirectoryService


WINDOWS_LOOP_ERROR = (
    "Windows requires a Twisted-compatible asyncio loop. "
    "Use the bundled plex-ldap-gateway runner so winloop is selected before startup."
)

INCOMPATIBLE_REACTOR_ERROR = "Twisted reactor is already installed and is not asyncio-compatible"


def new_event_loop() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        try:
            winloop = import_module("winloop")
        except ImportError:
            return asyncio.new_event_loop()
        return winloop.new_event_loop()

    try:
        uvloop = import_module("uvloop")
    except ImportError:
        return asyncio.new_event_loop()
    return uvloop.new_event_loop()


def _ensure_windows_reactor_compatible_loop(loop: asyncio.AbstractEventLoop) -> None:
    if sys.platform != "win32":
        return
    if "ProactorEventLoop" in loop.__class__.__name__:
        raise RuntimeError(WINDOWS_LOOP_ERROR)
    if not callable(getattr(loop, "add_reader", None)) or not callable(getattr(loop, "add_writer", None)):
        raise TypeError(WINDOWS_LOOP_ERROR)


def install_asyncio_reactor(loop: asyncio.AbstractEventLoop | None = None):
    from twisted.internet import asyncioreactor, error

    active_loop = loop or asyncio.get_running_loop()
    _ensure_windows_reactor_compatible_loop(active_loop)
    with suppress(error.ReactorAlreadyInstalledError):
        asyncioreactor.install(active_loop)

    from twisted.internet import reactor

    if "AsyncioSelectorReactor" not in reactor.__class__.__name__:
        raise RuntimeError(INCOMPATIBLE_REACTOR_ERROR)
    return reactor


class LDAPListener:
    def __init__(self, settings: Settings, directory_service: PlexDirectoryService) -> None:
        self.settings = settings
        self.directory_service = directory_service
        self.factory = None
        self._listening_port = None

    @property
    def is_listening(self) -> bool:
        return self._listening_port is not None

    async def start(self) -> None:
        if self._listening_port is not None:
            return
        reactor = install_asyncio_reactor(asyncio.get_running_loop())
        if self.factory is None:
            from plex_ldap_gateway.ldap_server import (
                PlexLDAPServerFactory,
            )

            self.factory = PlexLDAPServerFactory(self.directory_service)
        self._listening_port = reactor.listenTCP(
            self.settings.ldap_port,
            self.factory,
            interface=self.settings.ldap_host,
        )

    async def stop(self) -> None:
        from twisted.internet import defer

        if self._listening_port is None:
            return
        deferred_stop = self._listening_port.stopListening()
        if isinstance(deferred_stop, defer.Deferred):
            await deferred_stop.asFuture(asyncio.get_running_loop())
        self._listening_port = None
