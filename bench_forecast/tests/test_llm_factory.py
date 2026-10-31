"""Tests for the LLM provider factory (Groq / DeepSeek / OpenAI-compatible / Ollama)."""

import pytest

from src.core.config import Config
from src.core.llm import LLMFactory


def test_deepseek_client_uses_openai_compatible_endpoint(monkeypatch):
    monkeypatch.setattr(Config, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(Config, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(Config, "DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setattr(Config, "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

    llm = LLMFactory.get_chat_model()

    assert type(llm).__name__ == "ChatOpenAI"
    assert llm.model_name == "deepseek-chat"
    assert llm.openai_api_base == "https://api.deepseek.com/v1"
    assert llm.temperature == Config.LLM_TEMPERATURE


def test_deepseek_reasoner_omits_temperature(monkeypatch):
    monkeypatch.setattr(Config, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(Config, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(Config, "DEEPSEEK_MODEL", "deepseek-reasoner")
    monkeypatch.setattr(Config, "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

    llm = LLMFactory.get_chat_model()

    assert llm.model_name == "deepseek-reasoner"
    assert llm.temperature is None


def test_deepseek_requires_api_key(monkeypatch):
    monkeypatch.setattr(Config, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(Config, "DEEPSEEK_API_KEY", "")

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        LLMFactory.get_chat_model()
