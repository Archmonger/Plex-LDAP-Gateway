from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest
import twisted.internet as twisted_internet
from twisted.internet import asyncioreactor, defer, error

import plex_ldap_gateway.__main__ as entry
from plex_ldap_gateway import runtime
from plex_ldap_gateway.config import Settings, _get_bool, _get_float, _get_int, _require
from plex_ldap_gateway.runtime import LDAPListener


def make_settings() -> Settings:
    return Settings.from_env(
        {
            "PLEX_OWNER_TOKEN": "owner-token",
            "PLEX_MACHINE_IDENTIFIER": "machine-1",
            "PLEX_LDAP_BASE_DN": "dc=plex,dc=ldap",
        }
    )


def test_config_helpers_and_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "PLEX_BASE_URL",
        "PLEX_CLIENT_IDENTIFIER",
        "PLEX_CLIENT_PRODUCT",
        "PLEX_CLIENT_VERSION",
        "PLEX_TIMEOUT_SECONDS",
        "PLEX_LDAP_STRICT_MACHINE_MATCH",
        "PLEX_LDAP_REFRESH_SECONDS",
        "PLEX_LDAP_BASE_DN",
        "PLEX_LDAP_HOST",
        "PLEX_LDAP_PORT",
        "PLEX_HTTP_HOST",
        "PLEX_HTTP_PORT",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("PLEX_OWNER_TOKEN", "owner-token")
    monkeypatch.setenv("PLEX_MACHINE_IDENTIFIER", "machine-1")

    settings = Settings.from_env()

    assert settings.plex_base_url == "https://plex.tv"
    assert settings.plex_client_identifier == "plex-ldap-gateway-machine-1"
    assert settings.plex_timeout_seconds == 10.0
    assert settings.strict_machine_match is True
    assert settings.directory_refresh_seconds == 300
    assert settings.http_port == 7576
    assert settings.users_dn == "ou=users,dc=plex,dc=ldap"
    assert _get_bool("FLAG", {}, False) is False
    assert _get_bool("FLAG", {"FLAG": "On"}, False) is True
    assert _get_int("COUNT", {}, 4) == 4
    assert _get_int("COUNT", {"COUNT": " 7 "}, 4) == 7
    assert _get_float("TIMEOUT", {}, 1.5) == 1.5
    assert _get_float("TIMEOUT", {"TIMEOUT": " 2.5 "}, 1.5) == 2.5

    with pytest.raises(ValueError, match="Missing required environment variable: REQUIRED"):
        _require("REQUIRED", {})


