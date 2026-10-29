import pytest

from app.chunking import chunk_text


def test_short_text_is_one_chunk():
    assert chunk_text("Bonjour le monde.", size=100, overlap=10) == ["Bonjour le monde."]


def test_empty_text_gives_no_chunk():
    assert chunk_text("   \n\n  ", size=100, overlap=10) == []


def test_paragraphs_are_grouped_up_to_size():
    text = "\n\n".join(["a" * 40, "b" * 40, "c" * 40])
    chunks = chunk_text(text, size=100, overlap=0)
    assert len(chunks) == 2
    assert all(len(c) <= 100 for c in chunks)


def test_chunks_never_exceed_size():
    text = "\n\n".join(f"Paragraphe {i} " + "x" * (i * 37 % 300) for i in range(50))
    for chunk in chunk_text(text, size=200, overlap=40):
        assert len(chunk) <= 200


def test_long_paragraph_uses_sliding_windows_with_overlap():
    text = "".join(str(i % 10) for i in range(250))
    chunks = chunk_text(text, size=100, overlap=20)
    assert chunks[0][-20:] == chunks[1][:20]
    assert "".join(c[20:] if i else c for i, c in enumerate(chunks)).startswith(text[:200])


def test_overlap_carries_context_between_chunks():
    text = "\n\n".join(["premier " * 10, "second " * 10, "troisieme " * 10])
    chunks = chunk_text(text, size=120, overlap=30)
    assert len(chunks) >= 2
    assert chunks[0][-30:] in chunks[1]


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (100, 100), (100, -1)])
def test_invalid_parameters(size, overlap):
    with pytest.raises(ValueError):
        chunk_text("texte", size=size, overlap=overlap)
