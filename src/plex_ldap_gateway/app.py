"""ASGI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from .config import Settings
from .directory import PlexDirectoryService
from .errors import PlexLDAPError
from .logging_utils import configure_logging
from .plex import AsyncPlexClient
from .runtime import LDAPListener

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    directory_service: PlexDirectoryService | None = None,
    ldap_listener: LDAPListener | None = None,
) -> Starlette:
    active_settings = settings or Settings.from_env()
    configure_logging(active_settings)
    active_directory_service = directory_service or PlexDirectoryService(
        active_settings,
        AsyncPlexClient(active_settings),
    )
    active_listener = ldap_listener or LDAPListener(active_settings, active_directory_service)
    logger.debug(
        "Creating ASGI application with log_level=%s output=%s",
        logging.getLevelName(active_settings.log_level),
        active_settings.log_output,
    )

    @asynccontextmanager
    async def lifespan(app: Starlette):
        app.state.settings = active_settings
        app.state.directory_service = active_directory_service
        app.state.ldap_listener = active_listener
        logger.info("Application startup beginning")
        try:
            await active_directory_service.refresh(force=True)
            await active_listener.start()
            logger.info("Application startup complete")
            yield
        finally:
            logger.info("Application shutdown beginning")
            await active_listener.stop()
            await active_directory_service.aclose()
            logger.info("Application shutdown complete")

    async def healthz(_request):
        snapshot = active_directory_service.current_snapshot
        logger.debug("Serving /healthz with %s directory users", snapshot.user_count)
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
        logger.debug("Serving /readyz with force=%s", force)
        try:
            snapshot = await active_directory_service.refresh(force=force)
        except PlexLDAPError as error:
            logger.warning("Readiness check failed with service error: %s", error)
            return JSONResponse(
                {"status": "error", "detail": str(error)},
                status_code=503,
            )
        except Exception as error:
            logger.exception("Readiness check failed with unexpected error")
            return JSONResponse(
                {"status": "error", "detail": error.__class__.__name__},
                status_code=503,
            )

        logger.debug("Readiness check succeeded with %s directory users", snapshot.user_count)
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
