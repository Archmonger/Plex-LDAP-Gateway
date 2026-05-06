"""Command-line entrypoint."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import uvicorn

from .config import Settings
from .runtime import install_asyncio_reactor


async def _serve(settings: Settings) -> None:
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


def main() -> None:
    with suppress(AttributeError):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    settings = Settings.from_env()
    asyncio.run(_serve(settings))
