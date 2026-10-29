# Le GPU sur k3s, couche par couche

Un conteneur ne "voit" pas un GPU par magie. Quatre couches doivent être en place,
de la machine jusqu'au pod :

```
┌──────────────────────────────────────────────────────────────┐
│ 4. Pod vLLM        resources.limits: nvidia.com/gpu: 1        │
│                    runtimeClassName: nvidia                   │
├──────────────────────────────────────────────────────────────┤
│ 3. Kubernetes      device plugin -> ressource "nvidia.com/gpu"│
│                    (installé par le NVIDIA GPU Operator)      │
├──────────────────────────────────────────────────────────────┤
│ 2. Runtime         containerd (k3s) + nvidia-container-runtime│
│                    injecte /dev/nvidia* et les libs CUDA      │
├──────────────────────────────────────────────────────────────┤
│ 1. Hôte            driver NVIDIA (module noyau + nvidia-smi)  │
└──────────────────────────────────────────────────────────────┘
```

| Couche | Rôle | Qui l'installe ici |
|---|---|---|
| 1. Driver | Parle au matériel | Toi, sur l'hôte |
| 2. Container toolkit | Monte le GPU dans les conteneurs | Toi, sur l'hôte ; k3s le détecte |
| 3. Device plugin | Annonce `nvidia.com/gpu` au scheduler | GPU Operator (Terraform) |
| 3bis. DCGM exporter | Métriques GPU pour Prometheus | GPU Operator (Terraform) |
| 4. Pod | Demande un GPU | Manifest vLLM (GitOps) |

## Étape 1 : driver NVIDIA (hôte, Ubuntu)

```bash
sudo ubuntu-drivers install          # ou: sudo apt install nvidia-driver-<version>-server
sudo reboot
nvidia-smi                           # doit afficher ta carte et la version CUDA supportée
```

## Étape 2 : NVIDIA Container Toolkit (hôte)

Suis la procédure officielle "Installing the NVIDIA Container Toolkit" (dépôt apt NVIDIA), puis :

```bash
sudo apt install -y nvidia-container-toolkit
sudo systemctl restart k3s           # k3s détecte nvidia-container-runtime au démarrage
```

Vérifications :

```bash
# k3s a ajouté le runtime "nvidia" dans sa config containerd générée
sudo grep -A2 nvidia /var/lib/rancher/k3s/agent/etc/containerd/config.toml

# la RuntimeClass existe (créée par k3s sur les versions récentes)
kubectl get runtimeclass nvidia
```

Si la RuntimeClass n'existe pas : `kubectl apply -f deploy/extras/runtimeclass-nvidia.yaml`.

## Étape 3 : GPU Operator (automatisé par Terraform)

Avec `profile = "gpu"`, Terraform installe l'operator avec `driver.enabled=false` et
`toolkit.enabled=false` (voir `infra/terraform/values/gpu-operator.yaml`) : il ne gère
que le device plugin, la découverte des GPU et les métriques.

```bash
kubectl get pods -n gpu-operator                    # tout doit être Running/Completed
kubectl describe node | grep -A3 "nvidia.com/gpu"   # Capacity: nvidia.com/gpu: 1
```

Test de bout en bout :

```bash
kubectl run gpu-test --rm -it --restart=Never \
  --image=nvidia/cuda:12.4.1-base-ubuntu22.04 \
  --overrides='{"spec":{"runtimeClassName":"nvidia","containers":[{"name":"gpu-test","image":"nvidia/cuda:12.4.1-base-ubuntu22.04","command":["nvidia-smi"],"resources":{"limits":{"nvidia.com/gpu":"1"}}}]}}'
```

### Alternative : laisser l'operator installer le toolkit

