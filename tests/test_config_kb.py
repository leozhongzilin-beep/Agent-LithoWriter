"""Config tests for the KB integration keys."""
from __future__ import annotations

from write_agent.config import load_config


def test_kb_path_default_none():
    cfg = load_config()
    assert cfg.kb_path is None


def test_kb_path_parses():
    cfg = load_config()
    cfg.data["write"]["kb_path"] = "C:/kb/data"
    assert cfg.kb_path == "C:/kb/data"


def test_kb_path_empty_string_is_none():
    cfg = load_config()
    cfg.data["write"]["kb_path"] = ""
    assert cfg.kb_path is None


def test_kb_discovery_default_is_5():
    cfg = load_config()
    assert cfg.kb_discovery_per_category == 5


def test_kb_discovery_override():
    cfg = load_config()
    cfg.data["write"]["kb_discovery_per_category"] = 8
    assert cfg.kb_discovery_per_category == 8


def test_kb_path_env_override(monkeypatch):
    monkeypatch.setenv("WRITING_AGENT_KB_PATH", "D:/kb/data")
    cfg = load_config()
    assert cfg.kb_path == "D:/kb/data"
