"""Instrumentation OpenTelemetry.

- Traces HTTP entrantes (FastAPI) et appels LLM (instrumentation OpenAI, qui émet les
  attributs standard `gen_ai.*` : modèle, tokens d'entrée/sortie, durée).
- Export OTLP vers l'OpenTelemetry Collector : l'application ignore tout des backends.
- Les logs portent `trace_id=...` pour passer d'un log à sa trace dans Grafana.

Désactivable avec OTEL_SDK_DISABLED=true (tests, développement hors cluster).
"""

from __future__ import annotations

import logging
import os

from opentelemetry import trace

logger = logging.getLogger("rag")


class _TraceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        ctx = trace.get_current_span().get_span_context()
        record.trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else "-"
        return True


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_TraceIdFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s trace_id=%(trace_id)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def setup_tracing(app) -> None:
    if os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true":
        logger.info("OpenTelemetry désactivé")
        return

    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    # service.name vient de OTEL_SERVICE_NAME, l'endpoint de OTEL_EXPORTER_OTLP_ENDPOINT.
    provider = TracerProvider(resource=Resource.create())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(insecure=True)))
    trace.set_tracer_provider(provider)

    OpenAIInstrumentor().instrument()
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz")
