#!/usr/bin/env bash
# Mode air-gapped, étape 1 : pousse les poids des modèles dans la registry OCI interne.
#
# À lancer depuis une machine qui voit à la fois Internet et la registry interne (poste de
# transit, ou le nœud avant de couper l'accès Internet). Utilise l'image platform-tools
# (Python + huggingface_hub + ORAS) : rien à installer localement à part Docker.
#
#   scripts/stage-models.sh gpu-airgap [registry]      registry par défaut : 127.0.0.1:30500
#
# Pour chaque modèle de l'overlay (ConfigMaps llm-model et embed-model), à sa révision figée :
# téléchargement depuis Hugging Face puis `oras push` vers <registry>/models/<org>/<nom>:<révision>.
# Le digest affiché peut être reporté dans MODEL_DIGEST pour figer aussi le contenu.
set -euo pipefail
cd "$(dirname "$0")/.."

OVERLAY=${1:?usage: stage-models.sh <overlay> [registry]}
REGISTRY=${2:-127.0.0.1:30500}
IMAGE=${PLATFORM_TOOLS_IMAGE:-$(python3 -c "
import yaml
doc = yaml.safe_load(open('deploy/components/image-pins/kustomization.yaml'))
e = next(i for i in doc['images'] if i['name'] == 'platform-tools')
print(e['newName'] + ('@' + e['digest'] if e.get('digest') else ':' + e['newTag']))
")}

kustomize build "deploy/overlays/$OVERLAY" | python3 -c "
import sys, yaml
for doc in yaml.safe_load_all(sys.stdin):
    if doc and doc['kind'] == 'ConfigMap' and doc['metadata']['name'] in ('llm-model', 'embed-model'):
        d = doc['data']
        print(d['MODEL_REPO'], d['MODEL_REVISION'], d.get('ALLOW_PATTERNS', ''))
" | while read -r repo revision patterns; do
  echo "==> $repo@$revision"
  docker run --rm --network host \
    -e SOURCE=hf -e MODEL_REPO="$repo" -e MODEL_REVISION="$revision" -e ALLOW_PATTERNS="$patterns" \
    -e OCI_REGISTRY="$REGISTRY/models" -e OCI_PLAIN_HTTP=true -e HF_TOKEN="${HF_TOKEN:-}" \
    -e MODEL_DIR=/tmp/unused \
    "$IMAGE" stage
done
