"""The live model list: names get their provider prefix, non-chat models are dropped."""

from unittest.mock import patch

from opendot import models


def test_full_name_adds_prefix_once():
    assert models.full_name("deepseek", "deepseek-chat") == "deepseek/deepseek-chat"
    assert models.full_name("deepseek", "deepseek/deepseek-chat") == "deepseek/deepseek-chat"
    assert models.full_name("custom", "my-model") == "my-model"


def test_openai_list_newest_first_without_noise():
    data = {"data": [
        {"id": "gpt-a", "created": 1}, {"id": "gpt-b", "created": 3},
        {"id": "gpt-b-2030-01-01", "created": 2}, {"id": "text-embedding-x", "created": 9},
        {"id": "gpt-b-codex", "created": 8}, {"id": "gpt-realtime", "created": 7},
    ]}
    with patch.object(models, "_get", return_value=data):
        names, base = models.list_models("openai", "k")
    assert names == ["openai/gpt-b", "openai/gpt-a"] and base == ""


def test_region_fallback_keeps_working_base():
    calls = []

    def fake(url, headers=None, params=None):
        calls.append(url)
        if "intl" not in url:
            raise PermissionError("the key was not accepted")
        return {"data": [{"id": "qwen-x", "created": 1}]}

    with patch.object(models, "_get", side_effect=fake):
        names, base = models.list_models("qwen", "k")
    assert names == ["qwen-x"] and "intl" in base and len(calls) == 2


def test_friendly_error_hides_details():
    assert "key" in models.friendly_error(PermissionError("x"))
    assert "full LiteLLM" in models.friendly_error(Exception("LLM Provider NOT provided. blah"))
