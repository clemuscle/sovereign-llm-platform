output "argocd_application_path" {
  description = "Overlay synchronisé par ArgoCD."
  value       = "deploy/overlays/${local.overlay}"
}

output "urls" {
  description = "Points d'accès HTTPS (Traefik + cert-manager). Les consoles d'admin passent par port-forward."
  value = {
    rag_api = "https://rag.localhost/docs"
    gateway = "https://api.localhost/v1"
    grafana = "make grafana   (port-forward, http://localhost:3000)"
    argocd  = "make argocd    (port-forward, http://localhost:8081)"
  }
}

output "next_steps" {
  value = <<-EOT
    1. make seal-secrets && git add deploy/secrets && git commit && git push
    2. make backup-sealing-key   (clé de déchiffrement : à mettre en lieu sûr)
    3. make status
  EOT
}
