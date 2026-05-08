"""Command-line entrypoint."""

from __future__ import annotations

import asyncio

import uvicorn

from .config import Settings
from .runtime import install_asyncio_reactor, new_event_loop


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
    settings = Settings.from_env()
    with asyncio.Runner(loop_factory=new_event_loop) as runner:
        runner.run(_serve(settings))
