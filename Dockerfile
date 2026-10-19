# Deno is downloaded in an isolated build stage so curl/unzip never reach the
# runtime image. The release and archive checksums are pinned deliberately.
FROM python:3.14-slim AS deno-fetcher

ARG TARGETARCH
ARG DENO_VERSION=2.9.5
ARG DENO_SHA256_AMD64=8b010a3b1a4a0188a67cdb8a7a27348b2a501af78aec7fc74f2ace167368d530
ARG DENO_SHA256_ARM64=6b7cae3a8fc4385a59dea3146fcb8bad7fea4230e0ad36a8c692afacbc254be0

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        unzip \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "$arch" in \
        amd64) deno_arch="x86_64"; deno_sha256="$DENO_SHA256_AMD64" ;; \
        arm64) deno_arch="aarch64"; deno_sha256="$DENO_SHA256_ARM64" ;; \
        *) echo "Architecture Deno non supportée: $arch" >&2; exit 1 ;; \
    esac; \
    archive="deno-${deno_arch}-unknown-linux-gnu.zip"; \
    curl -fsSLo "/tmp/$archive" \
        "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/${archive}"; \
    echo "${deno_sha256}  /tmp/$archive" | sha256sum -c -; \
    unzip -q "/tmp/$archive" -d /usr/local/bin; \
    rm -f "/tmp/$archive"; \
    deno --version


FROM python:3.14-slim

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/refuge \
    DENO_DIR=/home/refuge/.cache/deno

# Runtime dependencies only. gosu is used by the entrypoint solely when
# Railway starts the container as root to initialise its root-owned volume;
# the bot itself is then exec'd as the unprivileged refuge user.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        gosu \
        libjpeg-dev \
        libopus0 \
        passwd \
        zlib1g-dev \
    && groupadd --gid "$APP_GID" refuge \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --create-home \
        --home-dir /home/refuge --shell /usr/sbin/nologin --no-log-init refuge \
    && mkdir -p /app/data "$DENO_DIR" \
    && chown -R refuge:refuge /app /home/refuge \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deno-fetcher /usr/local/bin/deno /usr/local/bin/deno
RUN deno --version

WORKDIR /app

# requirements.txt is generated from requirements.in and contains exact pins
# plus hashes. The PO Token plugin has its own tiny hash-locked manifest because
# its JavaScript provider runs as a separate Railway service instead of being
# embedded into the bot container.
COPY requirements.txt requirements-youtube-pot.txt ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.txt && \
    python -m pip install --no-cache-dir --require-hashes -r requirements-youtube-pot.txt

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

# .dockerignore prevents local secrets/runtime state from entering this COPY.
COPY --chown=refuge:refuge . .

USER refuge:refuge

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "main.py"]
