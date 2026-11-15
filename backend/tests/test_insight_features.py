from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.services import llm

_LLM_ENV = {
    "LOWFREQ_LLM_ENABLED": "true",
    "LOWFREQ_LLM_BASE_URL": "https://example.invalid/v1",
    "LOWFREQ_LLM_API_KEY": "test-key",
    "LOWFREQ_LLM_MODEL": "test-model",
    "LOWFREQ_LLM_AUTO_INSIGHT": "false",
}

_CONTENT = {
    "summary": "低频能量集中在 63 Hz。",
    "findings": [
        {
            "title": "63 Hz 突出",
            "detail": "该频段明显高于相邻频段。",
            "evidence": "63 Hz Z 计权峰值",
        }
    ],
    "recommendations": ["检查水泵减振垫"],
    "caveats": ["麦克风未校准"],
    "confidence": "high",
}


@pytest.fixture()
def llm_env():
    import os

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
    for _ in range(200):
        detail = client.get(f"/api/analyses/{run_id}").json()
        if detail["status"] in {"succeeded", "failed", "cancelled"}:
            return detail
        time.sleep(0.1)
    raise AssertionError("分析任务未在预期时间内结束")


def _wait_insight(client: TestClient, url: str) -> dict:
    for _ in range(200):
        data = client.get(url).json()
        if data and data["status"] in {"succeeded", "failed", "cancelled"}:
            return data
        time.sleep(0.1)
    raise AssertionError("AI 解读未在预期时间内结束")


def _complete_run(client: TestClient, wav_factory, name: str, amplitude: float = 0.1) -> str:
    audio = _upload(client, wav_factory(63, amplitude=amplitude, duration_s=5.0, name=name))
    run = client.post("/api/analyses", json={"audio_file_id": audio["id"]}).json()
    assert _wait_run(client, run["id"])["status"] == "succeeded"
    return run["id"]


