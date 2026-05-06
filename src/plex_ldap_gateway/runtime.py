"""Runtime helpers for sharing asyncio with Twisted."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Settings
    from .directory import PlexDirectoryService


def _ensure_windows_selector_loop(loop: asyncio.AbstractEventLoop) -> None:
    if sys.platform != "win32":
        return
    if "SelectorEventLoop" not in loop.__class__.__name__:
        raise RuntimeError(
            "Windows requires a selector-based asyncio loop for Twisted integration. "
            "Use the bundled plex-ldap-gateway runner or set WindowsSelectorEventLoopPolicy before startup."
        )


def install_asyncio_reactor(loop: asyncio.AbstractEventLoop | None = None):
    from twisted.internet import asyncioreactor, error

    active_loop = loop or asyncio.get_running_loop()
    _ensure_windows_selector_loop(active_loop)
    try:
        asyncioreactor.install(active_loop)
    except error.ReactorAlreadyInstalledError:
        pass

    from twisted.internet import reactor

    if "AsyncioSelectorReactor" not in reactor.__class__.__name__:
        raise RuntimeError("Twisted reactor is already installed and is not asyncio-compatible")
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
            from .ldap_server import PlexLDAPServerFactory

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
