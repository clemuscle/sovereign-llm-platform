"""Crée ou met à jour les clés virtuelles LiteLLM des clients de la plateforme. Idempotent.

Une clé par client (RAG, agent kagent...) au lieu d'une clé maître partagée :
  - chaque appel est attribué à un client (traces, coûts),
  - quotas indépendants (rpm), partagés entre réplicas LiteLLM via Redis,
  - révocation d'un client sans toucher aux autres.

Les valeurs des clés viennent de Secrets Kubernetes (scellés dans Git) : la clé d'un client est
donc connue avant sa création et reste stable d'un cluster à l'autre.

Exécuté par un Job ArgoCD (hook PostSync) à chaque synchronisation :
  - la clé n'existe pas    -> /key/generate
  - la clé existe          -> /key/update (aligne modèles et quotas sur la déclaration)

Variables : LITELLM_URL, LITELLM_MASTER_KEY, CLIENTS_FILE (JSON), et pour chaque client la
variable nommée par son champ "key_env".
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def http_client(base_url: str, master_key: str):
    def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(  # noqa: S310 - URL interne au cluster, fournie par la config
            base_url.rstrip("/") + path,
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as err:
            try:
                return err.code, json.loads(err.read() or b"{}")
            except json.JSONDecodeError:
                return err.code, {}

    return call


def wait_ready(call, attempts: int = 60, delay: float = 5.0, sleep=time.sleep) -> None:
    for i in range(attempts):
        try:
            status, _ = call("GET", "/health/readiness")
            if status == 200:
                return
        except OSError:
            pass
        print(f"LiteLLM pas encore prêt ({i + 1}/{attempts})", flush=True)
        sleep(delay)
    raise TimeoutError("LiteLLM indisponible")


def desired_payload(client: dict, key: str) -> dict:
    payload = {
        "key": key,
        "key_alias": client["alias"],
        "models": client["models"],
        "metadata": {"managed_by": "litellm-keys-bootstrap"},
    }
    for limit in ("rpm_limit", "tpm_limit", "max_budget"):
        if limit in client:
            payload[limit] = client[limit]
    return payload


def reconcile(call, clients: list[dict], env: dict[str, str]) -> dict[str, str]:
    """Retourne {alias: action} avec action dans created / updated."""
    results = {}
    for client in clients:
        key = env.get(client["key_env"], "")
        if not key.startswith("sk-"):
            raise ValueError(f"{client['key_env']} absente ou invalide (doit commencer par 'sk-')")
        payload = desired_payload(client, key)
        status, _ = call("GET", "/key/info?" + urllib.parse.urlencode({"key": key}))
        if status == 200:
            action, path = "updated", "/key/update"
        elif status in (400, 401, 404):
            action, path = "created", "/key/generate"
        else:
            raise RuntimeError(f"réponse inattendue de /key/info pour {client['alias']} : {status}")
        status, body = call("POST", path, payload)
        if status != 200:
            raise RuntimeError(f"{path} a échoué pour {client['alias']} : {status} {body}")
        results[client["alias"]] = action
        print(f"clé {client['alias']} : {action}", flush=True)
    return results


def main() -> int:
    clients = json.loads(open(os.environ.get("CLIENTS_FILE", "/config/clients.json"), encoding="utf-8").read())
    call = http_client(os.environ["LITELLM_URL"], os.environ["LITELLM_MASTER_KEY"])
    try:
        wait_ready(call)
        reconcile(call, clients, dict(os.environ))
    except Exception as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
