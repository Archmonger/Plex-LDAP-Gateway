from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest
import twisted.internet as twisted_internet
from twisted.internet import asyncioreactor, defer, error

import plex_ldap_gateway.__main__ as entry
import plex_ldap_gateway.runtime as runtime
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


def test_main_sets_selector_policy_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    settings = make_settings()
    policy = object()

    monkeypatch.setattr(entry, "Settings", SimpleNamespace(from_env=lambda: settings))
    monkeypatch.setattr(entry, "_serve", lambda active_settings: ("serve", active_settings))
    monkeypatch.setattr(entry.asyncio, "run", lambda target: captured.setdefault("target", target))
    monkeypatch.setattr(entry.asyncio, "WindowsSelectorEventLoopPolicy", lambda: policy, raising=False)
    monkeypatch.setattr(entry.asyncio, "set_event_loop_policy", lambda value: captured.setdefault("policy", value))

    entry.main()

    assert captured["policy"] is policy
    assert captured["target"] == ("serve", settings)


def test_main_tolerates_missing_windows_selector_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    settings = make_settings()

    def missing_policy() -> object:
        raise AttributeError("missing")

    monkeypatch.setattr(entry, "Settings", SimpleNamespace(from_env=lambda: settings))
    monkeypatch.setattr(entry, "_serve", lambda active_settings: ("serve", active_settings))
    monkeypatch.setattr(entry.asyncio, "run", lambda target: captured.setdefault("target", target))
    monkeypatch.setattr(entry.asyncio, "WindowsSelectorEventLoopPolicy", missing_policy, raising=False)
    monkeypatch.setattr(entry.asyncio, "set_event_loop_policy", lambda value: captured.setdefault("policy", value))

    entry.main()

    assert captured["target"] == ("serve", settings)
    assert "policy" not in captured


def test_ensure_windows_selector_loop_handles_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    runtime._ensure_windows_selector_loop(object())

    monkeypatch.setattr(runtime.sys, "platform", "win32")

    class SelectorEventLoop:
        pass

    class ProactorEventLoop:
        pass

    runtime._ensure_windows_selector_loop(SelectorEventLoop())

    with pytest.raises(RuntimeError, match="Windows requires a selector-based asyncio loop"):
        runtime._ensure_windows_selector_loop(ProactorEventLoop())


def test_install_asyncio_reactor_uses_running_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = object()
    calls: list[tuple[str, object]] = []

    class AsyncioSelectorReactor:
        pass

    fake_reactor = AsyncioSelectorReactor()

    monkeypatch.setattr(runtime.asyncio, "get_running_loop", lambda: loop)
    monkeypatch.setattr(runtime, "_ensure_windows_selector_loop", lambda active_loop: calls.append(("ensure", active_loop)))
    monkeypatch.setattr(asyncioreactor, "install", lambda active_loop: calls.append(("install", active_loop)))
    monkeypatch.setattr(twisted_internet, "reactor", fake_reactor, raising=False)

    assert runtime.install_asyncio_reactor() is fake_reactor
    assert calls == [("ensure", loop), ("install", loop)]


def test_install_asyncio_reactor_rejects_incompatible_reactor(monkeypatch: pytest.MonkeyPatch) -> None:
    class AlreadyInstalled(Exception):
        pass

    monkeypatch.setattr(runtime, "_ensure_windows_selector_loop", lambda active_loop: None)
    monkeypatch.setattr(error, "ReactorAlreadyInstalledError", AlreadyInstalled, raising=False)

    def raise_installed(_loop: object) -> None:
        raise AlreadyInstalled()

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

    import plex_ldap_gateway.ldap_server as ldap_server

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
