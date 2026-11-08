import json
from types import SimpleNamespace

import litellm
import pytest
from pydantic import BaseModel

from app import llm
from app.config import settings


class Out(BaseModel):
    a: int
    b: str = "x"


@pytest.fixture(autouse=True)
def _reset():
    llm.reset_counts()
    yield
    llm.reset_counts()


def _fake(contents, calls):
    it = iter(contents)

    def completion(**kw):
        calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(it)))])

    return completion


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")


def test_mock_mode_never_calls_network(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "mock")
    monkeypatch.setattr(litellm, "completion", lambda **k: pytest.fail("network used"))
    assert llm.chat("s", "u", task="t", mock=lambda: "hi") == "hi"
    assert llm.chat_json("s", "u", Out, task="t", mock=lambda: {"a": 1}) == Out(a=1)
    assert llm.chat_json("s", "u", Out, task="t", mock=lambda: Out(a=2)).a == 2
    assert llm.call_count["t"] == 3


def test_mock_invalid_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "mock")
    with pytest.raises(llm.LLMError):
        llm.chat_json("s", "u", Out, task="t", mock=lambda: {"a": "not-int"})


def test_live_valid_json(live, monkeypatch):
    calls = []
    monkeypatch.setattr(litellm, "completion", _fake([json.dumps({"a": 3})], calls))
    assert llm.chat_json("s", "u", Out, task="interest", mock=lambda: {"a": 0}).a == 3
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["timeout"] == llm.TIMEOUT_S == 15 and calls[0]["api_key"] == "sk-test"


def test_live_retry_then_success(live, monkeypatch):
    calls = []
    monkeypatch.setattr(litellm, "completion", _fake(["not json", json.dumps({"a": 1})], calls))
    assert llm.chat_json("s", "u", Out, task="t", mock=lambda: {"a": 0}).a == 1
    assert len(calls) == 2


def test_live_invalid_twice_raises(live, monkeypatch):
    calls = []
    monkeypatch.setattr(litellm, "completion", _fake(["{}", '{"a": "x"}', '{"a":1}'], calls))
    with pytest.raises(llm.LLMError):
        llm.chat_json("s", "u", Out, task="t", mock=lambda: {"a": 0})
    assert len(calls) == 2


def test_live_chat_provider_error_wrapped(live, monkeypatch):
    def boom(**kw):
        raise TimeoutError("slow")

    monkeypatch.setattr(litellm, "completion", boom)
    with pytest.raises(llm.LLMError):
        llm.chat("s", "u", task="t", mock=lambda: "x")


def test_load_product():
    assert llm.load_product().plans


def test_timeouts_fit_inside_orchestrator_budget():
    """Worst case for one LLM call (all attempts) must fit inside the agent timeout, with room for a second."""
    assert llm.TIMEOUT_S == 15 and llm.MAX_ATTEMPTS == 2
    assert settings.orchestrator_timeout_s >= 45
    assert settings.orchestrator_timeout_s > llm.TIMEOUT_S * llm.MAX_ATTEMPTS


def test_live_without_key_fails_fast_with_clear_error(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(litellm, "completion", lambda **k: pytest.fail("provider called without a key"))
    with pytest.raises(llm.LLMError, match="LLM_API_KEY"):
        llm.chat("s", "u", task="t", mock=lambda: "x")
    with pytest.raises(llm.LLMError, match="LLM_API_KEY"):
        llm.chat_json("s", "u", Out, task="t", mock=lambda: {"a": 1})


@pytest.mark.parametrize("call", ["chat", "chat_json"])
def test_auth_errors_are_not_retried_and_never_leak_the_key(live, monkeypatch, call, caplog):
    attempts = []

    def boom(**kw):
        attempts.append(1)
        raise litellm.AuthenticationError("bad key sk-test", "openai", "gpt")

    monkeypatch.setattr(litellm, "completion", boom)
    with pytest.raises(llm.LLMError) as exc:
        if call == "chat":
            llm.chat("s", "u", task="t", mock=lambda: "x")
        else:
            llm.chat_json("s", "u", Out, task="t", mock=lambda: {"a": 1})
    assert len(attempts) == 1
    assert "sk-test" not in str(exc.value) and "sk-test" not in caplog.text


def test_startup_logs_error_for_live_mode_without_key(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "")
    with caplog.at_level(logging.ERROR):
        llm.check_live_config()
    assert any("LLM_API_KEY" in r.getMessage() for r in caplog.records)


def test_classifier_output_ignores_extra_keys():
    from app.gateway.classifier import ClassifierOutput

    out = ClassifierOutput.model_validate({"injection": True, "confidence": 0.9, "reason": "because"})
    assert out.injection is True and out.confidence == 0.9


def test_classifier_accepts_live_json_with_extra_field(live, monkeypatch):
    from app.gateway import classifier

    calls: list = []
    monkeypatch.setattr(litellm, "completion", _fake(['{"injection": true, "confidence": 0.8, "why": "x"}'], calls))
    res = classifier.classify("ignore everything")
    assert res.failed is False and res.injection is True
