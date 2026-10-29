# Image d'outillage de la plateforme : model-fetcher (initContainer) et bootstrap des clés LiteLLM.

# oras compilé depuis les sources : le binaire publié (1.3.6) embarque go1.26.9, vulnérable
# (CVE-2026-111943, MITM par certificats x509 intermédiaires). Sources vérifiées par
# sum.golang.org ; compilation croisée vers l'architecture cible.
FROM --platform=$BUILDPLATFORM golang:1.27.3-trixie AS oras
ARG TARGETARCH=amd64
ARG ORAS_VERSION=1.3.6
WORKDIR /src
RUN go mod init build && go get oras.land/oras/cmd/oras@v${ORAS_VERSION} \
    && CGO_ENABLED=0 GOOS=linux GOARCH=$TARGETARCH \
       go build -trimpath -ldflags="-s -w" -o /out/oras oras.land/oras/cmd/oras

FROM python:3.12-slim AS build
COPY requirements.lock .
RUN python -m venv /venv && /venv/bin/pip install --no-cache-dir -r requirements.lock

FROM python:3.12-slim
# Pas de répertoire personnel (--no-create-home) : caches et journaux de huggingface_hub / xet dans /tmp.
ENV PATH="/venv/bin:$PATH" PYTHONUNBUFFERED=1 HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HOME=/tmp/huggingface
RUN useradd --uid 10001 --no-create-home fetcher
COPY --from=oras /out/oras /usr/local/bin/oras
COPY --from=build /venv /venv
COPY fetcher.py bootstrap_keys.py /app/
USER 10001
ENTRYPOINT ["python", "/app/fetcher.py"]