def test_asgi_module_uses_create_app(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()

    import plex_ldap_gateway.app as app_module

    monkeypatch.setattr(app_module, "create_app", lambda: sentinel)
    sys.modules.pop("plex_ldap_gateway.asgi", None)
    module = importlib.import_module("plex_ldap_gateway.asgi")

    assert module.app is sentinel


@pytest.mark.asyncio
async def test_serve_installs_reactor_and_runs_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    settings = make_settings()
    fake_app = object()

    import plex_ldap_gateway.app as app_module

    monkeypatch.setattr(entry, "install_asyncio_reactor", lambda loop: captured.setdefault("loop", loop))
    monkeypatch.setattr(app_module, "create_app", lambda **_: fake_app)

    class FakeConfig:
        def __init__(self, app, host, port):
            captured["config"] = (app, host, port)

    class FakeServer:
        def __init__(self, config):
            captured["server_config"] = config

        async def serve(self) -> None:
            captured["served"] = True

    monkeypatch.setattr(entry.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(entry.uvicorn, "Server", FakeServer)

    await entry._serve(settings)

    assert captured["config"] == (fake_app, settings.http_host, settings.http_port)
    assert captured["served"] is True
    assert captured["loop"] is not None


def test_main_uses_runner_with_platform_loop_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    settings = make_settings()
    loop_factory = object()

    class FakeRunner:
        def __init__(self, *, loop_factory):
            captured["loop_factory"] = loop_factory

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            captured["closed"] = True

        def run(self, target):
            captured["target"] = target

    monkeypatch.setattr(entry, "Settings", SimpleNamespace(from_env=lambda: settings))
    monkeypatch.setattr(entry, "_serve", lambda active_settings: ("serve", active_settings))
    monkeypatch.setattr(entry, "new_event_loop", loop_factory)
    monkeypatch.setattr(entry.asyncio, "Runner", FakeRunner)

    entry.main()

    assert captured["loop_factory"] is loop_factory
    assert captured["target"] == ("serve", settings)
    assert captured["closed"] is True


def test_new_event_loop_uses_winloop_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()

    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winloop", SimpleNamespace(new_event_loop=lambda: sentinel))

    assert runtime.new_event_loop() is sentinel


def test_new_event_loop_uses_uvloop_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()

    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "uvloop", SimpleNamespace(new_event_loop=lambda: sentinel))

    assert runtime.new_event_loop() is sentinel


def test_new_event_loop_falls_back_to_asyncio(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()

    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr(runtime.asyncio, "new_event_loop", lambda: sentinel)
    monkeypatch.setitem(sys.modules, "uvloop", None)

    assert runtime.new_event_loop() is sentinel


def test_ensure_windows_reactor_compatible_loop_handles_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    runtime._ensure_windows_reactor_compatible_loop(object())

    monkeypatch.setattr(runtime.sys, "platform", "win32")

    class CompatibleLoop:
        def add_reader(self, *args, **kwargs) -> None:
            return None

        def add_writer(self, *args, **kwargs) -> None:
            return None

    class ProactorEventLoop:
        pass

    runtime._ensure_windows_reactor_compatible_loop(CompatibleLoop())

    with pytest.raises(RuntimeError, match="Windows requires a Twisted-compatible asyncio loop"):
        runtime._ensure_windows_reactor_compatible_loop(ProactorEventLoop())


def test_install_asyncio_reactor_uses_running_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = object()
    calls: list[tuple[str, object]] = []

    class AsyncioSelectorReactor:
        pass

    fake_reactor = AsyncioSelectorReactor()

    monkeypatch.setattr(runtime.asyncio, "get_running_loop", lambda: loop)
    monkeypatch.setattr(
        runtime, "_ensure_windows_reactor_compatible_loop", lambda active_loop: calls.append(("ensure", active_loop))
    )
    monkeypatch.setattr(asyncioreactor, "install", lambda active_loop: calls.append(("install", active_loop)))
    monkeypatch.setattr(twisted_internet, "reactor", fake_reactor, raising=False)

    assert runtime.install_asyncio_reactor() is fake_reactor
    assert calls == [("ensure", loop), ("install", loop)]


def test_install_asyncio_reactor_rejects_incompatible_reactor(monkeypatch: pytest.MonkeyPatch) -> None:
    class AlreadyInstalled(Exception):
        pass

    monkeypatch.setattr(runtime, "_ensure_windows_reactor_compatible_loop", lambda active_loop: None)
    monkeypatch.setattr(error, "ReactorAlreadyInstalledError", AlreadyInstalled, raising=False)

    def raise_installed(_loop: object) -> None:
        raise AlreadyInstalled

    monkeypatch.setattr(asyncioreactor, "install", raise_installed)
    monkeypatch.setattr(twisted_internet, "reactor", object(), raising=False)

    with pytest.raises(RuntimeError, match="Twisted reactor is already installed"):
        runtime.install_asyncio_reactor(object())


class FakePort:
    def __init__(self, stop_result) -> None:
        self.stop_result = stop_result
        self.stop_calls = 0

    def stopListening(self):
        self.stop_calls += 1
        return self.stop_result


class FakeReactor:
    def __init__(self, port: FakePort) -> None:
        self.port = port
        self.calls: list[tuple[int, object, str]] = []

    def listenTCP(self, port: int, factory: object, interface: str):
        self.calls.append((port, factory, interface))
        return self.port


@pytest.mark.asyncio
async def test_ldap_listener_start_and_stop_with_deferred(monkeypatch: pytest.MonkeyPatch) -> None:
    created_factories: list[object] = []
    stop_result = defer.Deferred()
    stop_result.callback(None)
    port = FakePort(stop_result)
    reactor = FakeReactor(port)

    from plex_ldap_gateway import ldap_server

    class FakeFactory:
        def __init__(self, directory_service: object) -> None:
            created_factories.append(directory_service)

    monkeypatch.setattr(runtime, "install_asyncio_reactor", lambda loop: reactor)
    monkeypatch.setattr(ldap_server, "PlexLDAPServerFactory", FakeFactory)

    listener = LDAPListener(make_settings(), object())

    assert listener.is_listening is False

    await listener.start()
    await listener.start()
    await listener.stop()

    assert listener.is_listening is False
    assert created_factories
    assert len(reactor.calls) == 1
    assert port.stop_calls == 1


@pytest.mark.asyncio
async def test_ldap_listener_start_uses_existing_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings()
    port = FakePort(None)
    reactor = FakeReactor(port)
    existing_factory = object()
    listener = LDAPListener(settings, object())
    listener.factory = existing_factory

    monkeypatch.setattr(runtime, "install_asyncio_reactor", lambda loop: reactor)

    await listener.start()

    assert reactor.calls == [(settings.ldap_port, existing_factory, settings.ldap_host)]


@pytest.mark.asyncio
async def test_ldap_listener_stop_handles_missing_and_nondeferred_result() -> None:
    listener = LDAPListener(make_settings(), object())

    await listener.stop()

    port = FakePort(None)
    listener._listening_port = port
    await listener.stop()

    assert listener.is_listening is False
    assert port.stop_calls == 1