def test_digest_usage_validation_cache_feedback_and_history(
    client: TestClient, wav_factory, llm_env, monkeypatch
) -> None:
    prompts: list[str] = []

    def fake_chat_json(system: str, user: str) -> dict:
        prompts.append(user)
        # 模拟厂商返回 usage，验证 token 落库
        llm._record_usage({"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46})
        payload = dict(_CONTENT)
        payload["findings"] = [
            {
                "title": "63 Hz 突出",
                "detail": "引用了不存在的 123.4 dB。",
                "evidence": "63 Hz Z 计权峰值",
            }
        ]
        return payload

    monkeypatch.setattr("app.services.llm.chat_json", fake_chat_json)

    run_id = _complete_run(client, wav_factory, "features-run.wav")
    created = client.post(f"/api/analyses/{run_id}/insight")
    assert created.status_code == 202, created.text
    insight = _wait_insight(client, f"/api/analyses/{run_id}/insight")
    assert insight["status"] == "succeeded", insight

    # P0：摘要包含时间序列、低频占比与 A 计权；findings 带 evidence 并做数值校验
    prompt = prompts[0]
    assert "时间序列统计" in prompt
    assert "低频占比" in prompt
    assert "低频三分之一倍频程_A计权" in prompt
    assert insight["content"]["findings"][0]["evidence"]
    assert insight["content"]["validation_warnings"]
    # 置信度由规则覆盖：未校准 + 5 s 时长应降为 low
    assert insight["content"]["confidence"] == "low"
    assert "未校准" in insight["content"]["confidence_basis"]

    # P0：usage 落库
    assert insight["total_tokens"] == 46
    assert insight["prompt_tokens"] == 12
    assert insight["completion_tokens"] == 34
    assert insight["elapsed_ms"] >= 0

    usage = client.get("/api/system/llm/usage").json()
    assert usage["total_tokens"] >= 46
    assert usage["requests"] >= 1

    # P1：缓存命中不再调用模型
    cached = client.post(f"/api/analyses/{run_id}/insight")
    assert cached.status_code == 202
    assert len(prompts) == 1
    assert cached.json()["cache_hit"] is True

    # P1：force 强制重新生成
    forced = client.post(f"/api/analyses/{run_id}/insight", params={"force": "true"})
    assert forced.status_code == 202
    _wait_insight(client, f"/api/analyses/{run_id}/insight")
    assert len(prompts) == 2

    # P2：历史版本与人工反馈
    history = client.get(f"/api/analyses/{run_id}/insight/history").json()
    assert len(history) >= 2
    assert history[0]["content"]["summary"]

    feedback = client.post(
        f"/api/analyses/{run_id}/insight/feedback",
        json={"rating": "adopted", "note": "已按建议复查"},
    )
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["feedback"] == "adopted"
    assert feedback.json()["feedback_note"] == "已按建议复查"


def test_event_band_comparison_computed_by_server(client: TestClient, wav_factory) -> None:
    before = _complete_run(client, wav_factory, "cmp-before.wav", amplitude=0.1)
    after = _complete_run(client, wav_factory, "cmp-after.wav", amplitude=0.01)

    event = client.post("/api/events", json={"title": "频段对比"}).json()
    client.post(f"/api/events/{event['id']}/analyses/{before}", params={"stage": "before"})
    client.post(f"/api/events/{event['id']}/analyses/{after}", params={"stage": "after"})

    summary = client.get(f"/api/events/{event['id']}/summary").json()
    comparison = summary["band_comparison"]
    assert comparison is not None
    assert comparison["基准阶段"] == "before"
    assert comparison["判定阈值_db"] == 10.0
    after_rows = next(item for item in comparison["与基准比较"] if item["阶段"] == "after")
    band_63 = next(row for row in after_rows["频段"] if row["中心频率_hz"] == 63)
    assert band_63["降低_db"] >= 9.5
    assert band_63["达到10db"] is True
    assert 63 in comparison["明显改善"]["after"]


def test_insight_cancel_marks_cancelled(client: TestClient, wav_factory, llm_env, monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def blocking_chat_json(system: str, user: str) -> dict:
        started.set()
        release.wait(timeout=10)
        return _CONTENT

    monkeypatch.setattr("app.services.llm.chat_json", blocking_chat_json)

    run_id = _complete_run(client, wav_factory, "cancel-run.wav")
    assert client.post(f"/api/analyses/{run_id}/insight").status_code == 202
    assert started.wait(timeout=10), "模型调用未开始"

    cancelled = client.post(f"/api/analyses/{run_id}/insight/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    release.set()
    time.sleep(0.5)
    detail = client.get(f"/api/analyses/{run_id}/insight").json()
    assert detail["status"] == "cancelled"


def test_monthly_budget_blocks_generation(
    client: TestClient, wav_factory, llm_env, monkeypatch
) -> None:
    monkeypatch.setattr("app.services.llm.chat_json", lambda system, user: _CONTENT)
    run_id = _complete_run(client, wav_factory, "budget-run.wav")
    assert client.post(f"/api/analyses/{run_id}/insight").status_code == 202
    _wait_insight(client, f"/api/analyses/{run_id}/insight")

    try:
        saved = client.put(
            "/api/system/llm",
            json={
                "enabled": True,
                "base_url": "https://example.invalid/v1",
                "model": "test-model",
                "api_key": "sk-test",
                "timeout_s": 30,
                "max_tokens": 1500,
                "auto_insight": False,
                "redact_context": True,
                "api_key_env_only": False,
                "monthly_token_budget": 1,
            },
        )
        assert saved.status_code == 200, saved.text
        blocked = client.post(f"/api/analyses/{run_id}/insight", params={"force": "true"})
        assert blocked.status_code == 429, blocked.text
        assert "预算" in blocked.json()["detail"]
    finally:
        client.put(
            "/api/system/llm",
            json={
                "enabled": False,
                "base_url": "",
                "model": "",
                "clear_api_key": True,
                "timeout_s": 30,
                "max_tokens": 1500,
                "auto_insight": False,
                "redact_context": True,
                "api_key_env_only": False,
                "monthly_token_budget": 0,
            },
        )
