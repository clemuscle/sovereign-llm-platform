#!/usr/bin/env bash
# Met la plateforme en pause sur le poste local (k3s sous WSL2) : tout passe à 0 réplica.
# Rien n'est supprimé : volumes (poids des modèles, Qdrant, PostgreSQL), secrets et clé de
# Sealed Secrets restent en place. Reprise : infra/local/up.sh
set -euo pipefail
# /etc/rancher/k3s/k3s.yaml n'est lisible que par root : copie utilisateur sinon.
[[ -r "${KUBECONFIG:-}" ]] || export KUBECONFIG=$HOME/.kube/k3s.yaml

scale0() { kubectl -n "$1" scale "${@:2}" --replicas=0; }

# Port-forwards ouverts vers la plateforme (Grafana, RAG, kagent...).
pkill -f '^kubectl .*port-forward' || true

# Contrôleurs d'abord : sinon ils recréent ce qu'on arrête.
scale0 kagent deploy/kagent-controller
kubectl -n monitoring patch prometheus kube-prometheus-stack-prometheus --type merge -p '{"spec":{"replicas":0}}'
kubectl -n monitoring patch alertmanager kube-prometheus-stack-alertmanager --type merge -p '{"spec":{"replicas":0}}'
sleep 5 # laisse l'opérateur appliquer les replicas à 0
scale0 monitoring deploy/kube-prometheus-stack-operator

scale0 llm deploy --all
scale0 llm sts --all
scale0 kagent deploy --all
scale0 monitoring deploy --all
# Un DaemonSet ne se met pas à 0 : on lui donne un nodeSelector qu'aucun nœud ne porte.
kubectl -n monitoring patch ds kube-prometheus-stack-prometheus-node-exporter --type merge \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"sovereign-llm-platform/paused":"true"}}}}}'
scale0 cert-manager deploy --all
scale0 sealed-secrets deploy --all

# Registry local des images rag-app / platform-tools.
docker update --restart=unless-stopped local-registry >/dev/null && docker stop local-registry >/dev/null || true

echo "Plateforme en pause. Reprise : infra/local/up.sh"
