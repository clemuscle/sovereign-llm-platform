#!/usr/bin/env bash
# Mode air-gapped, étape 2 : copie toutes les images nécessaires dans la registry interne.
#
# Méthode "amorçage connecté" : la plateforme est d'abord installée en mode connecté, puis on
# photographie les images réellement utilisées (tous namespaces) et celles de l'overlay
# air-gapped visé, avant de couper l'accès Internet. Les digests sont conservés à l'identique :
# containerd, configuré en miroir (infra/k3s/registries.yaml.example), les trouve sans
# qu'aucun manifest ne change.
#
#   scripts/mirror-images.sh gpu-airgap [registry]      registry par défaut : 127.0.0.1:30500
#
# Pour un environnement isolé dès le premier jour (aucun amorçage connecté possible), les
# outils dédiés sont Zarf ou Hauler : ils produisent une archive transportable (images, charts,
# modèles) à importer de l'autre côté.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v crane >/dev/null || { echo "crane requis : go install github.com/google/go-containerregistry/cmd/crane@latest" >&2; exit 1; }

OVERLAY=${1:?usage: mirror-images.sh <overlay> [registry]}
REGISTRY=${2:-127.0.0.1:30500}

{
  kubectl get pods -A -o jsonpath='{range .items[*]}{range .spec.initContainers[*]}{.image}{"\n"}{end}{range .spec.containers[*]}{.image}{"\n"}{end}{end}'
  kustomize build "deploy/overlays/$OVERLAY" | grep -E '^\s+image:' | awk '{print $2}'
} | sort -u | while read -r image; do
  [[ -z "$image" ]] && continue
  # Chemin dans le miroir : sans le registre d'origine ; Docker Hub ajoute "library/".
  path=$image
  case "$image" in
    *.*/*|localhost*/*) path=${image#*/} ;;           # ghcr.io/org/img -> org/img
    */*) ;;                                           # org/img (Docker Hub)
    *) path="library/$image" ;;                       # postgres -> library/postgres
  esac
  echo "-> $image"
  crane copy --insecure "$image" "$REGISTRY/$path"
done
echo "Images copiées. Installe infra/k3s/registries.yaml.example puis : sudo systemctl restart k3s"
