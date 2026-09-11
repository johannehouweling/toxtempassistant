FROM quay.io/minio/mc:latest

# Bake the init script so the minio_init service is fully self-contained —
# no host-repo bind mount, no gluster copy, no Docker config. The compose
# minio_init service just sets `image:` to this and runs.
COPY myocyte/dockerfiles/minio/init.sh /init.sh
RUN chmod +x /init.sh

ENTRYPOINT ["/init.sh"]
