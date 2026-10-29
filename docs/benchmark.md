# Benchmarks

Résultats de `make bench`, à compléter avec ta machine. Chaque ligne d'un tableau est un palier
d'utilisateurs simultanés ; 16 requêtes par palier, 256 tokens maximum par réponse.

## Comment lire les résultats

- **TTFT p95** : latence ressentie ; c'est l'indicateur du SLO (< 1 s).
- **Tokens/s par requête** : vitesse d'affichage pour un utilisateur.
- **Débit total** : capacité de la plateforme. Il augmente avec la concurrence tant que le
  GPU n'est pas saturé (continuous batching), puis le TTFT se dégrade : ce point de bascule
  donne le nombre d'utilisateurs simultanés supportable par GPU.

## Expériences à mener

| Expérience | Ce qu'on fait varier | Question à laquelle elle répond |
|---|---|---|
| Concurrence | 1, 4, 8, 16 utilisateurs | Combien d'utilisateurs par GPU en respectant le SLO ? |
| Quantization | BF16 vs AWQ 4 bits | Gain de débit et de mémoire, perte de qualité ? |
| Contexte | `--max-model-len` 4k / 8k / 16k | Impact sur le KV cache et la concurrence |
| Prefix caching | avec / sans `--enable-prefix-caching` | Gain sur le RAG (prompt système commun) |
| Décodage contraint | avec / sans `response_format` | Surcoût sur le TTFT |
| CPU vs GPU | profil `cpu` vs `gpu` | Ordre de grandeur pour argumenter un achat de GPU |

## Résultats

<!-- Colle ici la sortie de `make bench LABEL="..."` -->

_À compléter._
