"""Project-specific exceptions."""


class PlexLDAPError(Exception):
    """Base exception for the service."""


class PlexAPIError(PlexLDAPError):
    """Raised when Plex returns an unexpected response."""


class PlexAuthenticationError(PlexLDAPError):
    """Raised when Plex credentials are invalid."""


class PlexAuthorizationError(PlexLDAPError):
    """Raised when a Plex account is authenticated but not authorized."""
