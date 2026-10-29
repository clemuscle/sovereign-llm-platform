# Runbook

Chaque alerte renvoie ici. Objectif : savoir quoi regarder en moins de 2 minutes.

## Budget d'erreur

Alertes `RAGAvailabilityBudgetBurn*` et `RAGLatencyBudgetBurn*`.

- **Fast** (critique) : le budget mensuel fond 14 fois trop vite. Agir maintenant.
- **Slow** (warning) : 6 fois trop vite sur 6 h. À traiter dans la journée.

1. Grafana, dashboard *Sovereign LLM Platform* : quelle fenêtre monte, disponibilité ou latence ?
2. Explore > Tempo : `{ resource.service.name = "rag-app" && status = error }` pour les traces
   en erreur ; le span fautif indique le maillon (LiteLLM, Qdrant, moteur).
3. Selon le maillon :
   - moteur : voir [Moteur indisponible](#moteur-indisponible) ou [Saturation](#saturation)
   - LiteLLM : `kubectl -n llm logs deploy/litellm`, état de `litellm-db` et `litellm-redis`
   - Qdrant : `kubectl -n llm logs statefulset/qdrant`
4. Une mise en production récente ? ArgoCD > *llm-platform* > *History* : `git revert` du commit.

## Moteur indisponible

Alerte `LLMEngineDown`.

```bash
kubectl -n llm get pods -l app.kubernetes.io/component=serving
kubectl -n llm logs statefulset/llm -c fetch-model   # téléchargement des poids
kubectl -n llm logs statefulset/llm                  # chargement du modèle
```

| Symptôme | Cause probable | Action |
|---|---|---|
| `Init:Error`, « n'est pas un SHA de commit » | révision non figée | `make pin`, commit |
| `Init:Error`, 401/403 Hugging Face | modèle « gated » sans jeton | `HF_TOKEN=... make seal-secrets` (FORCE=1 si déjà scellé) |
| `Init:Error`, timeout | egress fermé ou source injoignable | vérifier le composant model-source-* de l'overlay |
| `Pending`, `Insufficient nvidia.com/gpu` | GPU absent ou occupé | `make check-gpu`, docs/gpu-on-k3s.md |
| `CrashLoopBackOff`, `CUDA out of memory` | modèle trop gros pour la carte | composant `model-mistral-7b-awq` ou `MAX_MODEL_LEN` plus petit |

## Saturation

Alerte `LLMSaturated` : des requêtes attendent un slot du moteur depuis 10 minutes.

1. Dashboard : occupation du KV cache proche de 100 % ? Les requêtes longues l'épuisent.
2. Leviers, du plus rapide au plus coûteux : réduire `MAX_MODEL_LEN` ; quantiser (AWQ) ;
   baisser les quotas `rpm_limit` d'un client trop gourmand (`clients.json`) ; ajouter un GPU
   (composant `autoscaling-keda`).

## Erreur GPU

Alertes `GPUHardwareError` (code XID) et `GPUOverheating`.

1. `nvidia-smi -q | grep -i -A3 xid` sur le nœud. XID 48, 63, 64, 79 : matériel ou bus, prévoir un
   redémarrage du nœud et, si récurrent, un remplacement.
2. Surchauffe : ventilation, limite de puissance (`nvidia-smi -pl`), charge.
3. Drainer le nœud : `kubectl drain <nœud> --ignore-daemonsets` (les PDB protègent la gateway et le RAG).

## Restauration complète

1. Nouveau cluster, puis restaurer la clé Sealed Secrets **avant** ArgoCD :
   `kubectl apply -f sealing-key-backup.yaml` dans le namespace `sealed-secrets`.
2. `make infra-apply` : ArgoCD redéploie tout depuis Git, les secrets se déchiffrent.
3. Données : Qdrant et PostgreSQL sont reconstructibles (ré-ingestion des documents, clés
   recréées par le Job de bootstrap) ; la consommation historique de LiteLLM est perdue sans
   sauvegarde de PostgreSQL.
