import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import pin

SHA = "c" * 40
CONFIGMAP = """data:
  MODEL_REPO: org/model-a
  MODEL_REVISION: PIN_ME # commentaire conservé
  OTHER: x
"""
PATCH = """      - op: replace
        path: /data/MODEL_REPO
        value: org/model-b
      - op: replace
        path: /data/MODEL_REVISION
        value: PIN_ME
"""


def test_finds_revision_in_configmap_and_patch():
    assert [(r, v) for r, v, _, _ in pin.find_model_pins(CONFIGMAP)] == [("org/model-a", "PIN_ME")]
    assert [(r, v) for r, v, _, _ in pin.find_model_pins(PATCH)] == [("org/model-b", "PIN_ME")]


def test_replace_keeps_the_rest_of_the_file():
    text, changes = pin.replace_revisions(CONFIGMAP, lambda repo: SHA)
    assert f"MODEL_REVISION: {SHA} # commentaire conservé" in text
    assert "OTHER: x" in text and changes == [f"org/model-a: PIN_ME -> {SHA}"]


def test_pinned_revision_is_left_alone_unless_update():
    pinned = CONFIGMAP.replace("PIN_ME", SHA)
    assert pin.replace_revisions(pinned, lambda r: "d" * 40) == (pinned, [])
    text, _ = pin.replace_revisions(pinned, lambda r: "d" * 40, update=True)
    assert "d" * 40 in text


def test_placeholder_repo_is_skipped():
    text = CONFIGMAP.replace("org/model-a", "CHANGE_ME/model")
    assert pin.replace_revisions(text, lambda r: SHA) == (text, [])


def test_image_ref():
    assert pin.image_ref({"name": "postgres", "newTag": "17-alpine"}) == "postgres:17-alpine"
    assert pin.image_ref({"name": "x", "newName": "ghcr.io/o/x", "newTag": "1"}) == "ghcr.io/o/x:1"


def test_repository_files_are_parseable():
    """Chaque fichier de modèle du dépôt doit être reconnu par le script."""
    files = pin.model_files()
    assert files, "aucun fichier de modèle trouvé"
    for path in files:
        assert pin.find_model_pins(path.read_text(encoding="utf-8")), path
