# Mode air-gapped

Objectif : la plateforme fonctionne sans qu'aucun workload n'ait accès à Internet. Les poids des
modèles et les images viennent d'une registry OCI interne (Zot, projet CNCF Sandbox).

```
   machine de transit (Internet + réseau interne)            cluster isolé
 ┌──────────────────────────────────────────────┐   ┌──────────────────────────────────┐
 │ make stage-models   HF ──oras push──┐         │   │  Zot (model-store)               │
 │ make mirror-images  registres ──crane copy──┼──►│   ├─ models/<org>/<nom>:<SHA>    │
 └──────────────────────────────────────────────┘   │   └─ miroir des images (digests) │
                                                    │        ▲               ▲          │
                                                    │  initContainer     containerd     │
                                                    │  model-fetcher     (registries)   │
                                                    │  SOURCE=oci                       │
                                                    └──────────────────────────────────┘
```

## Étapes

```bash
# 1. Amorçage : installer en mode connecté avec la registry interne
make infra-apply PROFILE=gpu AIRGAP=true      # installe aussi Zot (NodePort 30500)
#    ArgoCD synchronise alors l'overlay gpu-airgap.

# 2. Pousser les poids des modèles (révisions figées par `make pin`)
HF_TOKEN=hf_xxx make stage-models PROFILE=gpu AIRGAP=true
#    Optionnel : reporter les digests affichés dans MODEL_DIGEST (contenu figé, vérifié au pull).

# 3. Copier toutes les images utilisées
make mirror-images PROFILE=gpu AIRGAP=true

# 4. Rediriger containerd vers la registry interne, puis couper l'accès Internet
sudo cp infra/k3s/registries.yaml.example /etc/rancher/k3s/registries.yaml
sudo systemctl restart k3s
```

## Ce qui garantit l'absence de fuite

| Couche | Mécanisme |
|---|---|
| Réseau | NetworkPolicy `default-deny-all` (entrée et sortie) ; seule sortie des pods de modèle : la registry interne |
| Poids | Téléchargés par l'initContainer depuis Zot ; serveurs démarrés avec `HF_HUB_OFFLINE=1` sur un dossier local |
| Images | Miroir containerd : les digests des manifests sont servis par Zot |
| Télémétrie d'éditeurs | Coupée : vLLM (`VLLM_NO_USAGE_STATS`), Qdrant, Hugging Face, grille tarifaire LiteLLM locale |
| Vérification | Test e2e : un pod du namespace tente de joindre Internet et doit échouer |

## Limites

- L'amorçage (charts Helm, première copie des images) nécessite un accès connecté. Pour un site
  isolé dès le premier jour, produire une archive transportable avec **Zarf** ou **Hauler**.
- Le trafic vers Zot est en HTTP à l'intérieur du cluster ; pour du TLS, émettre un certificat
  cert-manager pour Zot et passer `OCI_PLAIN_HTTP` à `false`.
- ArgoCD doit lire un dépôt Git joignable : miroir Git interne (`git_repo_url`).
