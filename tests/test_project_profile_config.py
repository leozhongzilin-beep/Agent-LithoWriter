from __future__ import annotations

from pathlib import Path

from write_agent.config import Config, load_config


def test_workspace_and_project_profile_are_exposed_as_paths():
    cfg = Config(data={
        "experiments": {
            "project_profile": "D:/team/profiles/member.yaml",
            "workspace_root": "D:/team/member-method",
            "lithobench_root": "D:/legacy/lithobench",
        }
    })

    assert cfg.auto_project_profile == Path("D:/team/profiles/member.yaml")
    assert cfg.auto_workspace_root == Path("D:/team/member-method")


def test_legacy_lithobench_root_remains_workspace_fallback():
    cfg = Config(data={
        "experiments": {
            "project_profile": None,
            "workspace_root": None,
            "lithobench_root": "D:/legacy/lithobench",
        }
    })

    assert cfg.auto_workspace_root == Path("D:/legacy/lithobench")


def test_project_profile_environment_overrides(monkeypatch):
    monkeypatch.setenv("WRITING_AGENT_PROJECT_PROFILE", "D:/profiles/method.yaml")
    monkeypatch.setenv("WRITING_AGENT_WORKSPACE_ROOT", "D:/workspaces/method")

    cfg = load_config()

    assert cfg.auto_project_profile == Path("D:/profiles/method.yaml")
    assert cfg.auto_workspace_root == Path("D:/workspaces/method")
