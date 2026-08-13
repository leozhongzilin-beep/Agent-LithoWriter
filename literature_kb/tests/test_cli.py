"""Smoke tests for the kb CLI (write + retrieval commands)."""

from __future__ import annotations

import json

from kb.cli import main


def _init_and_add(tmp_path, make_package):
    assert main(["init", "--root", str(tmp_path)]) == 0
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(json.dumps(make_package()), encoding="utf-8")
    assert main(["add", str(pkg_file), "--root", str(tmp_path)]) == 0


def test_cli_init_and_add(tmp_path, make_package):
    _init_and_add(tmp_path, make_package)
    assert (tmp_path / "kb.db").exists()


def test_cli_search_discovery(tmp_path, make_package, capsys):
    _init_and_add(tmp_path, make_package)
    rc = main(["search", "lithography", "--root", str(tmp_path),
               "--intent", "DISCOVERY"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ILT_2024_001" in out
    assert "Zhang2024DeepLearning" in out


def test_cli_search_with_filters(tmp_path, make_package, capsys):
    _init_and_add(tmp_path, make_package)
    rc = main(["search", "lithography", "--root", str(tmp_path),
               "--filter", "domain=SMO"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "(no results)" in out  # ILT paper filtered out by domain=SMO


def test_cli_verify(tmp_path, make_package, capsys):
    _init_and_add(tmp_path, make_package)
    main(["verify", "The method reduces turnaround time", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "supported" in out
    assert "strength: B" in out


def test_cli_metrics(tmp_path, make_package, capsys):
    _init_and_add(tmp_path, make_package)
    main(["metrics", "ILT_2024_001", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "EPE=2.1" in out
    assert "status=reported" in out


def test_cli_cite(tmp_path, make_package, capsys):
    _init_and_add(tmp_path, make_package)
    main(["cite", "ILT_2024_001", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "Zhang" in out
    assert "generated:" in out


def test_cli_chunk_and_chunks(tmp_path, make_package, capsys):
    _init_and_add(tmp_path, make_package)
    src = tmp_path / "paper.md"
    src.write_text("# T\n\nIntro.\n\n## Method\n\nPara.\n", encoding="utf-8")
    rc = main(["chunk", "ILT_2024_001", "--root", str(tmp_path),
               "--source", str(src)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "2 chunks" in out

    main(["chunks", "ILT_2024_001", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "[Preamble #1]" in out
    assert "Intro." in out


class _FakeEmbedder:
    model_name = "fake"

    def embed(self, texts):
        import numpy as np
        return np.zeros((len(texts), 4), dtype=np.float32)


def test_cli_embed(tmp_path, make_package, capsys, monkeypatch):
    monkeypatch.setattr("kb.embedder.get_embedder", lambda *a, **k: _FakeEmbedder())
    _init_and_add(tmp_path, make_package)
    rc = main(["embed", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "embedded 3 objects" in out  # paper + evidence + formula


def test_cli_init_seeds_styles(tmp_path, capsys):
    main(["init", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "author-date" in out


def test_cli_add_embed(tmp_path, make_package, capsys, monkeypatch):
    monkeypatch.setattr("kb.embedder.get_embedder", lambda *a, **k: _FakeEmbedder())
    main(["init", "--root", str(tmp_path)])
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(json.dumps(make_package()), encoding="utf-8")
    rc = main(["add", str(pkg_file), "--root", str(tmp_path), "--embed"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[INSERTED]" in out
    assert "embedded" in out  # import-time embed hook ran


def test_cli_add_embed_degrades_gracefully(tmp_path, make_package, capsys):
    """No sentence-transformers -> import still succeeds, embed is skipped."""
    main(["init", "--root", str(tmp_path)])
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(json.dumps(make_package()), encoding="utf-8")
    rc = main(["add", str(pkg_file), "--root", str(tmp_path), "--embed"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[INSERTED]" in out
    assert "embedding skipped" in out


def test_cli_seed_ontology(tmp_path, capsys):
    main(["init", "--root", str(tmp_path)])
    rc = main(["seed-ontology", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "seeded ontology" in out
    assert "concepts=" in out


def test_cli_bibliography(tmp_path, make_package, capsys):
    pytest_importorskip = __import__("pytest").importorskip
    pytest_importorskip("citeproc")
    _init_and_add(tmp_path, make_package)
    main(["bibliography", "author-date", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "[1]" in out
    assert "Zhang" in out and "2024" in out
