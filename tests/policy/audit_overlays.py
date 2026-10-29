#!/usr/bin/env python3
"""Audit des overlays rendus, sans cluster : ce que kubeconform ne voit pas.

  - chaque ressource a un namespace, chaque image est résolue par image-pins ;
  - chaque ConfigMap / clé de Secret référencée existe, chaque $(VAR) est défini ;
  - NetworkPolicies : les flux nécessaires passent (egress de la source ET ingress de la
    destination) et les flux interdits sont bloqués (Internet, accès directs aux bases...).

Usage : tests/policy/audit_overlays.py [overlay...]   (défaut : tous)
"""

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OVERLAYS = ["gpu", "gpu-airgap", "cpu", "cpu-airgap", "ci"]
SECRET_KEYS = {
    "litellm-keys": {"MASTER_KEY", "RAG_KEY", "KAGENT_KEY"},
    "litellm-db": {"POSTGRES_PASSWORD"},
    "litellm-redis": {"REDIS_PASSWORD"},
    "rag-api-keys": {"INGEST_API_KEY", "QUERY_API_KEY"},
    "hf-token": {"HF_TOKEN"},
    "kagent-litellm": {"LITELLM_API_KEY"},
}


def sel_match(sel, labels):
    if sel is None:
        return False
    for k, v in (sel.get("matchLabels") or {}).items():
        if labels.get(k) != v:
            return False
    for e in sel.get("matchExpressions") or []:
        if e["operator"] == "In" and labels.get(e["key"]) not in e["values"]:
            return False
    return True


def peer_match(peer, pol_ns, dst):
    if "ipBlock" in peer:
        return dst.get("external", False)
    if dst.get("external"):
        return False
    ns_ok = (
        sel_match(peer["namespaceSelector"], {"kubernetes.io/metadata.name": dst["ns"]})
        if "namespaceSelector" in peer
        else dst["ns"] == pol_ns
    )
    pod_ok = sel_match(peer["podSelector"], dst["labels"]) if "podSelector" in peer else True
    return ns_ok and pod_ok


def port_ok(rule, port):
    ports = rule.get("ports")
    return not ports or any(p.get("port") == port for p in ports)


def allowed(pols, direction, subject, other, port):
    sel = [
        p
        for p in pols
        if p["metadata"]["namespace"] == subject["ns"]
        and direction in p["spec"].get("policyTypes", [])
        and sel_match(p["spec"]["podSelector"], subject["labels"])
        or (
            p["metadata"]["namespace"] == subject["ns"]
            and direction in p["spec"].get("policyTypes", [])
            and p["spec"]["podSelector"] == {}
        )
    ]
    if not sel:
        return True
    key = "egress" if direction == "Egress" else "ingress"
    fld = "to" if key == "egress" else "from"
    for p in sel:
        for rule in p["spec"].get(key, []) or []:
            if port_ok(rule, port) and any(
                peer_match(peer, p["metadata"]["namespace"], other) for peer in rule.get(fld, [])
            ):
                return True
    return False


def flow(pols, src, dst, port):
    return allowed(pols, "Egress", src, dst, port) and (dst.get("external") or allowed(pols, "Ingress", dst, src, port))


