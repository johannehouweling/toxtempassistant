# mc is built from source rather than taken from alpine's `minio-mc` package,
# which installs the binary as `mcli` (to avoid a name collision with
# midnight-commander), while our script invokes `mc`. MinIO no longer publishes
# public images or binaries; see myocyte/dockerfiles/minio/Dockerfile, whose
# MC_RELEASE this must match.
ARG MC_RELEASE=RELEASE.2025-08-13T08-35-41Z

FROM golang:1.27-alpine AS build-mc
ARG MC_RELEASE
RUN apk add --no-cache git
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
RUN git clone --depth 1 --branch "${MC_RELEASE}" \
      https://github.com/minio/mc.git /src
WORKDIR /src
RUN go build -tags kqueue -trimpath \
      -ldflags "$(MC_RELEASE=RELEASE go run buildscripts/gen-ldflags.go "${MC_RELEASE#RELEASE.}")" \
      -o /out/mc

FROM alpine:3.23

# pg_dump matches the postgres:17-alpine server major version.
RUN apk add --no-cache \
    bash \
    coreutils \
    findutils \
    grep \
    sed \
    gzip \
    tar \
    tzdata \
    postgresql17-client \
    ca-certificates \
    curl \
    moreutils

COPY --from=build-mc /out/mc /usr/local/bin/mc

# Latest releases available at https://github.com/aptible/supercronic/releases
ENV SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.43/supercronic-linux-amd64 \
    SUPERCRONIC_SHA1SUM=f97b92132b61a8f827c3faf67106dc0e4467ccf2 \
    SUPERCRONIC=supercronic-linux-amd64

# --retry: a 504 from GitHub's release CDN once failed the whole image build,
# which left a tagged release with no image behind it and silently skipped a
# deploy. --retry-all-errors covers HTTP 5xx, which plain --retry does not.
# The SHA1 check below is unaffected: a corrupt or partial download still fails.
RUN curl -fsSLO --retry 5 --retry-delay 2 --retry-all-errors "$SUPERCRONIC_URL" \
 && echo "${SUPERCRONIC_SHA1SUM}  ${SUPERCRONIC}" | sha1sum -c - \
 && chmod +x "$SUPERCRONIC" \
 && mv "$SUPERCRONIC" "/usr/local/bin/${SUPERCRONIC}" \
 && ln -s "/usr/local/bin/${SUPERCRONIC}" /usr/local/bin/supercronic

WORKDIR /work

# Bake the backup script into the image so the container is self-contained:
# no `./:/work:ro` mount of the host repo is needed at runtime. Required for
# Docker Swarm, where each node has no notion of "the repo on this server".
COPY backup.sh /usr/local/bin/backup.sh
COPY myocyte/dockerfiles/backup-scheduler/entrypoint.sh /entrypoint.sh
RUN chmod +x /usr/local/bin/backup.sh /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]