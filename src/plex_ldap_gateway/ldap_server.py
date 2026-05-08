"""LDAP protocol integration built on Ldaptor."""

from __future__ import annotations

from ldaptor import interfaces
from ldaptor.protocols import pureldap
from ldaptor.protocols.ldap import distinguishedname, ldaperrors
from ldaptor.protocols.ldap.ldapserver import LDAPServer
from twisted.internet import protocol
from twisted.python.components import registerAdapter

from .directory import PlexDirectoryService
from .errors import PlexAuthenticationError
from .utils import await_maybe_deferred, deferred_from_coro

LDAP_PROTOCOL_VERSION = 3


class PlexLDAPServer(LDAPServer):
    fail_LDAPBindRequest = pureldap.LDAPBindResponse

    def handle_LDAPBindRequest(self, request, controls, _reply):
        if request.version != LDAP_PROTOCOL_VERSION:
            raise ldaperrors.LDAPProtocolError(f"Version {request.version} not supported")

        self.checkControls(controls)

        if request.dn in {b"", ""}:
            self.boundUser = None
            return pureldap.LDAPBindResponse(resultCode=ldaperrors.Success.resultCode)

        return deferred_from_coro(self._bind_against_plex(request))

    def handle_LDAPSearchRequest(self, request, controls, reply):
        return deferred_from_coro(self._refresh_then_search(request, controls, reply))

    async def _bind_against_plex(self, request):
        try:
            user = await self.factory.directory_service.authenticate_bind(request.dn, request.auth)
        except PlexAuthenticationError as error:
            raise ldaperrors.LDAPInvalidCredentials(str(error)) from error
        except Exception as error:
            raise ldaperrors.LDAPUnavailable("Authentication backend unavailable") from error

        root = interfaces.IConnectedLDAPEntry(self.factory)
        entry = await await_maybe_deferred(root.lookup(distinguishedname.DistinguishedName(user.dn)))
        self.boundUser = entry
        return pureldap.LDAPBindResponse(
            resultCode=ldaperrors.Success.resultCode,
            matchedDN=user.dn,
        )

    async def _refresh_then_search(self, request, controls, reply):
        await self.factory.directory_service.refresh()
        response = LDAPServer.handle_LDAPSearchRequest(self, request, controls, reply)
        return await await_maybe_deferred(response)


class PlexLDAPServerFactory(protocol.ServerFactory):
    protocol = PlexLDAPServer

    def __init__(self, directory_service: PlexDirectoryService) -> None:
        self.directory_service = directory_service

    @property
    def root(self):
        return self.directory_service.current_snapshot.root


registerAdapter(lambda factory: factory.root, PlexLDAPServerFactory, interfaces.IConnectedLDAPEntry)
