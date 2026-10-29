#!/usr/bin/env bash
# Relance la plateforme mise en pause par infra/local/down.sh (un réplica partout, comme
# l'overlay cpu-local). Les poids des modèles sont déjà sur les volumes : pas de téléchargement.
set -euo pipefail
# /etc/rancher/k3s/k3s.yaml n'est lisible que par root : copie utilisateur sinon.
[[ -r "${KUBECONFIG:-}" ]] || export KUBECONFIG=$HOME/.kube/k3s.yaml

scale1() { kubectl -n "$1" scale "${@:2}" --replicas=1; }

docker start local-registry >/dev/null

# Socle d'abord : Sealed Secrets et cert-manager servent les workloads.
scale1 sealed-secrets deploy --all
scale1 cert-manager deploy --all

scale1 monitoring deploy --all
kubectl -n monitoring patch prometheus kube-prometheus-stack-prometheus --type merge -p '{"spec":{"replicas":1}}'
kubectl -n monitoring patch alertmanager kube-prometheus-stack-alertmanager --type merge -p '{"spec":{"replicas":1}}'
kubectl -n monitoring patch ds kube-prometheus-stack-prometheus-node-exporter --type json \
  -p '[{"op":"remove","path":"/spec/template/spec/nodeSelector/sovereign-llm-platform~1paused"}]' 2>/dev/null || true

scale1 llm sts --all
scale1 llm deploy --all
scale1 kagent deploy --all # le contrôleur kagent relance lui-même l'agent cluster-doctor

echo "Démarrage en cours (le modèle met ~1 min à charger) : kubectl get pods -n llm -w"
