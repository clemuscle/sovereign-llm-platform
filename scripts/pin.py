#!/usr/bin/env python3
"""Fige tout ce qui pourrait changer sans passer par Git : révisions de modèles et images.

  pin.py check                    liste ce qui n'est pas figé (code retour 1 s'il reste quelque chose)
  pin.py models [--update]        révisions Hugging Face -> SHA de commit   (requiert huggingface_hub)
  pin.py images [--update]        tags d'images -> digest sha256            (requiert crane)
  pin.py owner <compte-github>    renseigne le propriétaire des images rag-app / platform-tools
  pin.py set-image <nom> <digest> fixe le digest d'une image (utilisé par la CI de promotion)

Sans --update, seules les valeurs non figées sont résolues : relancer la commande ne change
rien (idempotent). Avec --update, tout est re-résolu vers la dernière version : c'est une mise
à jour volontaire, qui passe en revue de code comme n'importe quel changement.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
PINS = DEPLOY / "components" / "image-pins" / "kustomization.yaml"
UNPINNED = "PIN_ME"
OWNER_PLACEHOLDER = "CHANGE_ME"
OWN_IMAGES = ("rag-app", "platform-tools")  # publiées et figées par la CI (job promote)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# MODEL_REPO puis MODEL_REVISION, sous forme de clé de ConfigMap ou de patch JSON6902.
REPO_RE = re.compile(r"MODEL_REPO(?::[ \t]*|\n[ \t]*value:[ \t]*)(?P<repo>[^\s#]+)")
REV_RE = re.compile(r"MODEL_REVISION(?::[ \t]*|\n[ \t]*value:[ \t]*)(?P<rev>[^\s#]+)")


# --- Modèles ---------------------------------------------------------------------------------


def find_model_pins(text: str) -> list[tuple[str, str, int, int]]:
    """(repo, révision, début, fin) de chaque révision, associée au MODEL_REPO qui la précède."""
    found = []
    for repo_match in REPO_RE.finditer(text):
        rev_match = REV_RE.search(text, repo_match.end())
        if rev_match:
            found.append((repo_match["repo"], rev_match["rev"], rev_match.start("rev"), rev_match.end("rev")))
    return found


def replace_revisions(text: str, resolve, update: bool = False) -> tuple[str, list[str]]:
    changes = []
    for repo, rev, start, end in reversed(find_model_pins(text)):
        if repo.startswith(OWNER_PLACEHOLDER) or (SHA_RE.match(rev) and not update):
            continue
        new = resolve(repo)
        if new != rev:
            text = text[:start] + new + text[end:]
            changes.append(f"{repo}: {rev} -> {new}")
    return text, changes


def model_files() -> list[Path]:
    return sorted(p for p in DEPLOY.rglob("*.yaml") if "MODEL_REPO" in p.read_text(encoding="utf-8"))


def hf_resolve(repo: str) -> str:
    from huggingface_hub import HfApi

    return HfApi().model_info(repo).sha


def cmd_models(update: bool) -> int:
    for path in model_files():
        text, changes = replace_revisions(path.read_text(encoding="utf-8"), hf_resolve, update)
        if changes:
            path.write_text(text, encoding="utf-8")
            for change in changes:
                print(f"{path.relative_to(ROOT)}: {change}")
    return 0


# --- Images ------------------------------------------------------------------------------------

HEADER_END = "apiVersion:"


def load_pins() -> tuple[str, dict]:
    raw = PINS.read_text(encoding="utf-8")
    header = raw[: raw.index(HEADER_END)]
    return header, yaml.safe_load(raw)


def save_pins(header: str, doc: dict) -> None:
    PINS.write_text(header + yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def image_ref(entry: dict) -> str:
    return f"{entry.get('newName', entry['name'])}:{entry['newTag']}"


def crane_digest(ref: str) -> str:
    out = subprocess.run(["crane", "digest", ref], check=True, capture_output=True, text=True)
    return out.stdout.strip()


def cmd_images(update: bool, resolve=crane_digest) -> int:
    header, doc = load_pins()
    for entry in doc["images"]:
        if entry["name"] in OWN_IMAGES:
            continue  # digest inscrit par la CI après publication (pin.py set-image)
        if entry.get("digest") and not update:
            continue
        digest = resolve(image_ref(entry))
        if entry.get("digest") != digest:
            entry["digest"] = digest
            print(f"{image_ref(entry)} -> {digest}")
    save_pins(header, doc)
    return 0


def cmd_owner(owner: str) -> int:
    if not re.fullmatch(r"[A-Za-z0-9-]+", owner):
        raise SystemExit("nom de compte GitHub invalide")
    PINS.write_text(PINS.read_text(encoding="utf-8").replace(OWNER_PLACEHOLDER, owner.lower()), encoding="utf-8")
    print(f"images publiées sous ghcr.io/{owner.lower()}/sovereign-llm-platform/")
    return 0


def cmd_set_image(name: str, digest: str) -> int:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise SystemExit(f"digest invalide : {digest}")
    header, doc = load_pins()
    entry = next((e for e in doc["images"] if e["name"] == name), None)
    if entry is None:
        raise SystemExit(f"image inconnue : {name}")
    entry["digest"] = digest
    save_pins(header, doc)
    print(f"{name} -> {digest}")
    return 0


# --- Contrôle ----------------------------------------------------------------------------------


def unpinned() -> list[str]:
    problems = []
    for path in model_files():
        for repo, rev, _, _ in find_model_pins(path.read_text(encoding="utf-8")):
            if repo.startswith(OWNER_PLACEHOLDER):
                continue  # composant optionnel à compléter avant usage (ex. model-mistral-7b-awq)
            if not SHA_RE.match(rev):
                problems.append(f"modèle non figé : {repo}@{rev} ({path.relative_to(ROOT)})")
    _, doc = load_pins()
    for entry in doc["images"]:
        if OWNER_PLACEHOLDER in entry.get("newName", ""):
            problems.append(f"propriétaire d'image non renseigné : {entry['name']} (pin.py owner)")
        elif not entry.get("digest"):
            hint = " (publiée par la CI au prochain push sur main)" if entry["name"] in OWN_IMAGES else ""
            problems.append(f"image sans digest : {image_ref(entry)}{hint}")
    return problems


def cmd_check() -> int:
    problems = unpinned()
    for p in problems:
        print(f"- {p}")
    print("tout est figé" if not problems else f"{len(problems)} élément(s) non figé(s) : make pin")
    return 1 if problems else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    for name in ("models", "images"):
        p = sub.add_parser(name)
        p.add_argument("--update", action="store_true")
    p = sub.add_parser("owner")
    p.add_argument("owner")
    p = sub.add_parser("set-image")
    p.add_argument("name")
    p.add_argument("digest")
    args = parser.parse_args(argv)
    if args.cmd == "check":
        return cmd_check()
    if args.cmd == "models":
        return cmd_models(args.update)
    if args.cmd == "images":
        return cmd_images(args.update)
    if args.cmd == "owner":
        return cmd_owner(args.owner)
    return cmd_set_image(args.name, args.digest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
