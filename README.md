# Plex LDAP Gateway

Some apps only know how to log in against LDAP. Plex does not speak LDAP. This project sits in the middle and makes your Plex users look like LDAP users so those apps can authenticate with Plex-backed identities.

When an LDAP client tries to log in, this service asks Plex to verify the username and password, and then returns an LDAP login success or failure. The LDAP side is only a read-only view of Plex. Passwords stay in Plex rather than being copied into a separate LDAP database.

## What the service exposes

- An LDAPv3 listener for bind, search, and unbind operations
- A small HTTP server with health and readiness endpoints
- An in-memory LDAP tree built from the Plex owner account and matching shared users
- A single process that runs both the HTTP endpoints and the LDAP listener

## Common client configuration

Here is an example client configuration, written from the perspective of the `Jellyfin LDAP-Auth` plugin:

| Setting | Value | Notes |
| --- | --- | --- |
| `LDAP Server` | `192.168.1.123` | Replace this value with the host/IP that publishes the LDAP port. Can be set to a container name if on a shared Docker network. |
| `LDAP Port` | `1389` | Use the published port if you changed the default. |
| `Secure LDAP` | `Disabled` | Turn on only when an external TLS terminator exposes LDAPS in front of the gateway. |
| `StartTLS` | `Disabled` | The gateway does not advertise StartTLS. |
| `Allow Password Change` | `Disabled` | The directory is read-only. Passwords are managed by Plex. |
| `Password Reset Url` | `https://app.plex.tv/auth/#?resetPassword` | Optional convenience link; password changes do not happen through LDAP here. |
| `LDAP Bind User` | blank | Leave blank to use anonymous binds. |
| `LDAP Bind User Password` | blank | Not needed when the bind user is blank. |
| `LDAP Base DN for searches` | `ou=users,dc=plex,dc=ldap` | This should match the base DN used in the gateway configuration `GATEWAY_LDAP_BASE_DN`. |
| `LDAP Search Filter` | `(objectClass=inetOrgPerson)` | Used as the base filter; the plugin adds an OR over the search attributes below. |
| `LDAP Search Attributes` | `uid, cn, mail, plexUsername` | Allow users sign in with any username-like attributes. |
| `LDAP Uid Attribute` | `uid` | Unique and always present. |
| `LDAP Username Attribute` | `cn` | Default for Jellyfin usernames created from LDAP. Can be set to `uid` if you want to keep your new Plex-LDAP users completely separate. |
| `LDAP Password Attribute` | blank | Only needed when an LDAP server supports password changes, which this gateway does not. |
| `Enable profile image synchronization` | `Disabled` | The gateway does not expose a profile image attribute. |
| `Remove profile images not in LDAP` | `Disabled` | The gateway does not expose a profile image attribute, thus this setting has no effect. |
| `LDAP Admin Base DN` | `ou=users,dc=plex,dc=ldap` | Set to the value you put within `LDAP Base DN for searches`. Whether an account is an admin is determined via the filter below. |
| `LDAP Admin Filter` | `(employeeType=owner)` | Automatically grant Jellyfin admin access to the Plex owner account. |
| `Enable Admin Filter 'memberUid' mode` | `Disabled` | Not used by this directory layout. |
| `Enable User Creation` | `Enabled` | Creates an equivalent Jellyfin user on first successful LDAP login. The new Jellyfin user will be auto-configured to authenticate against the LDAP server, rather than the local Jellyfin database. Note: If a user already exists with the same `LDAP Username Attribute`, they will not be automatically reconfigured to authenticate against LDAP. However, they will be able to login if their credentials match. |

## Environment variables

Required application settings:

- `PLEX_OWNER_TOKEN`: Plex owner or admin token used to enumerate and authorize directory users
- `PLEX_MACHINE_IDENTIFIER`: Plex Media Server `machineIdentifier` used to scope directory access

Optional application settings:

- `PLEX_BASE_URL`: defaults to `https://plex.tv`
- `PLEX_CLIENT_IDENTIFIER`: defaults to `plex-ldap-gateway-{PLEX_MACHINE_IDENTIFIER}`
- `PLEX_CLIENT_PRODUCT`: defaults to `Plex LDAP Gateway`
- `PLEX_CLIENT_VERSION`: defaults to the package version
- `PLEX_TIMEOUT_SECONDS`: defaults to `10`
- `GATEWAY_LDAP_STRICT_MACHINE_MATCH`: defaults to `true`
- `GATEWAY_LDAP_REFRESH_SECONDS`: defaults to `300`
- `GATEWAY_LDAP_BASE_DN`: defaults to `dc=plex,dc=ldap`
- `GATEWAY_LDAP_HOST`: defaults to `0.0.0.0`
- `GATEWAY_LDAP_PORT`: defaults to `1389`
- `GATEWAY_HTTP_HOST`: defaults to `127.0.0.1`
- `GATEWAY_HTTP_PORT`: defaults to `7576`
- `GATEWAY_LOG_LEVEL`: defaults to `ERROR`
- `GATEWAY_LOG_OUTPUT`: defaults to `console`; supported values are `console`, `file`, and `both`
- `GATEWAY_LOG_FILE_PATH`: defaults to `plex-ldap-gateway.log`; used when `GATEWAY_LOG_OUTPUT` includes file output

Docker specific settings:

- `PUID`, `PGID`, `TZ`
- `GATEWAY_LDAP_BIND_ADDRESS`, `GATEWAY_HTTP_BIND_ADDRESS`
- `SERVICE_CRASH_MAX_ATTEMPTS`, `SERVICE_CRASH_WINDOW_SECONDS`, `SERVICE_CRASH_BACKOFF_SECONDS`

