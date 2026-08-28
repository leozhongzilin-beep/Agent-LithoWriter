"""Configuration loading for the writing agent.

Precedence (highest wins):
    1. CLI flags (handled in cli.py)
    2. Environment variables (WRITING_AGENT_*)
    3. config.yaml in the project root
    4. Defaults baked into this module
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base."""
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_yaml(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Config:
    """Flat configuration object. Nested YAML sections are flattened."""

    data: dict[str, Any] = field(default_factory=dict)

    # --- helpers ---
    def get(self, *path: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    # --- typed accessors ---
    @property
    def model_name(self) -> str:
        return self.get("model", "name", default="deepseek-chat")

    @property
    def base_url(self) -> str:
        return self.get("model", "base_url", default="https://api.deepseek.com/v1")

    @property
    def temperature(self) -> float:
        return float(self.get("model", "temperature", default=0.7))

    @property
    def max_tokens(self) -> int:
        return int(self.get("model", "max_tokens", default=8192))

    @property
    def api_key(self) -> str:
        return os.environ.get("DEEPSEEK_API_KEY", "") or self.get("model", "api_key", default="")

    @property
    def venue(self) -> str:
        return self.get("pipeline", "venue", default="ICLR")

    @property
    def max_pages(self) -> int:
        return int(self.get("pipeline", "max_pages", default=9))

    @property
    def anonymous(self) -> bool:
        return bool(self.get("pipeline", "anonymous", default=True))

    @property
    def output_dir(self) -> Path:
        return Path(self.get("pipeline", "output_dir", default="output"))

    @property
    def language(self) -> str:
        return self.get("pipeline", "language", default="english")

    @property
    def review_max_rounds(self) -> int:
        return int(self.get("review", "max_rounds", default=3))

    @property
    def review_min_score(self) -> float:
        return float(self.get("review", "min_score", default=6.0))

    @property
    def acceptable_verdicts(self):
        return list(self.get("review", "acceptable_verdicts", default=["ready", "almost"]))

    @property
    def reviewer_independence(self) -> bool:
        return bool(self.get("review", "reviewer_independence", default=True))

    @property
    def human_checkpoint(self) -> bool:
        return bool(self.get("review", "human_checkpoint", default=False))

    @property
    def dblp_verify(self) -> bool:
        return bool(self.get("write", "dblp_verify", default=True))

    @property
    def kb_path(self) -> str | None:
        p = self.get("write", "kb_path", default=None)
        return p if isinstance(p, str) and p else None

    @property
    def kb_discovery_per_category(self) -> int:
        return int(self.get("write", "kb_discovery_per_category", default=5))


def load_config(
    yaml_path: Path | None = None,
    env_prefix: str = "WRITING_AGENT_",
) -> Config:
    """Load config from defaults + config.yaml + environment overrides.

    Environment variable mapping:
        WRITING_AGENT_MODEL_NAME        -> model.name
        WRITING_AGENT_BASE_URL          -> model.base_url
        WRITING_AGENT_TEMPERATURE       -> model.temperature
        WRITING_AGENT_MAX_TOKENS        -> model.max_tokens
        WRITING_AGENT_VENUE             -> pipeline.venue
        WRITING_AGENT_MAX_PAGES         -> pipeline.max_pages
        WRITING_AGENT_MAX_ROUNDS        -> review.max_rounds
        WRITING_AGENT_MIN_SCORE         -> review.min_score
        WRITING_AGENT_OUTPUT_DIR        -> pipeline.output_dir
    """
    # defaults
    data: dict[str, Any] = {
        "model": {
            "name": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "temperature": 0.7,
            "max_tokens": 8192,
        },
        "pipeline": {
            "venue": "ICLR",
            "max_pages": 9,
            "anonymous": True,
            "output_dir": "output",
            "language": "english",
        },
        "plan": {"max_sections": 8, "min_sections": 5, "num_contribution_bullets": 4},
        "write": {
            "abstract_words_min": 150,
            "abstract_words_max": 250,
            "related_work_min_pages": 1.0,
            "dblp_verify": True,
        },
        "review": {
            "max_rounds": 3,
            "min_score": 6.0,
            "acceptable_verdicts": ["ready", "almost"],
            "reviewer_independence": True,
            "human_checkpoint": False,
        },
        "citation": {
            "dblp_endpoint": "https://dblp.org/search/publ/api",
            "dblp_bib_base": "https://dblp.org/rec",
            "crossref_base": "https://doi.org",
            "timeout_seconds": 20,
        },
    }

    # config.yaml overrides
    if yaml_path is None:
        # look in project root (two levels up from this file: write_agent/config.py -> writing-agent/)
        yaml_path = Path(__file__).resolve().parent.parent / "config.yaml"
    data = _deep_merge(data, load_yaml(yaml_path))

    # environment overrides (flat mapping)
    env_map = {
        "MODEL_NAME": ("model", "name"),
        "BASE_URL": ("model", "base_url"),
        "TEMPERATURE": ("model", "temperature"),
        "MAX_TOKENS": ("model", "max_tokens"),
        "VENUE": ("pipeline", "venue"),
        "MAX_PAGES": ("pipeline", "max_pages"),
        "MAX_ROUNDS": ("review", "max_rounds"),
        "MIN_SCORE": ("review", "min_score"),
        "OUTPUT_DIR": ("pipeline", "output_dir"),
        "ANONYMOUS": ("pipeline", "anonymous"),
        "KB_PATH": ("write", "kb_path"),
    }
    for suffix, path in env_map.items():
        val = os.environ.get(f"{env_prefix}{suffix}")
        if val is None:
            continue
        node = data
        for key in path[:-1]:
            node = node.setdefault(key, {})
        if suffix in ("TEMPERATURE", "MIN_SCORE"):
            node[path[-1]] = float(val)
        elif suffix in ("MAX_TOKENS", "MAX_PAGES", "MAX_ROUNDS"):
            node[path[-1]] = int(val)
        elif suffix == "ANONYMOUS":
            node[path[-1]] = val.lower() in ("1", "true", "yes", "on")
        else:
            node[path[-1]] = val

    return Config(data=data)
