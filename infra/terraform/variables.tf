variable "kubeconfig_path" {
  description = "Chemin du kubeconfig. Pour k3s : copie de /etc/rancher/k3s/k3s.yaml (make kubeconfig)."
  type        = string
  default     = "~/.kube/k3s.yaml"
}

variable "kube_context" {
  description = "Contexte kubeconfig à utiliser (k3s nomme le sien 'default')."
  type        = string
  default     = "default"
}

variable "profile" {
  description = "Serveur de modèle : 'gpu' (vLLM, GPU NVIDIA) ou 'cpu' (llama.cpp)."
  type        = string
  default     = "gpu"

  validation {
    condition     = contains(["gpu", "cpu"], var.profile)
    error_message = "profile doit valoir 'gpu' ou 'cpu'."
  }
}

variable "airgap" {
  description = "Mode air-gapped : registry OCI interne (Zot) pour les poids, aucun accès Internet depuis les workloads."
  type        = bool
  default     = false
}

variable "git_repo_url" {
  description = "URL du dépôt Git que ArgoCD synchronise (ton fork). En air-gapped : miroir Git interne."
  type        = string
}

variable "git_revision" {
  description = "Branche ou tag suivi par ArgoCD."
  type        = string
  default     = "main"
}

variable "enable_kagent" {
  description = "Installe kagent (couche agents). Si false, retirer ../../agents des overlays (CRD absentes)."
  type        = bool
  default     = true
}

variable "enable_keda" {
  description = "Installe KEDA, requis par le composant autoscaling-keda (à partir de 2 GPU)."
  type        = bool
  default     = false
}

variable "zot_node_port" {
  description = "Port exposé par la registry Zot sur les nœuds, utilisé par containerd comme miroir (air-gapped)."
  type        = number
  default     = 30500
}

# Versions figées : un `terraform apply` installe toujours la même chose.
# Mises à jour proposées par Dependabot / Renovate, appliquées par pull request.
variable "chart_versions" {
  description = "Version de chaque chart Helm."
  type        = map(string)
  default = {
    argo_cd               = "10.11.6"
    argocd_apps           = "2.0.7"
    cert_manager          = "v1.21.3"
    sealed_secrets        = "2.22.0"
    gpu_operator          = "v26.7.2"
    kube_prometheus_stack = "92.14.1"
    loki                  = "20.0.3"
    tempo                 = "3.2.1"
    otel_collector        = "0.178.0"
    kagent                = "0.10.4"
    keda                  = "2.21.0"
    zot                   = "0.1.128"
  }
}
