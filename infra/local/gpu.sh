#!/usr/bin/env bash
# Couche 3 (docs/gpu-on-k3s.md) sur le poste local : annonce le GPU au scheduler.
# Prérequis : sudo infra/local/gpu-host.sh. Rejouable (helm upgrade --install, label --overwrite).
set -euo pipefail
# /etc/rancher/k3s/k3s.yaml n'est lisible que par root : copie utilisateur sinon.
[[ -r "${KUBECONFIG:-}" ]] || export KUBECONFIG=$HOME/.kube/k3s.yaml
cd "$(dirname "$0")"

CHART_VERSION=0.20.1

kubectl label nodes --all nvidia.com/gpu.present=true --overwrite

helm upgrade --install nvidia-device-plugin nvidia-device-plugin \
  --repo https://nvidia.github.io/k8s-device-plugin --version "$CHART_VERSION" \
  --namespace nvidia-device-plugin --create-namespace \
  -f nvidia-device-plugin.local.yaml --wait

for _ in $(seq 30); do
  gpus=$(kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}')
  [[ -n $gpus && $gpus != 0 ]] && break
  sleep 2
done
[[ -n $gpus && $gpus != 0 ]] || { echo "nvidia.com/gpu non annoncé : kubectl logs -n nvidia-device-plugin ds/nvidia-device-plugin" >&2; exit 1; }
echo "nvidia.com/gpu: $gpus. Déploiement : kubectl apply -k deploy/overlays/gpu-local --server-side"
