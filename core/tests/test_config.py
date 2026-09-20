"""Tests for framework/config.py - Hive configuration loading."""

import logging

import pytest

from framework.config import (
    get_api_base,
    get_hive_config,
    get_llm_extra_kwargs,
    get_preferred_model,
    get_worker_llm_extra_kwargs,
)


class TestGetHiveConfig:
    """Test get_hive_config() logs warnings on parse errors."""

    def test_logs_warning_on_malformed_json(self, tmp_path, monkeypatch, caplog):
        """Test that malformed JSON logs warning and returns empty dict."""
        config_file = tmp_path / "configuration.json"
        config_file.write_text('{"broken": }')

        monkeypatch.setattr("framework.config.HIVE_CONFIG_FILE", config_file)

        with caplog.at_level(logging.WARNING):
            result = get_hive_config()

        assert result == {}
        assert "Failed to load Hive config" in caplog.text
        assert str(config_file) in caplog.text


class TestReasoningEffortConfig:
    """Explicit reasoning effort is forwarded without provider-specific rewriting."""

    def test_main_reasoning_effort_passthrough(self, tmp_path, monkeypatch):
        config_file = tmp_path / "configuration.json"
        config_file.write_text(
            '{"llm":{"provider":"openai","model":"gpt-test","reasoning_effort":"high"}}',
            encoding="utf-8",
        )
        monkeypatch.setattr("framework.config.HIVE_CONFIG_FILE", config_file)

        assert get_llm_extra_kwargs() == {"reasoning_effort": "high"}

    def test_main_reasoning_effort_merges_with_extra_body(self, tmp_path, monkeypatch):
        config_file = tmp_path / "configuration.json"
        config_file.write_text(
            '{"llm":{"provider":"openai","model":"gpt-test","reasoning_effort":" xhigh ",'
            '"extra_body":{"chat_template_kwargs":{"enable_thinking":false}}}}',
            encoding="utf-8",
        )
        monkeypatch.setattr("framework.config.HIVE_CONFIG_FILE", config_file)

        assert get_llm_extra_kwargs() == {
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            "reasoning_effort": "xhigh",
        }

    def test_worker_reasoning_effort_merges_with_ollama_context(self, tmp_path, monkeypatch):
        config_file = tmp_path / "configuration.json"
        config_file.write_text(
            '{"worker_llm":{"provider":"ollama","model":"local","num_ctx":32768,'
            '"reasoning_effort":"medium"}}',
            encoding="utf-8",
        )
        monkeypatch.setattr("framework.config.HIVE_CONFIG_FILE", config_file)

        assert get_worker_llm_extra_kwargs() == {
            "num_ctx": 32768,
            "reasoning_effort": "medium",
        }

    def test_worker_without_override_inherits_main_reasoning_effort(self, tmp_path, monkeypatch):
        config_file = tmp_path / "configuration.json"
        config_file.write_text(
            '{"llm":{"provider":"openai","model":"gpt-test","reasoning_effort":"low"}}',
            encoding="utf-8",
        )
        monkeypatch.setattr("framework.config.HIVE_CONFIG_FILE", config_file)

        assert get_worker_llm_extra_kwargs() == {"reasoning_effort": "low"}

    @pytest.mark.parametrize("value", ["", "   ", 3, null])
    def test_invalid_reasoning_effort_is_rejected(self, tmp_path, monkeypatch, value):
        config_file = tmp_path / "configuration.json"
        config_file.write_text(
            __import__("json").dumps(
                {"llm": {"provider": "openai", "model": "gpt-test", "reasoning_effort": value}}
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("framework.config.HIVE_CONFIG_FILE", config_file)

        with pytest.raises(ValueError, match="reasoning_effort must be a non-empty string"):
            get_llm_extra_kwargs()


class TestOpenRouterConfig:
    """OpenRouter config composition and fallback behavior."""

    def test_get_preferred_model_for_openrouter(self, tmp_path, monkeypatch):
        config_file = tmp_path / "configuration.json"
        config_file.write_text(
            '{"llm":{"provider":"openrouter","model":"x-ai/grok-4.20-beta"}}',
            encoding="utf-8",
        )
        monkeypatch.setattr("framework.config.HIVE_CONFIG_FILE", config_file)

        assert get_preferred_model() == "openrouter/x-ai/grok-4.20-beta"

    def test_get_preferred_model_normalizes_openrouter_prefixed_model(self, tmp_path, monkeypatch):
        config_file = tmp_path / "configuration.json"
        config_file.write_text(
            '{"llm":{"provider":"openrouter","model":"openrouter/x-ai/grok-4.20-beta"}}',
            encoding="utf-8",
        )
        monkeypatch.setattr("framework.config.HIVE_CONFIG_FILE", config_file)

        assert get_preferred_model() == "openrouter/x-ai/grok-4.20-beta"

    def test_get_api_base_falls_back_to_openrouter_default(self, tmp_path, monkeypatch):
        config_file = tmp_path / "configuration.json"
        config_file.write_text(
            '{"llm":{"provider":"openrouter","model":"x-ai/grok-4.20-beta"}}',
            encoding="utf-8",
        )
        monkeypatch.setattr("framework.config.HIVE_CONFIG_FILE", config_file)

        assert get_api_base() == "https://openrouter.ai/api/v1"

    def test_get_api_base_keeps_explicit_openrouter_api_base(self, tmp_path, monkeypatch):
        config_file = tmp_path / "configuration.json"
        config_file.write_text(
            '{"llm":{"provider":"openrouter","model":"x-ai/grok-4.20-beta","api_base":"https://proxy.example/v1"}}',
            encoding="utf-8",
        )
        monkeypatch.setattr("framework.config.HIVE_CONFIG_FILE", config_file)

        assert get_api_base() == "https://proxy.example/v1"
