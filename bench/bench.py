"""Benchmark de charge du serveur d'inférence, à travers la gateway.

Mesure ce qui compte pour l'utilisateur et pour le dimensionnement :
  - TTFT (time to first token) : latence ressentie,
  - débit de décodage par requête (tokens/s),
  - débit agrégé de la plateforme selon le nombre d'utilisateurs simultanés.

Exemple :
  python bench/bench.py --api-key <clé LiteLLM> --ca-cert .secrets/platform-ca.crt \
      --concurrency 1 4 8 --requests 16 --label "RTX 4090 / BF16 / ctx 8k"

Produit un tableau Markdown (à coller dans docs/benchmark.md) et un fichier JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime

import httpx

PROMPTS = [
    "Explique en cinq phrases ce qu'est le KV cache d'un LLM.",
    "Donne trois bonnes pratiques pour déployer un modèle de langage sur Kubernetes.",
    "Résume la différence entre RAG et fine-tuning pour un public non technique.",
    "Écris une checklist de mise en production pour une API d'inférence.",
]


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


async def one_request(client: httpx.AsyncClient, model: str, prompt: str, max_tokens: int) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    start = time.perf_counter()
    first_token_at = None
    completion_tokens = 0
    chunks_with_text = 0

    async with client.stream("POST", "/chat/completions", json=payload) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            event = json.loads(line[6:])
            for choice in event.get("choices") or []:
                if (choice.get("delta") or {}).get("content"):
                    chunks_with_text += 1
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
            if event.get("usage"):
                completion_tokens = event["usage"].get("completion_tokens") or 0

    end = time.perf_counter()
    tokens = completion_tokens or chunks_with_text  # repli si le serveur ne renvoie pas l'usage
    ttft = (first_token_at or end) - start
    decode_time = max(end - (first_token_at or end), 1e-6)
    return {"ttft": ttft, "latency": end - start, "tokens": tokens, "decode_tps": tokens / decode_time}


async def run_level(args, concurrency: int) -> dict:
    sem = asyncio.Semaphore(concurrency)
    headers = {"Authorization": f"Bearer {args.api_key}"}
    verify = args.ca_cert or True
    async with httpx.AsyncClient(
        base_url=args.base_url, headers=headers, timeout=args.timeout, verify=verify
    ) as client:

        async def guarded(i: int):
            async with sem:
                try:
                    return await one_request(client, args.model, PROMPTS[i % len(PROMPTS)], args.max_tokens)
                except Exception as exc:  # noqa: BLE001 - on compte les erreurs sans arrêter le bench
                    return {"error": repr(exc)}

        t0 = time.perf_counter()
        results = await asyncio.gather(*(guarded(i) for i in range(args.requests)))
        wall = time.perf_counter() - t0

    ok = [r for r in results if "error" not in r]
    errors = len(results) - len(ok)
    if errors:
        print(f"  {errors} erreur(s), ex. : {next(r['error'] for r in results if 'error' in r)}")
    ttfts = [r["ttft"] for r in ok]
    return {
        "concurrency": concurrency,
        "requests": len(results),
        "errors": errors,
        "ttft_p50": percentile(ttfts, 0.50),
        "ttft_p95": percentile(ttfts, 0.95),
        "latency_p95": percentile([r["latency"] for r in ok], 0.95),
        "decode_tps_per_request": statistics.mean([r["decode_tps"] for r in ok]) if ok else float("nan"),
        "throughput_tps": sum(r["tokens"] for r in ok) / wall,
    }


def to_markdown(label: str, rows: list[dict]) -> str:
    lines = [
        f"### {label}",
        "",
        "| Utilisateurs simultanés | TTFT p50 | TTFT p95 | Latence p95 | Tokens/s par requête "
        "| Débit total (tokens/s) | Erreurs |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['concurrency']} | {r['ttft_p50']:.2f} s | {r['ttft_p95']:.2f} s | {r['latency_p95']:.1f} s "
            f"| {r['decode_tps_per_request']:.1f} | {r['throughput_tps']:.1f} | {r['errors']}/{r['requests']} |"
        )
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="https://api.localhost/v1")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default="chat", help="nom logique côté gateway")
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 4, 8])
    parser.add_argument("--requests", type=int, default=16, help="requêtes par palier")
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--ca-cert", default=None, help="CA de la plateforme (make trust-ca), sinon CA système")
    parser.add_argument("--label", default="Configuration non renseignée")
    parser.add_argument("--out", default="bench/results.json")
    args = parser.parse_args()

    rows = []
    for level in args.concurrency:
        print(f"→ palier {level} utilisateur(s) simultané(s)…", flush=True)
        rows.append(await run_level(args, level))

    print("\n" + to_markdown(args.label, rows))
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"label": args.label, "date": datetime.now(UTC).isoformat(), "results": rows}, fh, indent=2)
    print(f"\nRésultats bruts : {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
