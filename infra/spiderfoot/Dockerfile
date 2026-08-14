# SpiderFoot — self-hosted OSINT engine (pinned build)
# =====================================================
# There is NO official `spiderfoot/spiderfoot` image on Docker Hub (verified
# 2026-08-14: hub.docker.com/v2/repositories/spiderfoot/spiderfoot -> 404).
# The upstream project ships a Dockerfile and expects you to build it. This
# Dockerfile mirrors the official one (github.com/smicallef/spiderfoot/blob/
# master/Dockerfile) but pins the SOURCE to the v4.0.0 git tag so the build
# is reproducible and auditable. The only difference from upstream is the
# pinned checkout instead of a COPY of the working tree.
#
# Tag: v4.0.0 (last stable release, 2022-04-07; master still reports 4.0.0).
# Port: 5001 (web UI + JSON API). Data: /var/lib/spiderfoot (named volume).

FROM alpine:3.12.4 AS build
ARG SF_VERSION=v4.0.0
RUN apk add --no-cache gcc git curl python3 python3-dev py3-pip swig tinyxml-dev \
    musl-dev openssl-dev libffi-dev libxslt-dev libxml2-dev jpeg-dev \
    openjpeg-dev zlib-dev cargo rust
RUN git clone --depth 1 --branch "${SF_VERSION}" \
    https://github.com/smicallef/spiderfoot.git /src
WORKDIR /src
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin":$PATH
RUN pip3 install -U pip \
    && pip3 install -r requirements.txt

FROM alpine:3.13.0
WORKDIR /home/spiderfoot

# Place database and logs outside installation directory
ENV SPIDERFOOT_DATA /var/lib/spiderfoot
ENV SPIDERFOOT_LOGS /var/lib/spiderfoot/log
ENV SPIDERFOOT_CACHE /var/lib/spiderfoot/cache

# Run everything as one command so that only one layer is created
RUN apk --update --no-cache add python3 musl openssl libxslt tinyxml libxml2 jpeg zlib openjpeg \
    && addgroup spiderfoot \
    && adduser -G spiderfoot -h /home/spiderfoot -s /sbin/nologin \
               -g "SpiderFoot User" -D spiderfoot \
    && rm -rf /var/cache/apk/* \
    && mkdir -p $SPIDERFOOT_DATA || true \
    && mkdir -p $SPIDERFOOT_LOGS || true \
    && mkdir -p $SPIDERFOOT_CACHE || true \
    && chown spiderfoot:spiderfoot $SPIDERFOOT_DATA \
    && chown spiderfoot:spiderfoot $SPIDERFOOT_LOGS \
    && chown spiderfoot:spiderfoot $SPIDERFOOT_CACHE

COPY --from=build /src /home/spiderfoot
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

USER spiderfoot

EXPOSE 5001

# Run the application.
ENTRYPOINT ["/opt/venv/bin/python"]
CMD ["sf.py", "-l", "0.0.0.0:5001"]
