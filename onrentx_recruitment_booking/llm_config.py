"""Shared LiteLLM configuration for the recruitment module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_LITELLM_URL = "http://159.54.142.132:4000/v1/chat/completions"
DEFAULT_LITELLM_MODEL = "bedrock-mistral"


@dataclass(frozen=True)
class LiteLLMConfig:
    """Runtime-configured LiteLLM endpoint and model alias."""

    url: str
    api_key: str
    model: str


def get_litellm_config(env: Any) -> LiteLLMConfig:
    """Read the recruitment LLM settings from Odoo system parameters.

    The model remains operator-configurable, while the default is an alias
    verified in the production LiteLLM catalog. Credentials are never stored in
    this module or logged by callers.
    """
    parameters = env["ir.config_parameter"].sudo()
    model = parameters.get_param(
        "onrentx.recruitment.litellm_model",
        DEFAULT_LITELLM_MODEL,
    ) or DEFAULT_LITELLM_MODEL
    return LiteLLMConfig(
        url=parameters.get_param(
            "onrentx.recruitment.litellm_url",
            DEFAULT_LITELLM_URL,
        ),
        api_key=parameters.get_param("onrentx.recruitment.litellm_api_key", ""),
        model=model,
    )