errors = []
for name in sys.argv[1:] or OVERLAYS:
    rendered = subprocess.run(
        ["kustomize", "build", str(ROOT / "deploy/overlays" / name)], check=True, capture_output=True, text=True
    ).stdout
    docs = [d for d in yaml.safe_load_all(rendered) if d]
    cms = {
        (d["metadata"]["namespace"], d["metadata"]["name"]): d.get("data", {}) for d in docs if d["kind"] == "ConfigMap"
    }
    pols = [d for d in docs if d["kind"] == "NetworkPolicy"]
    pods = {}
    for d in docs:
        if not d["metadata"].get("namespace") and d["kind"] != "Namespace":
            errors.append(f"{name}: {d['kind']}/{d['metadata']['name']} sans namespace")
        tpl = d.get("spec", {}).get("template")
        if not tpl or "spec" not in tpl:  # SealedSecret : spec.template sans pod
            continue
        ns = d["metadata"]["namespace"]
        spec = tpl["spec"]
        labels = tpl["metadata"]["labels"]
        pods[labels.get("app.kubernetes.io/name")] = {"ns": ns, "labels": labels}
        for c in spec.get("initContainers", []) + spec["containers"]:
            if "/" not in c["image"] and ":" not in c["image"]:
                errors.append(f"{name}: image non résolue {c['image']}")
            env_names = {e["name"] for e in c.get("env", [])}
            for ef in c.get("envFrom", []):
                ref = ef.get("configMapRef")
                if ref:
                    if (ns, ref["name"]) not in cms:
                        errors.append(f"{name}: ConfigMap {ref['name']} absent ({c['name']})")
                    else:
                        env_names |= set(cms[(ns, ref["name"])])
            for e in c.get("env", []):
                sk = (e.get("valueFrom") or {}).get("secretKeyRef")
                if sk and sk["key"] not in SECRET_KEYS.get(sk["name"], set()):
                    errors.append(f"{name}: clé {sk['name']}/{sk['key']} inconnue")
                for var in re.findall(r"\$\((\w+)\)", e.get("value", "")):
                    if var not in env_names:
                        errors.append(f"{name}: $({var}) non défini dans env de {c['name']}")
            for a in c.get("args", []) + c.get("command", []):
                for var in re.findall(r"\$\((\w+)\)", a):
                    if var not in env_names:
                        errors.append(f"{name}: $({var}) non défini pour {c['name']}")
        for v in spec.get("volumes", []):
            if "configMap" in v and (ns, v["configMap"]["name"]) not in cms:
                errors.append(f"{name}: volume ConfigMap {v['configMap']['name']} absent")
    mon = {"ns": "monitoring", "labels": {}}
    kag = {"ns": "kagent", "labels": {}}
    traefik = {"ns": "kube-system", "labels": {"app.kubernetes.io/name": "traefik"}}
    dns = {"ns": "kube-system", "labels": {"k8s-app": "kube-dns"}}
    otel = {"ns": "monitoring", "labels": {}}
    internet = {"external": True}
    zot = {"ns": "model-store", "labels": {"app.kubernetes.io/name": "zot"}}
    serving = next(k for k in pods if k in ("vllm", "llamacpp"))
    must = [
        ("rag-app", pods["rag-app"], pods["litellm"], 4000),
        ("rag-app", pods["rag-app"], pods["qdrant"], 6333),
        ("litellm", pods["litellm"], pods[serving], 8000),
        ("litellm", pods["litellm"], pods["embeddings"], 80),
        ("litellm", pods["litellm"], pods["litellm-db"], 5432),
        ("litellm", pods["litellm"], pods["litellm-redis"], 6379),
        ("bootstrap", pods["litellm-keys-bootstrap"], pods["litellm"], 4000),
        ("kagent", kag, pods["litellm"], 4000),
        ("prometheus", mon, pods[serving], 8000),
        ("rag->dns", pods["rag-app"], dns, 53),
        ("llm->otel", pods[serving], otel, 4317),
    ]
    if "ci" not in name:
        must.append(("traefik", traefik, pods["rag-app"], 8080))
    airgap = "airgap" in name
    must.append(("fetch", pods[serving], zot if airgap else internet, 5000 if airgap else 443))
    deny = [
        ("rag->internet", pods["rag-app"], internet, 443),
        ("litellm->internet", pods["litellm"], internet, 443),
        ("rag->llm direct", pods["rag-app"], pods[serving], 8000),
        ("rag->db", pods["rag-app"], pods["litellm-db"], 5432),
        ("kagent->qdrant", kag, pods["qdrant"], 6333),
        ("qdrant->internet", pods["qdrant"], internet, 443),
    ]
    if airgap:
        deny.append(("serving->internet (airgap)", pods[serving], internet, 443))
    if "ci" not in name:
        deny.append(("kube-system autre que traefik", {"ns": "kube-system", "labels": {}}, pods["rag-app"], 8080))
    for label, s, d, port in must:
        if not flow(pols, s, d, port):
            errors.append(f"{name}: flux REQUIS bloqué : {label}:{port}")
    for label, s, d, port in deny:
        if flow(pols, s, d, port):
            errors.append(f"{name}: flux INTERDIT ouvert : {label}:{port}")
    print(f"{name:16} {len(docs)} ressources, {len(must)} flux requis et {len(deny)} interdits vérifiés")
print("\n".join(errors) or "AUCUNE ANOMALIE")
sys.exit(1 if errors else 0)
