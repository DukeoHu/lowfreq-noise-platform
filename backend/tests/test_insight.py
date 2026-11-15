from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache

_LLM_ENV = {
    "LOWFREQ_LLM_ENABLED": "true",
    "LOWFREQ_LLM_BASE_URL": "https://example.invalid/v1",
    "LOWFREQ_LLM_API_KEY": "test-key",
    "LOWFREQ_LLM_MODEL": "test-model",
    "LOWFREQ_LLM_AUTO_INSIGHT": "false",
}

_FAKE_CONTENT = {
    "summary": "低频能量集中在 63 Hz，疑似水泵或变压器结构传声。",
    "findings": [{"title": "63 Hz 突出", "detail": "该频段明显高于相邻倍频程，C−A 差值为正。"}],
    "recommendations": ["检查水泵减振垫与管道刚性连接", "复测时记录设备启停状态"],
    "caveats": ["麦克风未校准，声级为相对值，不能作为执法依据"],
    "confidence": "medium",
}


@pytest.fixture()
def llm_env():
    """临时启用 LLM 配置，结束后恢复环境变量与 settings 缓存。"""
    original = {key: os.environ.get(key) for key in _LLM_ENV}
    os.environ.update(_LLM_ENV)
    reset_settings_cache()
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_settings_cache()


def _upload(client: TestClient, path: Path) -> dict:
    with path.open("rb") as handle:
        response = client.post("/api/audio-files", files={"file": (path.name, handle, "audio/wav")})
    assert response.status_code == 201, response.text
    return response.json()


def _wait_run(client: TestClient, run_id: str) -> dict:
    for _ in range(120):
        detail = client.get(f"/api/analyses/{run_id}").json()
        if detail["status"] in {"succeeded", "failed", "cancelled"}:
            return detail
        time.sleep(0.1)
    raise AssertionError("分析任务未在预期时间内结束")


def _wait_insight(client: TestClient, url: str) -> dict:
    for _ in range(150):
        data = client.get(url).json()
        if data and data["status"] in {"succeeded", "failed"}:
            return data
        time.sleep(0.1)
    raise AssertionError("AI 解读未在预期时间内结束")


def test_insight_disabled_returns_503(client: TestClient, wav_factory) -> None:
    reset_settings_cache()
    audio = _upload(client, wav_factory(63, duration_s=2.0, name="insight-disabled.wav"))
    run = client.post("/api/analyses", json={"audio_file_id": audio["id"]}).json()
    assert _wait_run(client, run["id"])["status"] == "succeeded"

    response = client.post(f"/api/analyses/{run['id']}/insight")
    assert response.status_code == 503, response.text
    assert "未配置大模型" in response.json()["detail"]


def test_analysis_insight_generation_and_report(
    client: TestClient, wav_factory, llm_env, monkeypatch
) -> None:
    calls: list[str] = []

    def fake_chat_json(system: str, user: str) -> dict:
        calls.append(user)
        return _FAKE_CONTENT

    monkeypatch.setattr("app.services.llm.chat_json", fake_chat_json)

    audio = _upload(client, wav_factory(63, duration_s=2.0, name="insight-run.wav"))
    run = client.post("/api/analyses", json={"audio_file_id": audio["id"]}).json()
    assert _wait_run(client, run["id"])["status"] == "succeeded"

    assert client.get(f"/api/analyses/{run['id']}/insight").json() is None

    created = client.post(f"/api/analyses/{run['id']}/insight")
    assert created.status_code == 202, created.text
    assert created.json()["status"] == "queued"

    insight = _wait_insight(client, f"/api/analyses/{run['id']}/insight")
    assert insight["status"] == "succeeded", insight
    assert insight["model"] == "test-model"
    assert insight["content"]["summary"] == _FAKE_CONTENT["summary"]
    assert insight["content"]["recommendations"]
    assert calls
    assert "低频" in calls[0] or "63" in calls[0]

    logs = client.get("/api/audit", params={"action": "analysis.insight"}).json()["items"]
    assert logs and logs[0]["entity_id"] == run["id"]

    report = client.get(f"/api/analyses/{run['id']}/report.pdf")
    assert report.status_code == 200
    assert report.content.startswith(b"%PDF")


def test_event_insight_generation(client: TestClient, wav_factory, llm_env, monkeypatch) -> None:
    monkeypatch.setattr("app.services.llm.chat_json", lambda system, user: _FAKE_CONTENT)

    audio = _upload(client, wav_factory(63, duration_s=2.0, name="insight-event.wav"))
    run = client.post("/api/analyses", json={"audio_file_id": audio["id"]}).json()
    assert _wait_run(client, run["id"])["status"] == "succeeded"

    event = client.post("/api/events", json={"title": "AI 事件解读", "location": "泵房"}).json()
    linked = client.post(f"/api/events/{event['id']}/analyses/{run['id']}")
    assert linked.status_code == 200, linked.text

    created = client.post(f"/api/events/{event['id']}/insight")
    assert created.status_code == 202, created.text

    insight = _wait_insight(client, f"/api/events/{event['id']}/insight")
    assert insight["status"] == "succeeded", insight
    assert insight["content"]["summary"] == _FAKE_CONTENT["summary"]

    assert client.delete(f"/api/events/{event['id']}").status_code == 204
