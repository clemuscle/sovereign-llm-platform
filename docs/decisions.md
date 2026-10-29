# Décisions d'architecture

Format court inspiré des ADR : contexte, décision, conséquences et limites assumées.

## 1. Séparer plateforme (Terraform) et workloads (ArgoCD)

**Décision.** Terraform installe ce qui change rarement et exige un ordre (CRD, operators,
ArgoCD). ArgoCD synchronise en continu `deploy/overlays/<profil>`, avec `selfHeal` : une
modification manuelle dans le cluster est annulée.
**Conséquence.** Le dépôt Git est la source de vérité des workloads ; un rollback est un `git revert`.

## 2. Tout figer : charts, images, modèles

**Contexte.** Un tag (`latest`, `main-stable`) ou une branche de modèle (`main`) peut changer sans
que Git change : deux déploiements du même commit ne sont alors plus identiques.
**Décision.** Versions de charts dans `variables.tf` ; images par digest sha256 dans un composant
unique (`image-pins`) ; modèles par SHA de commit Hugging Face. `scripts/pin.py` résout et contrôle,
le model-fetcher refuse une révision non figée.
**Mise à jour.** Volontaire, par pull request (Dependabot, `pin.py --update`), donc testée par la CI.

## 3. Le modèle est une configuration, le moteur un composant

**Décision.** Chaque serveur expose son modèle sous le nom `llm` ; LiteLLM publie le nom logique
`chat`. Les paramètres du modèle vivent dans le ConfigMap `llm-model`, injectés dans les arguments
via `$(VAR)`. Le moteur (vLLM ou llama.cpp) est un composant Kustomize.
**Preuve.** La CI déploie un autre modèle (Qwen 0.5B) par un simple patch de ConfigMap.

## 4. Poids téléchargés par un initContainer, serveur hors ligne

**Décision.** `model-fetcher` place les poids sur le volume (idempotent : fichier témoin, reprise
après coupure, suppression des fichiers d'une ancienne révision), puis le serveur démarre sur un
dossier local avec `HF_HUB_OFFLINE=1`. Seul `ALLOW_PATTERNS` est téléchargé (Mistral 7B : format
natif uniquement, ~15 Go au lieu de ~30).
**Conséquence.** Le serveur de modèle n'a jamais besoin d'Internet ; la source des poids (Hugging
Face ou registry interne) se change sans toucher au serveur.

## 5. Air-gapped : les modèles comme artefacts OCI

**Décision.** En mode isolé, les poids sont poussés dans la registry interne (Zot) en artefacts OCI
(`oras push`), étiquetés par leur révision et optionnellement figés par digest. Une seule
infrastructure stocke images et modèles, avec les mêmes outils (réplication, rétention, contrôle
d'accès). Les images passent par un miroir containerd : aucun manifest ne change.
**Limite.** Le premier déploiement suppose un amorçage connecté (charts Helm, images). Pour un site
isolé dès le premier jour : archive transportable avec Zarf ou Hauler (feuille de route).

## 6. Egress default-deny

**Décision.** Toutes les sorties du namespace `llm` sont fermées, sauf DNS et télémétrie. Le mode
connecté ouvre HTTPS aux seuls pods qui téléchargent des poids ; le mode air-gapped ne les autorise
qu'à joindre la registry interne.
**Limite.** Une NetworkPolicy filtre par IP : restreindre à `huggingface.co` exige une politique
FQDN (Cilium) ou un proxy de sortie. Télémétries d'éditeurs coupées explicitement (vLLM, Qdrant,
Hugging Face, grille tarifaire LiteLLM).

## 7. StatefulSet pour les serveurs de modèle

**Contexte.** Avec un Deployment et un volume ReadWriteOnce partagé, un second réplica ne peut pas
monter le volume sur un autre nœud, et une mise à jour bloque sur un GPU unique.
**Décision.** StatefulSet avec `volumeClaimTemplates` (un cache de poids par réplica) ; la mise à
jour arrête l'ancien pod avant de créer le nouveau.
**Autoscaling.** KEDA sur `llm:requests_waiting` (un HPA CPU n'a pas de sens : la VRAM est
pré-allouée et le GPU sature avant le CPU).
**Limite.** Le Service répartit sans tenir compte du KV cache de chaque réplica ; étape suivante :
routage « cache-aware » (llm-d, Gateway API Inference Extension).

## 8. Gateway : une clé virtuelle par client, état externalisé

**Décision.** LiteLLM avec PostgreSQL (clés, consommation) et Valkey (compteurs de quotas partagés
entre réplicas). Un Job idempotent (hook ArgoCD PostSync) crée ou met à jour une clé par client
(`clients.json`) : RAG et kagent ont chacun leur quota, leur traçabilité et sont révocables
séparément. La clé maître ne sert qu'à l'administration.
**Choix de Valkey.** Fork open source de Redis sous la Linux Foundation, après le changement de
licence de Redis.

## 9. Secrets chiffrés dans Git (Sealed Secrets)

**Décision.** `make seal-secrets` génère les valeurs une fois, les chiffre avec la clé publique du
contrôleur et les écrit dans `deploy/secrets/` (commité, appliqué avant les workloads).
**Conséquence.** Un cluster se reconstruit depuis Git, à condition d'avoir sauvegardé la clé privée
du contrôleur (`make backup-sealing-key`).
**Alternative.** External Secrets Operator avec Vault / OpenBao ou le gestionnaire de secrets d'un
cloud, quand l'organisation en a un : les manifests ne changent pas, seule la source des secrets change.

## 10. SLO mesurés côté utilisateur, alertes par taux de consommation

**Décision.** SLI issus des spans serveur de l'API RAG, convertis en métriques par Tempo :
disponibilité 99 %, latence 95 % des `/ask` sous 8 s. Alertes multi-fenêtres (Google SRE
Workbook) : page à 14,4× sur 1 h et 5 min, ticket à 6× sur 6 h et 30 min.
**Pourquoi.** Identiques quel que soit le moteur ; on alerte sur ce que vit l'utilisateur et sur la
vitesse de dégradation, pas sur un seuil brut. Les règles sont testées par `promtool test rules`.
Les métriques des moteurs sont unifiées (`llm:*`) pour le dashboard et l'autoscaling.
**Dépendance assumée.** Le SLI dépend de la chaîne de traces (Collector, Tempo) ; une panne de
celle-ci se voit comme une absence de données, pas comme une fausse bonne santé.

## 11. Décodage contraint avec sortie bornée

**Décision.** `response_format: json_schema` strict, dérivé d'un modèle Pydantic, avec des bornes
(`maxLength`, nombre de sources). Le moteur applique la grammaire token par token : JSON toujours
valide, jamais tronqué avant la limite de tokens.

## 12. Défenses de l'application RAG

- Clé d'écriture distincte de la clé de lecture : empoisonner la base exige la clé d'écriture.
- Extraits balisés comme données dans le prompt ; balise fermante injectée neutralisée.
- Ré-ingestion atomique pour le lecteur : écriture des nouveaux chunks puis suppression des
  chunks en trop (jamais de document absent ni de chunk orphelin).
- Changement de modèle d'embedding détecté (dimension) au lieu de corrompre l'index.

## 13. Simplifications assumées

Cluster mono-nœud : PostgreSQL, Qdrant, Loki et Tempo en instance unique. En production :
CloudNativePG, Qdrant distribué, Loki et Tempo en mode distribué sur stockage objet.
