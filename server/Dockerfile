# syntax=docker/dockerfile:1
# Headless RPFM backend. The existing root Dockerfile remains the legacy CLI.
ARG RUST_IMAGE=rust:bookworm
FROM ${RUST_IMAGE} AS builder
ARG RPFM_COMMIT=c74eac65275f3f92fe0a74c2e6b4bc68f2387aaa
ARG SCHEMA_COMMIT=29024e92f634c1da38c845c064bdcb0fd6492f67
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git pkg-config cmake clang libclang-dev \
    libssl-dev libdbus-1-dev libsecret-1-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src/rpfm
RUN git init . \
    && git remote add origin https://github.com/Frodo45127/rpfm.git \
    && git fetch --depth 1 origin "${RPFM_COMMIT}" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${RPFM_COMMIT}" \
    && git submodule update --init --depth 1 3rdparty/src/ritual
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/src/rpfm/target \
    cargo build --locked --release -p rpfm_server \
    && install -D target/release/rpfm_server /out/rpfm_server \
    && strip /out/rpfm_server
# Seed a real, independent schema git checkout, so updates work after copying.
# A copied submodule .git file would point back into the discarded build stage.
RUN git init /out/schemas \
    && git -C /out/schemas remote add origin https://github.com/Frodo45127/rpfm-schemas.git \
    && git -C /out/schemas fetch --depth 1 origin "${SCHEMA_COMMIT}" \
    && git -C /out/schemas checkout -b master FETCH_HEAD \
    && test "$(git -C /out/schemas rev-parse HEAD)" = "${SCHEMA_COMMIT}"

FROM debian:bookworm-slim AS runtime
ARG RPFM_COMMIT=c74eac65275f3f92fe0a74c2e6b4bc68f2387aaa
LABEL org.opencontainers.image.title="RPFM Server" \
    org.opencontainers.image.version="5.3.3" \
    org.opencontainers.image.source="https://github.com/Warhammer-Mods/rpfm-server-docker" \
    org.opencontainers.image.revision="${RPFM_COMMIT}" \
    org.opencontainers.image.licenses="MIT"
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git libssl3 libssh2-1 libsecret-1-0 libdbus-1-3 \
    libzstd1 liblzma5 zlib1g xz-utils p7zip-full socat tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 rpfm \
    && useradd --uid 1000 --gid 1000 --create-home rpfm \
    && mkdir -p /config/rpfm /work \
    && chown -R rpfm:rpfm /config /work
COPY --from=builder /out/rpfm_server /usr/local/bin/rpfm_server
COPY --from=builder /out/schemas /opt/rpfm-seed/schemas
COPY --chmod=755 entrypoint.sh /usr/local/bin/rpfm-entrypoint
# Fail the image build if a dynamically linked dependency is missing.
RUN ldd /usr/local/bin/rpfm_server > /tmp/rpfm-ldd \
    && cat /tmp/rpfm-ldd \
    && ! grep -q 'not found' /tmp/rpfm-ldd \
    && rm /tmp/rpfm-ldd
ENV HOME=/home/rpfm XDG_CONFIG_HOME=/config
USER rpfm
WORKDIR /work
# Upstream binds 127.0.0.1:45127; socat exposes it on this container-only port.
EXPOSE 45128
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:45128/version > /dev/null || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/rpfm-entrypoint"]