Utile sur une flotte de nœuds où tu ne veux rien installer à la main (seul le driver reste
sur l'hôte). Il faut alors indiquer à l'operator les chemins spécifiques à k3s :

```yaml
toolkit:
  enabled: true
  env:
    - name: CONTAINERD_CONFIG
      value: /var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl
    - name: CONTAINERD_SOCKET
      value: /run/k3s/containerd/containerd.sock
    - name: CONTAINERD_RUNTIME_CLASS
      value: nvidia
```

Les chemins varient selon la version de k3s (containerd 2 utilise `config-v3.toml.tmpl`) :
vérifie le dossier `/var/lib/rancher/k3s/agent/etc/containerd/` avant.

## Étape 4 : le pod

Voir `deploy/components/serving-gpu/vllm.yaml` : `runtimeClassName: nvidia`,
`resources.limits["nvidia.com/gpu"]: 1`, un `nodeSelector` sur le label posé par GPU Feature
Discovery (`nvidia.com/gpu.present`) et une toleration pour les nœuds GPU "taintés". Sans la
RuntimeClass, le pod est bien planifié mais CUDA ne trouve aucun périphérique.

## Dimensionner selon ta carte

Règle de calcul : VRAM ≈ poids du modèle + KV cache + ~1 Go de marge CUDA.

| VRAM | Ce qui tient | Réglage |
|---|---|---|
| 24 Go (RTX 3090/4090, L4) | Mistral 7B en BF16, contexte 8k | défaut |
| 12-16 Go (RTX 3060/4070, T4) | Mistral 7B quantisé AWQ 4 bits, contexte 4k | composant `model-mistral-7b-awq` dans l'overlay |
| 8 Go | idem, contexte 2k ; ou Qwen3 4B AWQ, contexte 16k (agents kagent) | même composant, `MAX_MODEL_LEN: "2048"` ; ou `model-qwen3-4b-awq` |
| Pas de GPU NVIDIA | profil `cpu` (llama.cpp, GGUF 4 bits) | `profile = "cpu"` |

## Poste de dev sous WSL2 (overlay `gpu-local`)

Sous WSL2, le driver est celui de Windows : le GPU apparaît comme `/dev/dxg`, les bibliothèques
CUDA dans `/usr/lib/wsl/lib`, et il n'y a ni `/dev/nvidia*` ni bus PCI visible. Conséquences :

| Couche | Sous WSL2 |
|---|---|
| 1. Driver | Driver Windows, rien à installer dans WSL (surtout pas de driver Linux) |
| 2. Container toolkit | Identique : `sudo infra/local/gpu-host.sh` (version figée, redémarre k3s si besoin) |
| 3. Device plugin | GPU Operator inutilisable (validator et DCGM cherchent `/dev/nvidia*`) : device plugin seul, qui gère WSL2, `infra/local/gpu.sh` |
| 3bis. Métriques GPU | Pas de DCGM sous WSL2 : seules les métriques vLLM remontent |
| 4. Pod | `deploy/overlays/gpu-local` |

NFD ne voyant pas le GPU, `gpu.sh` pose lui-même le label `nvidia.com/gpu.present=true`
(attendu par le device plugin et par vLLM).

```bash
sudo infra/local/gpu-host.sh
infra/local/gpu.sh                                   # affiche nvidia.com/gpu: 1
kubectl -n llm delete sts llm --ignore-not-found     # depuis cpu-local : sélecteur immuable
kubectl apply -k deploy/overlays/gpu-local --server-side
```

Avec 8 Go de VRAM, dont ~1,5 Go pris par l'affichage Windows, l'overlay sert Qwen3 4B AWQ
(composant `model-qwen3-4b-awq`) avec `GPU_MEMORY_UTILIZATION=0.75` : si vLLM refuse de
démarrer faute de VRAM libre, fermer les applications Windows qui utilisent le GPU.

## Partager un GPU entre plusieurs pods (time-slicing)

Le device plugin peut annoncer un GPU physique comme N GPU logiques. Pas d'isolation mémoire :
à réserver aux tests (par exemple deux petits modèles). Configuration via
`devicePlugin.config` dans les values de l'operator (documentation NVIDIA "Time-Slicing GPUs
in Kubernetes"). Sur GPU datacenter (A100/H100), préférer MIG, qui isole réellement la mémoire.

## Dépannage

| Symptôme | Cause probable |
|---|---|
| Pod vLLM `Pending`, `Insufficient nvidia.com/gpu` | device plugin absent ou GPU déjà pris |
| `RuntimeError: No CUDA GPUs are available` | `runtimeClassName: nvidia` manquant |
| `CUDA out of memory` au démarrage | modèle trop gros : passer en AWQ ou réduire `--max-model-len` |
| Pods de l'operator en `Init:CrashLoopBackOff` | runtime nvidia non détecté : redémarrer k3s après le toolkit |
