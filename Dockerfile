FROM lscr.io/linuxserver/baseimage-ubuntu:noble

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:${PATH}

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      python3 \
      python3-venv \
 && python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN /opt/venv/bin/pip install . \
 && rm -rf /app

COPY rootfs /

RUN chmod +x /custom-services.d/plex-ldap-gateway

EXPOSE 1389 8000
