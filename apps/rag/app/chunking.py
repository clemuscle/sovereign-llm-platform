"""Découpage des documents en morceaux ("chunks") avant vectorisation.

Stratégie simple et prévisible : on regroupe les paragraphes jusqu'à `size` caractères,
avec un recouvrement de `overlap` caractères entre deux chunks pour ne pas couper
une idée en deux. Un paragraphe trop long est découpé en fenêtres glissantes.
"""

from __future__ import annotations


def _windows(text: str, size: int, overlap: int) -> list[str]:
    step = size - overlap
    return [text[i : i + size] for i in range(0, max(len(text) - overlap, 1), step)]


def chunk_text(text: str, size: int = 800, overlap: int = 120) -> list[str]:
    if size <= 0:
        raise ValueError("size doit être strictement positif")
    if not 0 <= overlap < size:
        raise ValueError("overlap doit être compris entre 0 et size - 1")

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_windows(paragraph, size, overlap))
            continue

        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= size:
            current = candidate
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n\n{paragraph}" if tail else paragraph
            if len(current) > size:  # le recouvrement ne doit jamais faire déborder le chunk
                current = paragraph

    if current:
        chunks.append(current)
    return chunks
