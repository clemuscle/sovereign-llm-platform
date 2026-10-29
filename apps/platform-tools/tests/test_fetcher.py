from pathlib import Path

import pytest

from fetcher import (
    MARKER,
    Config,
    fetch,
    files_from_oci_manifest,
    is_up_to_date,
    matches,
    oci_reference,
    stale_files,
)

SHA = "a" * 40
SHA2 = "b" * 40


def cfg(tmp_path: Path, **kw) -> Config:
    base = dict(source="hf", repo="org/Model-7B", revision=SHA, model_dir=tmp_path / "m")
    return Config(**{**base, **kw})


# --- validation ------------------------------------------------------------------------------


def test_branch_revision_is_refused(tmp_path):
    with pytest.raises(ValueError, match="SHA"):
        cfg(tmp_path, revision="main").validate()


def test_unpinned_allowed_explicitly(tmp_path):
    cfg(tmp_path, revision="main", allow_unpinned=True).validate()


def test_oci_requires_registry(tmp_path):
    with pytest.raises(ValueError, match="OCI_REGISTRY"):
        cfg(tmp_path, source="oci").validate()


def test_from_env_parses_patterns():
    c = Config.from_env({"MODEL_REPO": "a/b", "MODEL_REVISION": SHA, "ALLOW_PATTERNS": "*.json, *.gguf ,"})
    assert c.allow_patterns == ["*.json", "*.gguf"]


# --- fonctions pures ---------------------------------------------------------------------------


def test_pattern_matching():
    assert matches("consolidated.safetensors", ["*.safetensors"])
    assert not matches("model-00001.safetensors", ["consolidated.*"])
    assert matches("anything", [])


def test_oci_reference_tag_and_digest(tmp_path):
    c = cfg(tmp_path, source="oci", oci_registry="zot:5000/models")
    assert oci_reference(c) == f"zot:5000/models/org/model-7b:{SHA}"
    c2 = cfg(tmp_path, source="oci", oci_registry="zot:5000/models", model_digest="sha256:abc")
    assert oci_reference(c2) == "zot:5000/models/org/model-7b@sha256:abc"


def test_stale_files_keeps_tool_metadata():
    existing = ["old.bin", "keep.json", ".cache/huggingface/x.lock", MARKER]
    assert stale_files(existing, ["keep.json"]) == ["old.bin"]


def test_marker_must_match_revision_and_files(tmp_path):
    c = cfg(tmp_path)
    marker = {"source": "hf", "repo": "org/Model-7B", "revision": SHA, "files": ["a", "b"]}
    assert is_up_to_date(marker, c, {"a", "b", MARKER})
    assert not is_up_to_date(marker, c, {"a"})  # fichier supprimé à la main
    assert not is_up_to_date({**marker, "revision": SHA2}, c, {"a", "b"})
    assert not is_up_to_date(None, c, {"a", "b"})


def test_files_from_oci_manifest():
    manifest = {"layers": [{"annotations": {"org.opencontainers.image.title": "params.json"}}]}
    assert files_from_oci_manifest(manifest) == ["params.json"]


# --- flux complet avec E/S simulées -------------------------------------------------------------


def fake_source(files: dict[str, str]):
    calls = {"download": 0}

    def expected(_cfg):
        return sorted(files)

    def download(_cfg, target: Path):
        calls["download"] += 1
        for name, content in files.items():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            (target / name).write_text(content)

    return expected, download, calls


def test_fetch_is_idempotent(tmp_path):
    c = cfg(tmp_path)
    expected, download, calls = fake_source({"params.json": "{}", "weights.bin": "w"})
    assert fetch(c, expected, download) == "fetched"
    assert fetch(c, expected, download) == "skipped"
    assert calls["download"] == 1


def test_new_revision_removes_old_files(tmp_path):
    exp1, dl1, _ = fake_source({"params.json": "{}", "old-weights.bin": "w1"})
    fetch(cfg(tmp_path), exp1, dl1)
    exp2, dl2, _ = fake_source({"params.json": "{}", "new-weights.bin": "w2"})
    assert fetch(cfg(tmp_path, revision=SHA2), exp2, dl2) == "fetched"
    files = {p.name for p in (tmp_path / "m").iterdir()}
    assert "old-weights.bin" not in files and "new-weights.bin" in files


def test_interrupted_download_is_retried(tmp_path):
    c = cfg(tmp_path)
    expected, _, _ = fake_source({"a.bin": "x", "b.bin": "y"})

    def partial(_cfg, target: Path):
        (target / "a.bin").write_text("x")  # b.bin manquant : coupure réseau simulée

    with pytest.raises(RuntimeError, match="manquants"):
        fetch(c, expected, partial)
    assert not (tmp_path / "m" / MARKER).exists()  # pas de témoin : on reprendra au redémarrage

    _, full, calls = fake_source({"a.bin": "x", "b.bin": "y"})
    assert fetch(c, expected, full) == "fetched"
    assert calls["download"] == 1


def test_empty_selection_is_an_error(tmp_path):
    with pytest.raises(RuntimeError, match="aucun fichier"):
        fetch(cfg(tmp_path), lambda _c: [], lambda _c, _t: None)


def test_digest_change_triggers_refetch(tmp_path):
    expected, download, calls = fake_source({"a.bin": "x"})
    fetch(cfg(tmp_path, model_digest="sha256:111"), expected, download)
    assert fetch(cfg(tmp_path, model_digest="sha256:222"), expected, download) == "fetched"
    assert calls["download"] == 2
