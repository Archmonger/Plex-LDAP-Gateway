# Plex LDAP Gateway

Plex LDAP Gateway is a Python service that exposes a small ASGI control plane and runs a real LDAP listener in the same process. LDAP authentication is delegated to Plex, while authorization is scoped by a configured Plex owner token and Plex machine identifier.

## What it does

- Exposes LDAPv3 bind, search, and unbind behavior through Ldaptor
- Builds an in-memory LDAP tree from the Plex owner account plus Plex users who can access the configured server
- Accepts binds by full DN, plain username, email address, or simple `attr=value` forms such as `uid=alice`
- Verifies passwords against Plex _(with the username/password sign-in endpoint)_
- Publishes `GET /healthz` and `GET /readyz` through ASGI

## Configuration

Required environment variables:

- `PLEX_OWNER_TOKEN`: Plex owner or admin token used to enumerate shared users.
- `PLEX_MACHINE_IDENTIFIER`: Plex Media Server `machineIdentifier` used for authorization scoping.

Optional environment variables:

- `PLEX_BASE_URL`: defaults to `https://plex.tv`
- `PLEX_CLIENT_IDENTIFIER`: defaults to `plex-ldap-gateway-{PLEX_MACHINE_IDENTIFIER}`
- `PLEX_CLIENT_PRODUCT`: defaults to `Plex LDAP Gateway`
- `PLEX_CLIENT_VERSION`: defaults to the package version
- `PLEX_HTTP_HOST`: defaults to `127.0.0.1`
- `PLEX_HTTP_PORT`: defaults to `8000`
- `PLEX_LDAP_BASE_DN`: defaults to `dc=plex,dc=ldap`
- `PLEX_LDAP_HOST`: defaults to `0.0.0.0`
- `PLEX_LDAP_PORT`: defaults to `1389`
- `PLEX_LDAP_REFRESH_SECONDS`: defaults to `300`
- `PLEX_LDAP_STRICT_MACHINE_MATCH`: defaults to `true`
- `PLEX_TIMEOUT_SECONDS`: defaults to `10`

## Running Locally

Install the project into an environment and use the bundled runner:

```powershell
pip install -e .
plex-ldap-gateway
```

The bundled runner selects `uvloop` on non-Windows platforms and `winloop` on Windows before Twisted installs its asyncio reactor.

You can also point an ASGI server at the app factory:

```powershell
uvicorn --factory plex_ldap_gateway.app:create_app --host 127.0.0.1 --port 8000
```

If you do not use the bundled runner, ensure your ASGI server creates a Twisted-compatible asyncio loop before startup.

The packaged entrypoint reads all runtime inputs from the `environ` and passes those settings to the HTTP and LDAP startup.

## Docker Compose

The repository includes a LinuxServer-based image and a compose template:

- [Dockerfile](Dockerfile) uses `lscr.io/linuxserver/baseimage-ubuntu:noble` so the service can run under LinuxServer's `s6-overlay` conventions without Alpine-specific wheel friction.
- [compose.yml](compose.yml) builds and runs the container, exposes LDAP and HTTP ports, configures a health check, and limits container restart attempts with `restart: on-failure:5`.
- [.env.example](.env.example) contains the runtime and live-test variables expected by the app and the compose file.

To use it:

```powershell
copy .env.example .env
docker compose up -d --build
```

By default the HTTP endpoint binds to localhost on the host and the LDAP port binds on all interfaces. Adjust `PLEX_HTTP_BIND_ADDRESS` and `PLEX_LDAP_BIND_ADDRESS` in `.env` if you need different exposure.

## Endpoints

- `GET /healthz`: local process status and current directory snapshot metadata
- `GET /readyz`: cached readiness state, refreshing from Plex when the cache is stale
- `GET /readyz?force=1`: forces a Plex refresh before responding

## Live Plex Tests

The repository includes live integration tests that require the presence of specific environment variables.

Required live-test variables:

- `PLEX_OWNER_TOKEN`
- `PLEX_MACHINE_IDENTIFIER`
- `PLEX_TEST_BIND_LOGIN`
- `PLEX_TEST_BIND_PASSWORD`

Optional live-test variables:

- `PLEX_TEST_BIND_IDENTITY`: defaults to `PLEX_TEST_BIND_LOGIN`
- `PLEX_TEST_EXPECTED_USERNAME`
- `PLEX_TEST_EXPECTED_EMAIL`

When these values are present, the live tests validate:

- Directory refresh against the real Plex account
- Bind authorization using the real Plex credentials
- LDAP bind round-trip through the Ldaptor server class

## Security notes

- LDAP simple bind sends credentials in cleartext unless you add transport security. Run this only behind trusted network boundaries or wrap LDAP with TLS separately.
- If Plex changes the sign-in API, this service will need a corresponding update.
- The LDAP directory is intentionally read-only.

## Q&A: Why split LDAP and ASGI?

ASGI cannot terminate raw LDAP traffic on its own, so the service uses two components on one asyncio event loop:

- Starlette provides HTTP health and readiness endpoints.
- Ldaptor and Twisted provide the LDAP protocol listener.
