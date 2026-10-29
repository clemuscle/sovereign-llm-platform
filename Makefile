# Sovereign LLM Platform — commandes du quotidien. `make help` liste les cibles.
#
#   PROFILE=gpu|cpu        serveur de modèle
#   AIRGAP=true|false      registry interne, aucun accès Internet depuis les workloads

PROFILE ?= gpu
AIRGAP  ?= false
OVERLAY := $(PROFILE)$(if $(filter true,$(AIRGAP)),-airgap,)
TF_DIR  := infra/terraform
TF_VARS := -var profile=$(PROFILE) -var airgap=$(AIRGAP)
VALUES  := .secrets/values.env
CA_CERT := .secrets/platform-ca.crt
secret   = $(shell grep -E '^$(1)=' $(VALUES) 2>/dev/null | cut -d= -f2-)

.DEFAULT_GOAL := help
.PHONY: help kubeconfig check-gpu check-pins pin infra-init infra-plan infra-apply infra-destroy \
        seal-secrets backup-sealing-key stage-models mirror-images deploy-local status logs-llm \
        grafana argocd kagent-ui trust-ca demo-ingest demo-ask bench test test-alerts lint validate e2e-local

help: ## Affiche cette aide
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- Cluster ---------------------------------------------------------------------------------

kubeconfig: ## Copie le kubeconfig de k3s dans ~/.kube/k3s.yaml
	mkdir -p $$HOME/.kube
	sudo cp /etc/rancher/k3s/k3s.yaml $$HOME/.kube/k3s.yaml
	sudo chown $$(id -u):$$(id -g) $$HOME/.kube/k3s.yaml && chmod 600 $$HOME/.kube/k3s.yaml
	@echo "export KUBECONFIG=$$HOME/.kube/k3s.yaml"

check-gpu: ## Vérifie driver, runtime et ressource GPU (profil gpu)
	nvidia-smi
	kubectl get runtimeclass nvidia
	kubectl get nodes -o custom-columns='NODE:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu'

# --- Versions figées --------------------------------------------------------------------------

check-pins: ## Liste ce qui n'est pas figé (révisions de modèles, digests d'images)
	python3 scripts/pin.py check

pin: ## Fige révisions de modèles et digests d'images (OWNER=compte-github la 1re fois)
	@if [ -n "$(OWNER)" ]; then python3 scripts/pin.py owner $(OWNER); fi
	python3 scripts/pin.py models
	python3 scripts/pin.py images
	python3 scripts/pin.py check

# --- Infra (Terraform) ------------------------------------------------------------------------

infra-init: ## terraform init
	terraform -chdir=$(TF_DIR) init

infra-plan: ## terraform plan
	terraform -chdir=$(TF_DIR) plan $(TF_VARS)

infra-apply: ## Installe la plateforme (cert-manager, Sealed Secrets, ArgoCD, observabilité, GPU, kagent)
	terraform -chdir=$(TF_DIR) apply $(TF_VARS)

infra-destroy: ## Désinstalle la plateforme
	terraform -chdir=$(TF_DIR) destroy $(TF_VARS)

# --- Secrets (GitOps) -------------------------------------------------------------------------

seal-secrets: ## Génère et chiffre les secrets dans deploy/secrets (à commiter)
	scripts/seal-secrets.sh

backup-sealing-key: ## Exporte la clé privée de Sealed Secrets (à stocker HORS du dépôt)
	@umask 077 && kubectl -n sealed-secrets get secret -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml > .secrets/sealing-key-backup.yaml
	@echo "Clé exportée dans .secrets/sealing-key-backup.yaml : à placer dans un coffre (jamais dans Git)."

# --- Air-gapped -------------------------------------------------------------------------------

stage-models: ## Pousse les poids des modèles dans la registry interne (OVERLAY=gpu-airgap)
	scripts/stage-models.sh $(OVERLAY)

mirror-images: ## Copie toutes les images utilisées dans la registry interne
	scripts/mirror-images.sh $(OVERLAY)

# --- Déploiement et exploitation ----------------------------------------------------------------

deploy-local: check-pins ## Applique l'overlay sans ArgoCD (itération rapide)
	kubectl apply -k deploy/overlays/$(OVERLAY) --server-side

status: ## État des workloads
	kubectl get pods,pvc,jobs -n llm -o wide
	kubectl get agents.kagent.dev,modelconfigs.kagent.dev -n kagent 2>/dev/null || true

logs-llm: ## Logs du serveur d'inférence (et du téléchargement du modèle)
	kubectl logs -n llm statefulset/llm --all-containers -f --tail=100

