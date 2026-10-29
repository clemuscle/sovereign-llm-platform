#!/usr/bin/env bash
# Génère les secrets de la plateforme et les chiffre en SealedSecret, prêts à être commités.
#
#  - Les valeurs en clair restent dans .secrets/values.env (ignoré par Git, à sauvegarder
#    dans un gestionnaire de mots de passe). Elles ne sont générées qu'une fois.
#  - Les fichiers chiffrés vont dans deploy/secrets/ : seul le contrôleur Sealed Secrets du
#    cluster cible peut les déchiffrer. ArgoCD les applique avant les workloads.
#  - Idempotent : un secret déjà scellé n'est pas re-chiffré (le chiffrement étant aléatoire,
#    le refaire produirait un diff Git inutile). FORCE=1 pour tout re-sceller.
set -euo pipefail

cd "$(dirname "$0")/.."
SECRETS_DIR=.secrets
VALUES="$SECRETS_DIR/values.env"
CERT="$SECRETS_DIR/sealed-secrets-cert.pem"
OUT=deploy/secrets
CONTROLLER_NAME=${CONTROLLER_NAME:-sealed-secrets-controller}
CONTROLLER_NS=${CONTROLLER_NS:-sealed-secrets}

command -v kubeseal >/dev/null || { echo "kubeseal requis : https://github.com/bitnami-labs/sealed-secrets#kubeseal" >&2; exit 1; }

mkdir -p "$SECRETS_DIR" && chmod 700 "$SECRETS_DIR"
key() { echo "sk-$(openssl rand -hex 24)"; }
pw() { openssl rand -hex 24; }

if [[ ! -s "$VALUES" ]]; then
  echo "Génération des valeurs dans $VALUES"
  umask 077
  cat >"$VALUES" <<EOF
# Valeurs en clair des secrets de la plateforme. NE PAS COMMITER. À sauvegarder.
LITELLM_MASTER_KEY=$(key)
RAG_LITELLM_KEY=$(key)
KAGENT_LITELLM_KEY=$(key)
POSTGRES_PASSWORD=$(pw)
REDIS_PASSWORD=$(pw)
RAG_INGEST_API_KEY=$(key)
RAG_QUERY_API_KEY=$(key)
HF_TOKEN=${HF_TOKEN:-}
EOF
fi
# shellcheck disable=SC1090
source "$VALUES"

# Certificat public du contrôleur : permet de chiffrer, jamais de déchiffrer.
kubeseal --fetch-cert --controller-name "$CONTROLLER_NAME" --controller-namespace "$CONTROLLER_NS" >"$CERT"

seal() { # seal <namespace> <nom> --from-literal=K=V...
  local ns=$1 name=$2; shift 2
  local file="$OUT/$ns-$name.yaml"
  if [[ -f "$file" && "${FORCE:-0}" != "1" ]]; then
    echo "  = $file (déjà scellé)"
    return
  fi
  kubectl create secret generic "$name" --namespace "$ns" "$@" --dry-run=client -o yaml |
    kubeseal --cert "$CERT" --format yaml >"$file"
  echo "  + $file"
}

echo "Scellement des secrets :"
seal llm litellm-keys \
  --from-literal=MASTER_KEY="$LITELLM_MASTER_KEY" \
  --from-literal=RAG_KEY="$RAG_LITELLM_KEY" \
  --from-literal=KAGENT_KEY="$KAGENT_LITELLM_KEY"
seal llm litellm-db --from-literal=POSTGRES_PASSWORD="$POSTGRES_PASSWORD"
seal llm litellm-redis --from-literal=REDIS_PASSWORD="$REDIS_PASSWORD"
seal llm rag-api-keys \
  --from-literal=INGEST_API_KEY="$RAG_INGEST_API_KEY" \
  --from-literal=QUERY_API_KEY="$RAG_QUERY_API_KEY"
seal kagent kagent-litellm --from-literal=LITELLM_API_KEY="$KAGENT_LITELLM_KEY"
if [[ -n "${HF_TOKEN:-}" ]]; then
  seal llm hf-token --from-literal=HF_TOKEN="$HF_TOKEN"
else
  echo "  - hf-token ignoré (HF_TOKEN vide) : requis pour Mistral 7B en profil gpu connecté"
fi

# Régénère la liste des ressources de deploy/secrets.
{
  sed '/^resources:/,$d' "$OUT/kustomization.yaml"
  echo "resources:"
  for f in "$OUT"/*.yaml; do
    [[ "$(basename "$f")" == kustomization.yaml ]] || echo "  - $(basename "$f")"
  done
} >"$OUT/kustomization.yaml.tmp" && mv "$OUT/kustomization.yaml.tmp" "$OUT/kustomization.yaml"

echo
echo "Fait. Commite deploy/secrets/ ; ArgoCD les déploiera."
echo "Important : sauvegarde la clé privée du contrôleur (make backup-sealing-key),"
echo "sans elle, ces secrets sont illisibles sur un nouveau cluster."
