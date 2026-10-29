"""model-fetcher : place les poids d'un modèle sur un volume, de façon idempotente.

Lancé en initContainer devant chaque serveur de modèle (vLLM, llama.cpp, TEI). Le serveur
lui-même tourne ensuite hors ligne, sur un dossier local : il n'a jamais besoin d'Internet.

Deux sources, même résultat :
  SOURCE=hf   mode connecté : Hugging Face, à une révision (SHA de commit) figée
  SOURCE=oci  mode air-gapped : artefact OCI dans la registry interne (Zot), poussé au
              préalable par `fetcher.py stage` depuis une machine connectée

Garanties :
  - révision obligatoirement figée (SHA de 40 caractères) sauf ALLOW_UNPINNED=true ;
  - idempotent : un fichier témoin (.fetched.json) évite tout re-téléchargement ;
  - pas de mélange de versions : les fichiers d'une ancienne révision sont supprimés ;
  - le témoin est écrit en dernier : un téléchargement interrompu est repris au redémarrage.

Usage :
  python fetcher.py              # fetch (variables d'environnement, voir Config)
  python fetcher.py stage        # hf -> registry OCI (préparation du mode air-gapped)
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("model-fetcher")

MARKER = ".fetched.json"
ARTIFACT_TYPE = "application/vnd.sovereign-llm.model.v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# Dossiers techniques laissés par les outils de téléchargement : jamais considérés comme obsolètes.
IGNORED_PREFIXES = (".cache/", MARKER)


@dataclass(frozen=True)
class Config:
    source: str
    repo: str
    revision: str
    model_dir: Path
    allow_patterns: list[str] = field(default_factory=list)
    oci_registry: str = ""
    oci_plain_http: bool = False
    model_digest: str = ""
    allow_unpinned: bool = False

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        env = dict(os.environ if env is None else env)
        patterns = [p.strip() for p in env.get("ALLOW_PATTERNS", "").split(",") if p.strip()]
        return cls(
            source=env.get("SOURCE", "hf"),
            repo=env["MODEL_REPO"],
            revision=env["MODEL_REVISION"],
            model_dir=Path(env.get("MODEL_DIR", "/models/llm")),
            allow_patterns=patterns,
            oci_registry=env.get("OCI_REGISTRY", "").rstrip("/"),
            oci_plain_http=env.get("OCI_PLAIN_HTTP", "false").lower() == "true",
            model_digest=env.get("MODEL_DIGEST", ""),
            allow_unpinned=env.get("ALLOW_UNPINNED", "false").lower() == "true",
        )

    def validate(self) -> None:
        if self.source not in ("hf", "oci"):
            raise ValueError(f"SOURCE inconnue : {self.source!r} (attendu : hf ou oci)")
        if not self.allow_unpinned and not SHA_RE.match(self.revision):
            raise ValueError(
                f"MODEL_REVISION={self.revision!r} n'est pas un SHA de commit : une branche comme 'main' "
                "peut changer sans prévenir. Fige la révision (make pin) ou ALLOW_UNPINNED=true."
            )
        if self.source == "oci" and not self.oci_registry:
            raise ValueError("SOURCE=oci exige OCI_REGISTRY (ex. zot.model-store.svc:5000/models)")


# --- Fonctions pures (testées unitairement) ----------------------------------------------


def matches(path: str, patterns: list[str]) -> bool:
    return not patterns or any(fnmatch.fnmatch(path, p) for p in patterns)


def oci_reference(cfg: Config) -> str:
    """Référence OCI d'un modèle : <registry>/<org>/<nom>:<révision> (ou @digest si figé)."""
    name = cfg.repo.lower()
    base = f"{cfg.oci_registry}/{name}"
    return f"{base}@{cfg.model_digest}" if cfg.model_digest else f"{base}:{cfg.revision}"


def stale_files(existing: list[str], expected: list[str]) -> list[str]:
    """Fichiers présents sur le volume qui n'appartiennent pas à la révision attendue."""
    keep = set(expected)
    return sorted(f for f in existing if f not in keep and not f.startswith(IGNORED_PREFIXES))


def is_up_to_date(marker: dict | None, cfg: Config, present: set[str]) -> bool:
    if not marker:
        return False
    same = (marker.get("source"), marker.get("repo"), marker.get("revision"), marker.get("digest", "")) == (
        cfg.source,
        cfg.repo,
        cfg.revision,
        cfg.model_digest,
    )
    return same and set(marker.get("files", [])) <= present


def files_from_oci_manifest(manifest: dict) -> list[str]:
    return [layer["annotations"]["org.opencontainers.image.title"] for layer in manifest.get("layers", [])]


# --- Entrées / sorties --------------------------------------------------------------------


