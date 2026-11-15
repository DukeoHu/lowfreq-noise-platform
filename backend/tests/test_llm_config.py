from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.db.base import get_session_factory
from app.db.models import LlmConfig
from app.services import llm, llm_config


def _clear_config() -> None:
    session = get_session_factory()()
    try:
        row = session.get(LlmConfig, 1)
        if row is not None:
            session.delete(row)
            session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def clean_llm_config():
    _clear_config()
    reset_settings_cache()
    try:
        yield
    finally:
        _clear_config()
        reset_settings_cache()


def _payload(**overrides) -> dict:
    base = {
        "enabled": True,
        "base_url": "https://example.invalid/v1",
        "model": "test-model",
        "api_key": "sk-test",
        "timeout_s": 30,
        "max_tokens": 800,
        "auto_insight": False,
    }
    base.update(overrides)
    return base


def test_get_llm_config_never_returns_api_key(client: TestClient) -> None:
    response = client.get("/api/system/llm")
    assert response.status_code == 200, response.text
    data = response.json()
    assert "api_key" not in data
    assert data["has_api_key"] is False
    assert data["source"] in {"env", "none"}


def test_update_llm_config_and_keep_or_clear_key(client: TestClient) -> None:
    saved = client.put("/api/system/llm", json=_payload())
    assert saved.status_code == 200, saved.text
    data = saved.json()
    assert data["source"] == "db"
    assert data["enabled"] is True
    assert data["has_api_key"] is True
    assert "api_key" not in data

    # 留空密钥表示保持不变
    kept = client.put("/api/system/llm", json=_payload(enabled=False, api_key=None))
    assert kept.status_code == 200, kept.text
    assert kept.json()["has_api_key"] is True

    cleared = client.put(
        "/api/system/llm",
        json=_payload(enabled=False, api_key=None, clear_api_key=True),
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["has_api_key"] is False

    logs = client.get("/api/audit", params={"action": "system.llm_update"}).json()["items"]
    assert logs
    assert "api_key" not in logs[0]["detail"]


def test_update_llm_config_validation(client: TestClient) -> None:
    bad_url = client.put("/api/system/llm", json=_payload(base_url="api.example.com/v1"))
    assert bad_url.status_code == 422

    missing_model = client.put("/api/system/llm", json=_payload(model=""))
    assert missing_model.status_code == 422

    missing_key = client.put("/api/system/llm", json=_payload(api_key=None))
    assert missing_key.status_code == 422


def test_db_config_overrides_env(client: TestClient) -> None:
    assert llm.is_configured() is False

    response = client.put("/api/system/llm", json=_payload())
    assert response.status_code == 200, response.text
    assert llm.is_configured() is True
    assert llm.get_config().source == "db"
    assert llm.get_config().model == "test-model"


def test_llm_test_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.post("/api/system/llm/test").status_code == 503

    assert client.put("/api/system/llm", json=_payload()).status_code == 200
    monkeypatch.setattr(llm, "chat_json", lambda system, user: {"ok": True})
    response = client.post("/api/system/llm/test")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["model"] == "test-model"
    assert "ok" in data["reply"]


def _runtime_config(**overrides) -> object:
    base = {
        "enabled": True,
        "base_url": "https://example.invalid/v1",
        "api_key": "sk-test",
        "model": "test-model",
        "timeout_s": 5.0,
        "max_tokens": 1500,
        "auto_insight": False,
        "source": "db",
    }
    base.update(overrides)
    return llm_config.LlmRuntimeConfig(**base)


def test_chat_json_escalates_budget_when_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """推理模型把预算用于思考时，应按 2 倍逐级加预算重试。"""
    monkeypatch.setattr(llm, "get_config", lambda: _runtime_config())
    budgets: list[int] = []

    def fake_request(cfg, system, user, max_tokens):
        budgets.append(max_tokens)
        if max_tokens < 3000:
            return "", "length", {"completion_tokens_details": {"reasoning_tokens": 1400}}
        return '{"summary": "ok"}', "stop", {}

    monkeypatch.setattr(llm, "_request", fake_request)
    assert llm.chat_json("system", "user") == {"summary": "ok"}
    assert budgets == [1500, 3000]


def test_chat_json_reports_truncation_when_budget_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "get_config", lambda: _runtime_config(max_tokens=8000))
    monkeypatch.setattr(llm, "_request", lambda cfg, system, user, max_tokens: ("", "length", {}))
    with pytest.raises(llm.LLMError) as excinfo:
        llm.chat_json("system", "user")
    assert "截断" in str(excinfo.value)
    assert "推理" in str(excinfo.value)
