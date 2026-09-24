# MinIO server and client, built from source.
#
# MinIO no longer publishes public images (Docker Hub, then quay.io, went
# private) and has archived github.com/minio/minio and github.com/minio/mc, so
# we build both from their final release tags. The builds are frozen: no
# upstream fixes will come. Building with a current Go toolchain at least keeps
# the Go standard library patched.
#
# Targets:
#   server -> ghcr.io/johannehouweling/toxtempassistant-minio       (compose: minio)
#   init   -> ghcr.io/johannehouweling/toxtempassistant-minio-init  (compose: minio_init)
# The backup-scheduler Dockerfile builds mc the same way; keep MC_RELEASE in sync.

ARG GO_IMAGE=golang:1.27-alpine
ARG MINIO_RELEASE=RELEASE.2025-10-15T17-29-55Z
ARG MC_RELEASE=RELEASE.2025-08-13T08-35-41Z

FROM ${GO_IMAGE} AS build-base
RUN apk add --no-cache git
ENV CGO_ENABLED=0 GOTOOLCHAIN=local

FROM build-base AS build-minio
ARG MINIO_RELEASE
RUN git clone --depth 1 --branch "${MINIO_RELEASE}" \
      https://github.com/minio/minio.git /src
WORKDIR /src
# Same flags as upstream's `make build`; gen-ldflags stamps the release version.
RUN MINIO_RELEASE=RELEASE go build -tags kqueue -trimpath \
      -ldflags "$(MINIO_RELEASE=RELEASE go run buildscripts/gen-ldflags.go "${MINIO_RELEASE#RELEASE.}")" \
      -o /out/minio

FROM build-base AS build-mc
ARG MC_RELEASE
RUN git clone --depth 1 --branch "${MC_RELEASE}" \
      https://github.com/minio/mc.git /src
WORKDIR /src
RUN go build -tags kqueue -trimpath \
      -ldflags "$(MC_RELEASE=RELEASE go run buildscripts/gen-ldflags.go "${MC_RELEASE#RELEASE.}")" \
      -o /out/mc

FROM alpine:3.23 AS server
RUN apk add --no-cache ca-certificates
COPY --from=build-minio /out/minio /usr/bin/minio
EXPOSE 9000 9001
VOLUME ["/data"]
ENTRYPOINT ["/usr/bin/minio"]

# Bake the init script so the minio_init service is fully self-contained —
# no host-repo bind mount, no gluster copy, no Docker config. The compose
# minio_init service just sets `image:` to this and runs.
FROM alpine:3.23 AS init
RUN apk add --no-cache ca-certificates
COPY --from=build-mc /out/mc /usr/bin/mc
COPY myocyte/dockerfiles/minio/init.sh /init.sh
RUN chmod +x /init.sh

ENTRYPOINT ["/init.sh"]
