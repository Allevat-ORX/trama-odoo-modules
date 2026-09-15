"""Tests for shared recruitment LiteLLM configuration."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1]
    / "onrentx_recruitment_booking"
    / "llm_config.py"
)
SPEC = importlib.util.spec_from_file_location("recruitment_llm_config", MODULE_PATH)
assert SPEC and SPEC.loader
llm_config = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = llm_config
SPEC.loader.exec_module(llm_config)


class FakeParameters:
    def __init__(self, values=None):
        self.values = values or {}

    def sudo(self):
        return self

    def get_param(self, key, default=None):
        return self.values.get(key, default)


class FakeEnvironment:
    def __init__(self, values=None):
        self.parameters = FakeParameters(values)

    def __getitem__(self, key):
        assert key == "ir.config_parameter"
        return self.parameters


def test_defaults_to_live_verified_model_without_credentials():
    config = llm_config.get_litellm_config(FakeEnvironment())

    assert config.model == "bedrock-mistral"
    assert config.url == llm_config.DEFAULT_LITELLM_URL
    assert config.api_key == ""


def test_operator_model_override_is_preserved():
    config = llm_config.get_litellm_config(
        FakeEnvironment({"onrentx.recruitment.litellm_model": "bedrock-deepseek-v3"})
    )

    assert config.model == "bedrock-deepseek-v3"


def test_empty_model_parameter_uses_safe_default():
    config = llm_config.get_litellm_config(
        FakeEnvironment({"onrentx.recruitment.litellm_model": ""})
    )

    assert config.model == "bedrock-mistral"


def test_all_recruitment_generation_paths_use_shared_config():
    root = MODULE_PATH.parents[0]
    paths = (
        root / "models" / "wa_chatbot.py",
        root / "models" / "survey_auto_trigger.py",
        root / "models" / "resource_booking.py",
        root / "controllers" / "interview_webhooks.py",
    )

    for path in paths:
        source = path.read_text()
        assert "get_litellm_config" in source
        assert '"model": "groq-llama"' not in source
        assert '"model": "mistral-large"' not in source
