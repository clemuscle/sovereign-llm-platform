#!/usr/bin/env bash
# Test de bout en bout sur un cluster déployé avec l'overlay ci (ou cpu).
# Vérifie le comportement réel, pas seulement que les pods tournent :
#   1. tous les workloads sont prêts (modèles téléchargés à révision figée, clés créées)
#   2. authentification de l'API (401 sans clé, 401 avec la clé de lecture sur /ingest)
#   3. RAG complet : ingestion, puis réponse JSON conforme au schéma (décodage contraint)
#   4. ré-ingestion d'un document raccourci : plus aucun chunk orphelin
#   5. isolation réseau : un pod du namespace llm ne sort pas sur Internet
#   6. idempotence : le job de clés rejoué ne recrée rien
set -euo pipefail
NS=llm
KEYS_FILE=${KEYS_FILE:-/tmp/e2e-keys.env}
# shellcheck disable=SC1090
source "$KEYS_FILE"
PORT=18080
pass() { echo "  ✔ $*"; }
fail() { echo "  ✘ $*" >&2; exit 1; }
expect() { # expect <message si OK> <message si KO> <commande...>
  local ok=$1 ko=$2; shift 2
  if "$@"; then pass "$ok"; else fail "$ko"; fi
}

echo "1. Démarrage des workloads"
kubectl -n $NS rollout status statefulset/embeddings --timeout=15m
kubectl -n $NS rollout status statefulset/llm --timeout=15m
kubectl -n $NS rollout status statefulset/litellm-db statefulset/qdrant --timeout=5m
kubectl -n $NS rollout status deployment/litellm-redis deployment/litellm deployment/rag-app --timeout=10m
kubectl -n $NS wait --for=condition=complete job/litellm-keys-bootstrap --timeout=5m
pass "plateforme prête"

kubectl -n $NS port-forward svc/rag-app $PORT:8080 >/tmp/port-forward.log 2>&1 &
PF_PID=$!
trap 'kill $PF_PID 2>/dev/null || true' EXIT
for _ in $(seq 30); do curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null && break; sleep 1; done
API="http://127.0.0.1:$PORT"
post() { curl -s -o /tmp/body.json -w '%{http_code}' -X POST "$API$1" -H 'Content-Type: application/json' "${@:3}" -d "$2"; }

echo "2. Authentification"
expect "/ask sans clé : 401" "/ask sans clé accepté" \
  test "$(post /ask '{"question":"test sans clé"}')" = 401
expect "/ingest avec la clé de lecture : 401" "la clé de lecture peut écrire" \
  test "$(post /ingest '{"documents":[{"id":"x","text":"y"}]}' -H "Authorization: Bearer $QUERY_API_KEY")" = 401

echo "3. RAG de bout en bout"
LONG=$(python3 -c "print('\\n\\n'.join(f'Section {i}. Le GPU est exposé aux pods par le device plugin NVIDIA, qui annonce la ressource nvidia.com/gpu au scheduler Kubernetes.' for i in range(12)))")
DOC=$(jq -n --arg t "$LONG" '{documents: [{id: "guide-gpu", text: $t, source: "docs/gpu-on-k3s.md"}]}')
[[ $(post /ingest "$DOC" -H "Authorization: Bearer $INGEST_API_KEY") == 200 ]] || fail "ingestion : $(cat /tmp/body.json)"
CHUNKS=$(jq .chunks /tmp/body.json)
pass "document ingéré ($CHUNKS chunks)"
[[ $(post /ask '{"question":"Comment le GPU est-il exposé aux pods ?"}' -H "Authorization: Bearer $QUERY_API_KEY") == 200 ]] \
  || fail "/ask : $(cat /tmp/body.json)"
expect "réponse conforme au schéma : $(jq -c '{confidence, sources}' /tmp/body.json)" \
  "réponse hors schéma : $(cat /tmp/body.json)" \
  jq -e '(.answer | type == "string" and length > 0) and (.confidence | IN("high","medium","low")) and (.sources | type == "array")' /tmp/body.json

echo "4. Ré-ingestion d'un document raccourci"
SHORT=$(jq -n '{documents: [{id: "guide-gpu", text: "Version courte du guide.", source: "docs/gpu-on-k3s.md"}]}')
post /ingest "$SHORT" -H "Authorization: Bearer $INGEST_API_KEY" >/dev/null
COUNT=$(curl -sf "$API/documents/guide-gpu" -H "Authorization: Bearer $INGEST_API_KEY" | jq .chunks)
expect "$CHUNKS chunks -> 1, aucun orphelin" "chunks restants : $COUNT" test "$COUNT" = 1

echo "5. Isolation réseau (egress default-deny)"
if kubectl -n $NS run egress-probe --rm -i --restart=Never --quiet --image=curlimages/curl:8.17.0 \
  --labels=app.kubernetes.io/name=egress-probe --command -- \
  curl -s -m 8 -o /dev/null https://example.com 2>/dev/null; then
  fail "un pod du namespace llm atteint Internet"
fi
pass "sortie Internet bloquée"

echo "6. Idempotence du bootstrap des clés"
kubectl -n $NS create job litellm-keys-rerun --from=job/litellm-keys-bootstrap >/dev/null
kubectl -n $NS wait --for=condition=complete job/litellm-keys-rerun --timeout=3m >/dev/null
LOGS=$(kubectl -n $NS logs job/litellm-keys-rerun)
grep -q "created" <<<"$LOGS" && fail "le second passage a recréé des clés : $LOGS"
pass "second passage : $(grep -c updated <<<"$LOGS") clés mises à jour, aucune recréée"

echo "Tous les tests de bout en bout sont passés."
