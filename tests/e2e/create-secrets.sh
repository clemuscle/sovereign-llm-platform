#!/usr/bin/env bash
# Secrets éphémères pour les tests de bout en bout (cluster kind jetable, sans Sealed Secrets).
# Même structure que scripts/seal-secrets.sh. Écrit les clés de test dans $KEYS_FILE.
set -euo pipefail
KEYS_FILE=${KEYS_FILE:-/tmp/e2e-keys.env}
key() { echo "sk-$(openssl rand -hex 24)"; }

INGEST=$(key) QUERY=$(key)
kubectl create namespace llm --dry-run=client -o yaml | kubectl apply -f -
create() { kubectl -n llm create secret generic "$@" --dry-run=client -o yaml | kubectl apply -f -; }
create litellm-keys --from-literal=MASTER_KEY="$(key)" --from-literal=RAG_KEY="$(key)" --from-literal=KAGENT_KEY="$(key)"
create litellm-db --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 16)"
create litellm-redis --from-literal=REDIS_PASSWORD="$(openssl rand -hex 16)"
create rag-api-keys --from-literal=INGEST_API_KEY="$INGEST" --from-literal=QUERY_API_KEY="$QUERY"
printf 'INGEST_API_KEY=%s\nQUERY_API_KEY=%s\n' "$INGEST" "$QUERY" >"$KEYS_FILE"
echo "secrets de test créés"