def list_local_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def read_marker(root: Path) -> dict | None:
    try:
        return json.loads((root / MARKER).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_marker(root: Path, cfg: Config, files: list[str]) -> None:
    payload = {
        "source": cfg.source,
        "repo": cfg.repo,
        "revision": cfg.revision,
        "digest": cfg.model_digest,
        "files": sorted(files),
    }
    tmp = root / f"{MARKER}.tmp"
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(root / MARKER)  # écriture atomique


def hf_expected_files(cfg: Config) -> list[str]:
    from huggingface_hub import HfApi

    files = HfApi().list_repo_files(cfg.repo, revision=cfg.revision)
    return sorted(f for f in files if matches(f, cfg.allow_patterns))


def hf_download(cfg: Config, target: Path) -> None:
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=cfg.repo,
        revision=cfg.revision,
        local_dir=str(target),
        allow_patterns=cfg.allow_patterns or None,
    )


def oras(*args: str) -> str:
    result = subprocess.run(["oras", *args], capture_output=True, text=True)  # noqa: S603, S607
    if result.returncode != 0:
        raise RuntimeError(f"oras {args[0]} a échoué : {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def oci_expected_files(cfg: Config) -> list[str]:
    flags = ["--plain-http"] if cfg.oci_plain_http else []
    manifest = json.loads(oras("manifest", "fetch", *flags, oci_reference(cfg)))
    return sorted(files_from_oci_manifest(manifest))


def oci_download(cfg: Config, target: Path) -> None:
    flags = ["--plain-http"] if cfg.oci_plain_http else []
    oras("pull", *flags, "-o", str(target), oci_reference(cfg))


# --- Commandes ------------------------------------------------------------------------------


def fetch(cfg: Config, expected_fn=None, download_fn=None) -> str:
    """Retourne 'skipped' ou 'fetched'. Les fonctions d'E/S sont injectables pour les tests."""
    cfg.validate()
    expected_fn = expected_fn or (hf_expected_files if cfg.source == "hf" else oci_expected_files)
    download_fn = download_fn or (hf_download if cfg.source == "hf" else oci_download)
    root = cfg.model_dir
    root.mkdir(parents=True, exist_ok=True)

    present = set(list_local_files(root))
    if is_up_to_date(read_marker(root), cfg, present):
        log.info("modèle %s@%s déjà présent : rien à faire", cfg.repo, cfg.revision[:12])
        return "skipped"

    expected = expected_fn(cfg)
    if not expected:
        raise RuntimeError(f"aucun fichier ne correspond à {cfg.allow_patterns} dans {cfg.repo}")

    (root / MARKER).unlink(missing_ok=True)  # invalide l'ancien témoin avant toute modification
    for stale in stale_files(sorted(present), expected):
        log.info("suppression du fichier obsolète %s", stale)
        (root / stale).unlink(missing_ok=True)

    log.info("téléchargement de %s@%s (%d fichiers) depuis %s", cfg.repo, cfg.revision[:12], len(expected), cfg.source)
    download_fn(cfg, root)

    missing = set(expected) - set(list_local_files(root))
    if missing:
        raise RuntimeError(f"fichiers manquants après téléchargement : {sorted(missing)}")
    write_marker(root, cfg, expected)
    log.info("modèle prêt dans %s", root)
    return "fetched"


def stage(cfg: Config) -> str:
    """Mode connecté -> air-gapped : télécharge depuis Hugging Face et pousse en artefact OCI."""
    hf_cfg = Config(**{**cfg.__dict__, "source": "hf"})
    hf_cfg.validate()
    if not cfg.oci_registry:
        raise ValueError("stage exige OCI_REGISTRY")
    with tempfile.TemporaryDirectory(prefix="stage-") as tmp:
        work = Path(tmp)
        hf_download(hf_cfg, work)
        files = [f for f in list_local_files(work) if not f.startswith(IGNORED_PREFIXES)]
        flags = ["--plain-http"] if cfg.oci_plain_http else []
        ref = oci_reference(Config(**{**cfg.__dict__, "model_digest": ""}))
        cwd = os.getcwd()
        try:
            os.chdir(work)  # oras enregistre des chemins relatifs comme noms de fichiers
            out = oras("push", *flags, "--artifact-type", ARTIFACT_TYPE, ref, *files)
        finally:
            os.chdir(cwd)
    digest = next((line.split()[-1] for line in out.splitlines() if line.startswith("Digest:")), "")
    print(f"artefact poussé : {ref}\ndigest : {digest}\n-> à reporter dans MODEL_DIGEST pour figer le contenu")
    return digest


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.from_env()
    command = argv[1] if len(argv) > 1 else "fetch"
    try:
        if command == "stage":
            stage(cfg)
        elif command == "fetch":
            fetch(cfg)
        else:
            raise ValueError(f"commande inconnue : {command}")
    except Exception as exc:  # message clair dans les logs de l'initContainer
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