grafana: ## Grafana sur http://localhost:3000 (identifiant admin, mot de passe affiché)
	@kubectl -n monitoring get secret kube-prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo
	kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80

argocd: ## ArgoCD sur http://localhost:8081 (identifiant admin, mot de passe affiché)
	@kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
	kubectl -n argocd port-forward svc/argo-cd-argocd-server 8081:80

kagent-ui: ## Interface kagent (CLI kagent requise)
	kagent dashboard

trust-ca: ## Exporte la CA de la plateforme (à ajouter aux certificats de confiance)
	@mkdir -p .secrets && kubectl -n llm get secret llm-platform-ca -o jsonpath='{.data.ca\.crt}' | base64 -d > $(CA_CERT)
	@echo "CA exportée : $(CA_CERT)"

# --- Démo ----------------------------------------------------------------------------------------

demo-ingest: ## Indexe la documentation du projet dans le RAG
	@for f in README.md docs/*.md; do \
		jq -n --arg id "$$f" --rawfile text "$$f" '{documents: [{id: $$id, text: $$text, source: $$id}]}' \
		| curl -sf --cacert $(CA_CERT) -X POST https://rag.localhost/ingest -H 'Content-Type: application/json' \
		  -H "Authorization: Bearer $(call secret,RAG_INGEST_API_KEY)" -d @- ; echo ; \
	done

demo-ask: ## Pose une question au RAG (Q="...")
	@curl -sf --cacert $(CA_CERT) -X POST https://rag.localhost/ask -H 'Content-Type: application/json' \
		-H "Authorization: Bearer $(call secret,RAG_QUERY_API_KEY)" \
		-d "$$(jq -n --arg q "$${Q:-Comment le GPU est-il exposé aux pods sur k3s ?}" '{question: $$q}')" | jq

bench: ## Benchmark TTFT / débit via la gateway (LABEL="ma config")
	python3 bench/bench.py --api-key $(call secret,LITELLM_MASTER_KEY) --ca-cert $(CA_CERT) \
		--concurrency 1 4 8 --requests 16 --label "$${LABEL:-$(OVERLAY)}"

# --- Qualité -----------------------------------------------------------------------------------

test: ## Tests unitaires (applications, scripts)
	cd apps/rag && python3 -m pytest -q
	cd apps/platform-tools && python3 -m pytest -q
	python3 -m pytest -q tests/unit

test-alerts: ## Tests unitaires des règles d'alerte (promtool)
	python3 -c "import yaml; d = yaml.safe_load(open('deploy/base/observability/slo-rules.yaml')); \
		yaml.safe_dump({'groups': d['spec']['groups']}, open('tests/prometheus/rules.yaml', 'w'))"
	promtool check rules tests/prometheus/rules.yaml
	cd tests/prometheus && promtool test rules slo-rules.test.yaml

lint: ## Lint Python, shell et format Terraform
	cd apps/rag && ruff check . && ruff format --check .
	cd apps/platform-tools && ruff check . && ruff format --check .
	ruff check scripts tests bench && ruff format --check scripts tests bench
	shellcheck scripts/*.sh tests/e2e/*.sh
	terraform -chdir=$(TF_DIR) fmt -check -recursive

validate: ## Rend les overlays, valide les schémas et audite références et flux réseau
	@for o in gpu gpu-airgap cpu cpu-airgap ci; do \
		echo "== overlay $$o"; \
		kustomize build deploy/overlays/$$o | kubeconform -strict -summary \
			-schema-location default \
			-schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
			|| exit 1; \
	done
	python3 tests/policy/audit_overlays.py

e2e-local: ## Rejoue le test de bout en bout de la CI sur un cluster kind local
	kind create cluster --name e2e
	docker build -t rag-app:e2e apps/rag && docker build -t platform-tools:e2e apps/platform-tools
	kind load docker-image rag-app:e2e platform-tools:e2e --name e2e
	kubectl apply --server-side \
		-f https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/v0.94.2/example/prometheus-operator-crd/monitoring.coreos.com_servicemonitors.yaml \
		-f https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/v0.94.2/example/prometheus-operator-crd/monitoring.coreos.com_prometheusrules.yaml
	python3 scripts/pin.py models
	python3 tests/e2e/render-overlay.py > /tmp/e2e.yaml
	tests/e2e/create-secrets.sh && kubectl apply --server-side -f /tmp/e2e.yaml
	tests/e2e/smoke.sh
