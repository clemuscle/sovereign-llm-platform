# ---------------------------------------------------------------------------
# Couche "plateforme" : ce qui est installé une fois par cluster et doit l'être
# dans un ordre précis (CRD, operators). Les workloads sont gérés par ArgoCD
# (GitOps) à partir de deploy/overlays/<profil>[-airgap].
#
# Toutes les versions sont figées (variables.tf) : apply est reproductible et idempotent.
# Distribution-agnostique : Terraform cible un cluster existant via son kubeconfig.
# ---------------------------------------------------------------------------

locals {
  is_gpu  = var.profile == "gpu"
  overlay = var.airgap ? "${var.profile}-airgap" : var.profile
  v       = var.chart_versions
}

# --- Namespaces ------------------------------------------------------------

resource "kubernetes_namespace_v1" "llm" {
  metadata {
    name = "llm"
    labels = {
      "pod-security.kubernetes.io/enforce" = "baseline"
      "pod-security.kubernetes.io/warn"    = "restricted"
    }
  }
}

resource "kubernetes_namespace_v1" "monitoring" {
  metadata {
    name = "monitoring"
  }
}

resource "kubernetes_namespace_v1" "kagent" {
  count = var.enable_kagent ? 1 : 0
  metadata {
    name = "kagent"
  }
}

resource "kubernetes_namespace_v1" "model_store" {
  count = var.airgap ? 1 : 0
  metadata {
    name = "model-store"
  }
}

# --- Socle : certificats et secrets ----------------------------------------

resource "helm_release" "cert_manager" {
  name             = "cert-manager"
  repository       = "https://charts.jetstack.io"
  chart            = "cert-manager"
  version          = local.v["cert_manager"]
  namespace        = "cert-manager"
  create_namespace = true
  values           = [file("${path.module}/values/cert-manager.yaml")]
}

resource "helm_release" "sealed_secrets" {
  name             = "sealed-secrets"
  repository       = "https://bitnami-labs.github.io/sealed-secrets"
  chart            = "sealed-secrets"
  version          = local.v["sealed_secrets"]
  namespace        = "sealed-secrets"
  create_namespace = true
  values           = [file("${path.module}/values/sealed-secrets.yaml")]
}

# --- GitOps : ArgoCD -------------------------------------------------------

resource "helm_release" "argo_cd" {
  name             = "argo-cd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = local.v["argo_cd"]
  namespace        = "argocd"
  create_namespace = true
  values           = [file("${path.module}/values/argo-cd.yaml")]
}

# --- Observabilité ---------------------------------------------------------

resource "helm_release" "kube_prometheus_stack" {
  name       = "kube-prometheus-stack"
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "kube-prometheus-stack"
  version    = local.v["kube_prometheus_stack"]
  namespace  = kubernetes_namespace_v1.monitoring.metadata[0].name
  timeout    = 900
  values     = [file("${path.module}/values/kube-prometheus-stack.yaml")]
}

resource "helm_release" "loki" {
  name       = "loki"
  repository = "https://grafana-community.github.io/helm-charts" # chart OSS (grafana/helm-charts = édition Enterprise)
  chart      = "loki"
  version    = local.v["loki"]
  namespace  = kubernetes_namespace_v1.monitoring.metadata[0].name
  values     = [file("${path.module}/values/loki.yaml")]
}

resource "helm_release" "tempo" {
  name       = "tempo"
  repository = "https://grafana-community.github.io/helm-charts"
  chart      = "tempo"
  version    = local.v["tempo"]
  namespace  = kubernetes_namespace_v1.monitoring.metadata[0].name
  values     = [file("${path.module}/values/tempo.yaml")]
}

resource "helm_release" "otel_collector" {
  name       = "otel-collector"
  repository = "https://open-telemetry.github.io/opentelemetry-helm-charts"
  chart      = "opentelemetry-collector"
  version    = local.v["otel_collector"]
  namespace  = kubernetes_namespace_v1.monitoring.metadata[0].name
  values     = [file("${path.module}/values/otel-collector.yaml")]

  depends_on = [helm_release.loki, helm_release.tempo, helm_release.kube_prometheus_stack]
}

# --- GPU : NVIDIA GPU Operator ---------------------------------------------

resource "helm_release" "gpu_operator" {
  count            = local.is_gpu ? 1 : 0
  name             = "gpu-operator"
  repository       = "https://helm.ngc.nvidia.com/nvidia"
  chart            = "gpu-operator"
  version          = local.v["gpu_operator"]
  namespace        = "gpu-operator"
  create_namespace = true
  timeout          = 900
  values           = [file("${path.module}/values/gpu-operator.yaml")]

  depends_on = [helm_release.kube_prometheus_stack] # CRD ServiceMonitor du DCGM exporter
}

# --- Autoscaling : KEDA (optionnel) -----------------------------------------

resource "helm_release" "keda" {
  count            = var.enable_keda ? 1 : 0
  name             = "keda"
  repository       = "https://kedacore.github.io/charts"
  chart            = "keda"
  version          = local.v["keda"]
  namespace        = "keda"
  create_namespace = true
}

# --- Air-gapped : registry OCI interne pour les images et les modèles -------

resource "helm_release" "zot" {
  count      = var.airgap ? 1 : 0
  name       = "zot"
  repository = "https://zotregistry.dev/helm-charts"
  chart      = "zot"
  version    = local.v["zot"]
  namespace  = kubernetes_namespace_v1.model_store[0].metadata[0].name
  values = [templatefile("${path.module}/values/zot.yaml.tftpl", {
    node_port = var.zot_node_port
  })]
}

# --- Agents : kagent (CNCF Sandbox) ----------------------------------------

resource "helm_release" "kagent_crds" {
  count     = var.enable_kagent ? 1 : 0
  name      = "kagent-crds"
  chart     = "oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds"
  version   = local.v["kagent"]
  namespace = kubernetes_namespace_v1.kagent[0].metadata[0].name
}

resource "helm_release" "kagent" {
  count     = var.enable_kagent ? 1 : 0
  name      = "kagent"
  chart     = "oci://ghcr.io/kagent-dev/kagent/helm/kagent"
  version   = local.v["kagent"]
  namespace = kubernetes_namespace_v1.kagent[0].metadata[0].name
  timeout   = 600
  values    = [file("${path.module}/values/kagent.yaml")]

  depends_on = [helm_release.kagent_crds, helm_release.otel_collector]
}

# --- Point d'entrée GitOps : l'Application ArgoCD des workloads ------------

resource "helm_release" "workloads_app" {
  name       = "workloads"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  version    = local.v["argocd_apps"]
  namespace  = "argocd"

  values = [yamlencode({
    applications = {
      "llm-platform" = {
        namespace = "argocd"
        project   = "default"
        source = {
          repoURL        = var.git_repo_url
          targetRevision = var.git_revision
          path           = "deploy/overlays/${local.overlay}"
        }
        destination = {
          server    = "https://kubernetes.default.svc"
          namespace = "llm"
        }
        syncPolicy = {
          automated   = { prune = true, selfHeal = true }
          syncOptions = ["ServerSideApply=true"]
          retry = {
            limit   = 10
            backoff = { duration = "30s", factor = 2, maxDuration = "10m" }
          }
        }
      }
    }
  })]

  depends_on = [
    helm_release.argo_cd,
    helm_release.cert_manager,   # CRD Issuer / Certificate
    helm_release.sealed_secrets, # CRD SealedSecret
    helm_release.kagent,         # CRD Agent / ModelConfig
    helm_release.kube_prometheus_stack,
    kubernetes_namespace_v1.llm,
  ]
}
