ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.20
# hadolint ignore=DL3006
FROM ${BUILD_FROM}

ARG BUILD_ARCH=amd64
ARG BUILD_VERSION=dev
LABEL io.hass.name="DTEK Telegram Bot" \
      io.hass.description="DTEK schedules and power alerts for Telegram" \
      io.hass.type="addon" \
      io.hass.arch="${BUILD_ARCH}" \
      io.hass.version="${BUILD_VERSION}" \
      org.opencontainers.image.title="DTEK Telegram Bot" \
      org.opencontainers.image.source="https://github.com/groove-max/ha-dtek-telegram-bot" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0"

WORKDIR /app

# hadolint ignore=DL3018
RUN apk add --no-cache python3 py3-pip py3-virtualenv font-dejavu

COPY requirements.txt requirements.lock LICENSE NOTICE /app/
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt -c /app/requirements.lock

COPY rootfs/ /
COPY run.sh /app/run.sh
RUN chmod +x /app/run.sh

CMD ["/app/run.sh"]