`GATEWAY_LDAP_BIND_ADDRESS` and `GATEWAY_HTTP_BIND_ADDRESS` only control host port publishing in [compose.yml](compose.yml). They are not application settings. Inside the container, the service binds with `GATEWAY_LDAP_HOST=0.0.0.0` and `GATEWAY_HTTP_HOST=0.0.0.0`.

## Running locally

Set the required environment variables in your shell, then install and start the bundled runner:

```powershell
pip install -e .
plex-ldap-gateway
```

Alternatively, you can also start the ASGI app through an external server:

```powershell
uvicorn --factory plex_ldap_gateway.app:create_app --host 127.0.0.1 --port 7576
```

If you do that, your ASGI server must create a Twisted-compatible asyncio loop before startup so the LDAP listener can attach to the same process.

## Running in Docker

The repository ships deployment artifacts for LinuxServer-style container environments:

- [Dockerfile](Dockerfile) builds the image from `lscr.io/linuxserver/baseimage-ubuntu:noble`
- [compose.yml](compose.yml) runs the container locally with health checks and restart limits
- [unraid/plex-ldap-gateway.xml](unraid/plex-ldap-gateway.xml) provides an Unraid template

A typical compose flow looks like this:

```powershell
copy .env.example .env
docker compose up -d --build
```

If you prefer, you can export the same variables from your shell instead of using a `.env` file.

The compose template publishes LDAP on all interfaces by default and HTTP on localhost by default:

- LDAP publish address: `GATEWAY_LDAP_BIND_ADDRESS`, default `0.0.0.0`
- HTTP publish address: `GATEWAY_HTTP_BIND_ADDRESS`, default `127.0.0.1`

When file logging is enabled in the shipped container artifacts, the recommended path is `/config/logs/plex-ldap-gateway.log` so logs persist on the mounted config volume.

## Technical overview

### How the directory is built

1. Load settings from environment variables.
2. Fetch the Plex owner account with `PLEX_OWNER_TOKEN`.
3. Fetch the Plex users shared by that owner.
4. If `GATEWAY_LDAP_STRICT_MACHINE_MATCH=true`, keep only users who can access `PLEX_MACHINE_IDENTIFIER`.
5. Build a read-only LDAP tree rooted at `GATEWAY_LDAP_BASE_DN`, with all users under `ou=users`.

### Binding behavior

Credentialed binds can resolve a user by:

- Full DN, such as `uid=alice,ou=users,dc=plex,dc=ldap`
- Plain identifier, such as `alice`
- Email address, such as `alice@example.com`
- Simple `attr=value` input, such as `uid=alice`

Short-form identifiers only work when they map to exactly one generated user in the current directory snapshot.

An empty bind DN is treated as an anonymous bind. Non-anonymous binds with empty passwords are rejected.

### LDAP layout

By default, the generated directory looks like this:

```text
dc=plex,dc=ldap
+-- ou=users
	+-- uid=<generated-user-id>
```

The root DN is configurable with `GATEWAY_LDAP_BASE_DN`. User entries always live under `ou=users,<base DN>`.

Each generated user entry is read-only and exposes these LDAP attributes:

| Attribute | Value | Notes |
| --- | --- | --- |
| `objectClass` | `top`, `person`, `organizationalPerson`, `inetOrgPerson` | Fixed for every generated user |
| `uid` | Generated from Plex username, email, title, or a fallback value | Also used in the LDAP DN |
| `cn` | Plex display name | Uses Plex title, then username, email, UUID, or `plex-user` |
| `sn` | Derived surname | Falls back to `uid` when a surname cannot be inferred |
| `displayName` | Same display name as `cn` | Always present |
| `employeeType` | `owner` or `shared` | Shows how the user entered the directory |
| `plexUsername` | Plex username | Present only when Plex returns one |
| `mail` | Plex email address | Present only when Plex returns one |
| `userPrincipalName` | Same value as `mail` | Present only when Plex returns an email |

Internally the service also tracks Plex identifiers, bind aliases, and uniqueness indexes, but those internal fields are not exposed as LDAP attributes.

### HTTP endpoints

- `GET /healthz`: process status, directory size, last refresh timestamp, and listener state
- `GET /readyz`: readiness state, refreshing from Plex when the cache is stale
- `GET /readyz?force=1`: forces a fresh Plex directory refresh before responding

### Testing and CI

Useful local commands:

```powershell
hatch fmt --check
hatch test -m "not live_plex"
hatch test --cover
```

Live Plex tests need these environment variables:

- `PLEX_OWNER_TOKEN`
- `PLEX_MACHINE_IDENTIFIER`
- `PLEX_TEST_BIND_LOGIN`
- `PLEX_TEST_BIND_PASSWORD`

Optional live-test variables:

- `PLEX_TEST_BIND_IDENTITY`: defaults to `PLEX_TEST_BIND_LOGIN`
- `PLEX_TEST_EXPECTED_USERNAME`
- `PLEX_TEST_EXPECTED_EMAIL`

The GitHub Actions workflow currently runs:

- `hatch fmt --check`
- `hatch test --cover` on Python 3.13 and 3.14
- Docker image build validation
- `docker compose` startup and smoke validation when Plex secrets are available

### Security and behavior notes

- LDAP simple bind sends credentials in cleartext unless you add transport security. Put this behind trusted network boundaries or wrap LDAP with TLS separately.
- The generated LDAP directory is read-only.
- Passwords are validated against Plex at bind time rather than stored in LDAP.
