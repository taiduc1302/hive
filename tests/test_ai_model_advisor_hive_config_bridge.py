from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_config_module():
    path = Path(__file__).resolve().parents[1] / "core" / "framework" / "config.py"
    spec = importlib.util.spec_from_file_location("advisor_hive_config_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_config(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "configuration.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_native_hive_config_forwards_reasoning_effort(tmp_path) -> None:
    config = _load_config_module()
    config.HIVE_CONFIG_FILE = _write_config(
        tmp_path,
        {
            "llm": {
                "provider": "openai",
                "model": "gpt-test",
                "reasoning_effort": " high ",
            }
        },
    )

    assert config.get_llm_extra_kwargs() == {"reasoning_effort": "high"}


def test_native_hive_config_merges_effort_with_existing_kwargs(tmp_path) -> None:
    config = _load_config_module()
    config.HIVE_CONFIG_FILE = _write_config(
        tmp_path,
        {
            "llm": {
                "provider": "openai",
                "model": "gpt-test",
                "reasoning_effort": "xhigh",
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            }
        },
    )

    assert config.get_llm_extra_kwargs() == {
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        "reasoning_effort": "xhigh",
    }


def test_native_worker_config_forwards_reasoning_effort(tmp_path) -> None:
    config = _load_config_module()
    config.HIVE_CONFIG_FILE = _write_config(
        tmp_path,
        {
            "worker_llm": {
                "provider": "ollama",
                "model": "local",
                "num_ctx": 32768,
                "reasoning_effort": "medium",
            }
        },
    )

    assert config.get_worker_llm_extra_kwargs() == {
        "num_ctx": 32768,
        "reasoning_effort": "medium",
    }


@pytest.mark.parametrize("value", ["", "   ", 3, None])
def test_native_hive_config_rejects_invalid_reasoning_effort(tmp_path, value) -> None:
    config = _load_config_module()
    config.HIVE_CONFIG_FILE = _write_config(
        tmp_path,
        {
            "llm": {
                "provider": "openai",
                "model": "gpt-test",
                "reasoning_effort": value,
            }
        },
    )

    with pytest.raises(ValueError, match="reasoning_effort must be a non-empty string"):
        config.get_llm_extra_kwargs()
