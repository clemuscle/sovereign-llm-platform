# Image d'outillage de la plateforme : model-fetcher (initContainer) et bootstrap des clés LiteLLM.
FROM python:3.12-slim AS build
ARG TARGETARCH=amd64
ARG ORAS_VERSION=1.3.6
# Sommes de contrôle officielles (oras_<version>_checksums.txt) : le binaire est vérifié avant usage.
ARG ORAS_SHA256_amd64=66fb0ad7206f3f6fa77ed5b552049d364ccf4b50b1684554f731f76302b52ca0
ARG ORAS_SHA256_arm64=b84269e78dfc2d5329a4aab373da3200de95e6c138338cf6c66c10c0a3f248c1
ADD https://github.com/oras-project/oras/releases/download/v${ORAS_VERSION}/oras_${ORAS_VERSION}_linux_${TARGETARCH}.tar.gz /tmp/oras.tgz
RUN set -eu; \
    eval "expected=\$ORAS_SHA256_${TARGETARCH}"; \
    echo "${expected}  /tmp/oras.tgz" | sha256sum -c -; \
    tar -xzf /tmp/oras.tgz -C /usr/local/bin oras
COPY requirements.lock .
RUN python -m venv /venv && /venv/bin/pip install --no-cache-dir -r requirements.lock

FROM python:3.12-slim
ENV PATH="/venv/bin:$PATH" PYTHONUNBUFFERED=1 HF_HUB_DISABLE_TELEMETRY=1
RUN useradd --uid 10001 --no-create-home fetcher
COPY --from=build /usr/local/bin/oras /usr/local/bin/oras
COPY --from=build /venv /venv
COPY fetcher.py bootstrap_keys.py /app/
USER 10001
ENTRYPOINT ["python", "/app/fetcher.py"]
