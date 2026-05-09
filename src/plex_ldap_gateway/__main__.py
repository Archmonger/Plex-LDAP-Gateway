"""Command-line entrypoint."""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from .config import Settings
from .logging_utils import configure_logging
from .runtime import install_asyncio_reactor, new_event_loop

logger = logging.getLogger(__name__)


async def _serve(settings: Settings) -> None:
    logger.info(
        "Starting service runtime with HTTP %s:%s and LDAP %s:%s",
        settings.http_host,
        settings.http_port,
        settings.ldap_host,
        settings.ldap_port,
    )
    install_asyncio_reactor(asyncio.get_running_loop())

    from .app import create_app

    app = create_app(settings=settings)
    config = uvicorn.Config(
        app,
        host=settings.http_host,
        port=settings.http_port,
    )
    server = uvicorn.Server(config)
    await server.serve()
    logger.info("HTTP server exited")


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings)
    logger.info("Launching plex-ldap-gateway")
    with asyncio.Runner(loop_factory=new_event_loop) as runner:
        runner.run(_serve(settings))
