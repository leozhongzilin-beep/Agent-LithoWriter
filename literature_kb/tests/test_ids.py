"""Unit tests for identifier generation (kb/ids.py)."""

from __future__ import annotations

from kb.ids import (
    dedupe_citation_key,
    format_paper_id,
    format_sub_id,
    hash_bytes,
    hash_file,
    make_citation_key,
)


def test_format_paper_id():
    assert format_paper_id("ILT", 2024, 31) == "ILT_2024_031"
    assert format_paper_id("ilt", 2023, 7) == "ILT_2023_007"
    assert format_paper_id("SMO", 2025, 1000) == "SMO_2025_1000"


def test_format_sub_id():
    assert format_sub_id("ILT_2024_031", "evidence", 7) == "ILT_2024_031.ev007"
    assert format_sub_id("ILT_2024_031", "formula", 3) == "ILT_2024_031.fm003"
    assert format_sub_id("ILT_2024_031", "metric", 12) == "ILT_2024_031.mt012"


def test_format_sub_id_unknown_kind():
    import pytest
    with pytest.raises(ValueError):
        format_sub_id("ILT_2024_031", "bogus", 1)


def test_citation_key_basic():
    key = make_citation_key(
        "Deep Learning for Inverse Lithography", 2024, "Zhang, Wei"
    )
    assert key == "Zhang2024DeepLearning"

    # two significant words are taken; stopwords ("is") are dropped
    key2 = make_citation_key(
        "Attention Is All You Need", 2017, "Vaswani, Ashish"
    )
    assert key2 == "Vaswani2017AttentionAll"


def test_citation_key_stopwords_and_empty():
    # leading stopword skipped; empty author -> no author prefix
    key = make_citation_key("The Transformer for ILT", 2024, "")
    assert key == "2024TransformerILT"
    assert "The" not in key


def test_citation_key_paper_fallback():
    key = make_citation_key("", None, "")
    assert key == "Paper"


def test_dedupe_citation_key():
    existing = {"Zhang2024DeepLearning", "Zhang2024DeepLearning_a"}
    assert dedupe_citation_key(existing, "Zhang2024DeepLearning") == "Zhang2024DeepLearning_b"
    assert dedupe_citation_key(set(), "Zhang2024DeepLearning") == "Zhang2024DeepLearning"


def test_hash_bytes_and_file(tmp_path):
    h = hash_bytes(b"hello")
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello")
    assert hash_file(p) == h
    assert hash_file(tmp_path / "missing.bin") is None
