"""Tests for tex.sanitize_citations — deterministic citation-key enforcement.

Root cause B: the writing LLM fabricates ``\\cite{...}`` keys that are not in
the allowed set. ``sanitize_citations`` is the deterministic post-pass that
guarantees the body only ever references allowed keys.
"""
from __future__ import annotations

from write_agent.tex import sanitize_citations


def test_all_allowed_keys_unchanged():
    body = r"prior work \cite{vaswani2017attention} shows"
    out, dropped = sanitize_citations(body, {"vaswani2017attention"})
    assert out == body
    assert dropped == []


def test_unknown_key_dropped_and_reported():
    out, dropped = sanitize_citations(r"\cite{liu2024kan}", {"alhusseiny2025kan"})
    assert out == ""
    assert dropped == ["liu2024kan"]


def test_mixed_keys_keep_only_allowed():
    out, dropped = sanitize_citations(
        r"\cite{good,bad}", {"good"}
    )
    assert out == r"\cite{good}"
    assert dropped == ["bad"]


def test_command_removed_when_no_key_allowed():
    out, dropped = sanitize_citations(r"\cite{bad}", set())
    assert out == ""
    assert dropped == ["bad"]


def test_citep_and_citet_prefixes_enforced():
    out, dropped = sanitize_citations(
        r"\citep{bad} and \citet{good}", {"good"}
    )
    assert r"\citep{bad}" not in out
    assert r"\citet{good}" in out
    assert dropped == ["bad"]


def test_no_citations_unchanged():
    body = "A sentence with no citation at all."
    out, dropped = sanitize_citations(body, {"anything"})
    assert out == body
    assert dropped == []


def test_multiple_unknown_keys_all_reported():
    out, dropped = sanitize_citations(
        r"\cite{liu2024kan,li2024kan}", set()
    )
    assert out == ""
    assert dropped == ["liu2024kan", "li2024kan"]
