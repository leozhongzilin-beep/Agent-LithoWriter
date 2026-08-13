"""Tests for bibliographic metadata resolution (paper2kb/metadata.py)."""

from __future__ import annotations

from paper2kb.metadata import (
    BibRecord,
    crossref_item_to_record,
    merge_metadata,
    resolve_metadata,
)


def test_crossref_item_maps_to_record():
    item = {
        "title": ["Deep Learning for Inverse Lithography"],
        "author": [{"family": "Zhang", "given": "Wei"}],
        "issued": {"date-parts": [[2024, 5]]},
        "container-title": ["Optics and Lasers in Engineering"],
        "volume": "178",
        "page": "108000",
        "publisher": "Elsevier",
        "DOI": "10.1016/x",
        "URL": "https://doi.org/10.1016/x",
        "type": "journal-article",
    }
    rec = crossref_item_to_record(item)
    assert rec.title == "Deep Learning for Inverse Lithography"
    assert rec.authors == [{"family": "Zhang", "given": "Wei"}]
    assert rec.year == 2024
    assert rec.venue == "Optics and Lasers in Engineering"
    assert rec.article_type == "journal"
    assert rec.doi == "10.1016/x"


def test_merge_prefers_crossref_over_pdf_metadata():
    rec = BibRecord(title="Real Title", authors=[{"family": "Zhang", "given": "Wei"}],
                    year=2024, venue="IEEE TCAD", doi="10.1/x")
    pdf_meta = {"title": "PDF Title (wrong)", "author": "Someone Else"}
    merged = merge_metadata(rec, pdf_meta)
    assert merged["title"] == "Real Title"
    assert merged["doi"] == "10.1/x"
    assert merged["year"] == 2024
    assert merged["venue"] == "IEEE TCAD"
    assert merged["authors_summary"] == "Zhang"  # single author


def test_merge_fills_gaps_from_pdf_metadata():
    pdf_meta = {"title": "A Paper From A PDF", "author": "Li, Ming"}
    merged = merge_metadata(None, pdf_meta)
    assert merged["title"] == "A Paper From A PDF"
    assert merged["authors_summary"].startswith("Li")


def test_resolve_by_doi_uses_injected_http():
    item = {"title": ["T"], "issued": {"date-parts": [[2023]]},
            "DOI": "10.9999/t", "type": "proceedings-article"}

    def fake_http(url, timeout=20):
        assert "10.9999/t" in url
        return {"message": item}

    rec = resolve_metadata(doi="10.9999/t", http_get=fake_http)
    assert rec is not None and rec.year == 2023
    assert rec.article_type == "conf"


def test_resolve_metadata_none_when_unresolvable():
    assert resolve_metadata(doi="10.0/nope", http_get=lambda u, **k: None) is None
