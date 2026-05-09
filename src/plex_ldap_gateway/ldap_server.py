"""LDAP protocol integration built on Ldaptor."""

from __future__ import annotations

import logging

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


logger = logging.getLogger(__name__)


class PlexLDAPServer(LDAPServer):
    fail_LDAPBindRequest = pureldap.LDAPBindResponse

    def handle_LDAPBindRequest(self, request, controls, _reply):
        if request.version != LDAP_PROTOCOL_VERSION:
            logger.warning("Rejected LDAP bind request with unsupported version %s", request.version)
            raise ldaperrors.LDAPProtocolError(f"Version {request.version} not supported")

        self.checkControls(controls)

        if request.dn in {b"", ""}:
            self.boundUser = None
            logger.debug("Accepted anonymous LDAP bind request")
            return pureldap.LDAPBindResponse(resultCode=ldaperrors.Success.resultCode)

        logger.debug("Handling LDAP bind request")
        return deferred_from_coro(self._bind_against_plex(request))

    def handle_LDAPSearchRequest(self, request, controls, reply):
        logger.debug("Handling LDAP search request")
        return deferred_from_coro(self._refresh_then_search(request, controls, reply))

    async def _bind_against_plex(self, request):
        try:
            user = await self.factory.directory_service.authenticate_bind(request.dn, request.auth)
        except PlexAuthenticationError as error:
            logger.warning("LDAP bind failed due to invalid credentials")
            raise ldaperrors.LDAPInvalidCredentials(str(error)) from error
        except Exception as error:
            logger.exception("LDAP bind failed because the authentication backend is unavailable")
            raise ldaperrors.LDAPUnavailable("Authentication backend unavailable") from error

        root = interfaces.IConnectedLDAPEntry(self.factory)
        entry = await await_maybe_deferred(root.lookup(distinguishedname.DistinguishedName(user.dn)))
        self.boundUser = entry
        logger.info("LDAP bind succeeded for directory user %s", user.uid)
        return pureldap.LDAPBindResponse(
            resultCode=ldaperrors.Success.resultCode,
            matchedDN=user.dn,
        )

    async def _refresh_then_search(self, request, controls, reply):
        logger.debug("Refreshing directory snapshot before LDAP search")
        await self.factory.directory_service.refresh()
        response = LDAPServer.handle_LDAPSearchRequest(self, request, controls, reply)
        return await await_maybe_deferred(response)


class PlexLDAPServerFactory(protocol.ServerFactory):
    protocol = PlexLDAPServer

    def __init__(self, directory_service: PlexDirectoryService) -> None:
        self.directory_service = directory_service
        logger.debug("Created LDAP server factory")

    @property
    def root(self):
        return self.directory_service.current_snapshot.root


registerAdapter(lambda factory: factory.root, PlexLDAPServerFactory, interfaces.IConnectedLDAPEntry)
