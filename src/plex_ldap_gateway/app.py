"""ASGI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from .config import Settings
from .directory import PlexDirectoryService
from .errors import PlexLDAPError
from .plex import AsyncPlexClient
from .runtime import LDAPListener


def create_app(
    settings: Settings | None = None,
    directory_service: PlexDirectoryService | None = None,
    ldap_listener: LDAPListener | None = None,
) -> Starlette:
    active_settings = settings or Settings.from_env()
    active_directory_service = directory_service or PlexDirectoryService(
        active_settings,
        AsyncPlexClient(active_settings),
    )
    active_listener = ldap_listener or LDAPListener(active_settings, active_directory_service)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        app.state.settings = active_settings
        app.state.directory_service = active_directory_service
        app.state.ldap_listener = active_listener
        try:
            await active_directory_service.refresh(force=True)
            await active_listener.start()
            yield
        finally:
            await active_listener.stop()
            await active_directory_service.aclose()

    async def healthz(request):
        snapshot = active_directory_service.current_snapshot
        return JSONResponse(
            {
                "status": "ok",
                "directory_users": snapshot.user_count,
                "last_refresh": snapshot.generated_at.isoformat(),
                "ldap": {
                    "host": active_settings.ldap_host,
                    "port": active_settings.ldap_port,
                    "listening": active_listener.is_listening,
                },
                "plex": {
                    "machine_identifier": active_settings.plex_machine_identifier,
                    "strict_machine_match": active_settings.strict_machine_match,
                },
            }
        )

    async def readyz(request):
        force = request.query_params.get("force", "0") in {"1", "true", "yes"}
        try:
            snapshot = await active_directory_service.refresh(force=force)
        except PlexLDAPError as error:
            return JSONResponse(
                {"status": "error", "detail": str(error)},
                status_code=503,
            )
        except Exception as error:
            return JSONResponse(
                {"status": "error", "detail": error.__class__.__name__},
                status_code=503,
            )

        return JSONResponse(
            {
                "status": "ready",
                "directory_users": snapshot.user_count,
                "last_refresh": snapshot.generated_at.isoformat(),
            }
        )

    routes = [
        Route("/healthz", healthz),
        Route("/readyz", readyz),
    ]
    return Starlette(debug=False, routes=routes, lifespan=lifespan)
