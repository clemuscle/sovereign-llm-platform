#!/usr/bin/env python3
"""Rend l'overlay ci en remplaçant nos images publiées par celles construites localement.

En CI, rag-app et platform-tools viennent d'être construits et chargés dans kind
(tags :e2e) : on teste le code de la PR, pas la dernière image publiée.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
pins = yaml.safe_load((ROOT / "deploy/components/image-pins/kustomization.yaml").read_text())
published = {e["name"]: e["newName"] for e in pins["images"] if e["name"] in ("rag-app", "platform-tools")}

with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
    overlay = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": [str((ROOT / "deploy/overlays/ci").relative_to(Path(tmp), walk_up=True))],
        "images": [{"name": published[n], "newName": n, "newTag": "e2e"} for n in published],
    }
    (Path(tmp) / "kustomization.yaml").write_text(yaml.safe_dump(overlay))
    rendered = subprocess.run(["kustomize", "build", tmp], check=True, capture_output=True, text=True).stdout
    # On force IfNotPresent : kubelet doit utiliser l'image chargée dans kind, pas la chercher en ligne.
    docs = [d for d in yaml.safe_load_all(rendered) if d]
    for doc in docs:
        spec = doc.get("spec", {}).get("template", {}).get("spec", {})
        for c in spec.get("containers", []) + spec.get("initContainers", []):
            if c["image"].endswith(":e2e"):
                c["imagePullPolicy"] = "IfNotPresent"
    sys.stdout.write(yaml.safe_dump_all(docs, sort_keys=False))
