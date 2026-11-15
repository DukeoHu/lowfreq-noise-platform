from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.main import create_app

TOKEN = "secret-token"


@pytest.fixture()
def token_client(monkeypatch):
    monkeypatch.setenv("LOWFREQ_ACCESS_TOKEN", TOKEN)
    reset_settings_cache()
    with TestClient(create_app()) as client:
        yield client
    reset_settings_cache()


def test_health_and_root_stay_public(token_client) -> None:
    assert token_client.get("/api/health").status_code == 200
    assert token_client.get("/").status_code == 200


def test_http_requires_token(token_client) -> None:
    assert token_client.get("/api/audio-files").status_code == 401

    bearer = token_client.get("/api/audio-files", headers={"Authorization": f"Bearer {TOKEN}"})
    assert bearer.status_code == 200

    header_token = token_client.get("/api/audio-files", headers={"X-Access-Token": TOKEN})
    assert header_token.status_code == 200

    query_token = token_client.get("/api/audio-files", params={"token": TOKEN})
    assert query_token.status_code == 200

    assert token_client.get("/api/audio-files", params={"token": "wrong"}).status_code == 401


def test_websocket_requires_token(token_client) -> None:
    with token_client.websocket_connect("/api/realtime/ws") as websocket:
        message = websocket.receive_json()
    assert message["type"] == "error"
    assert "口令" in message["detail"]


def test_websocket_accepts_token(token_client) -> None:
    with token_client.websocket_connect(f"/api/realtime/ws?token={TOKEN}") as websocket:
        websocket.send_json({"type": "hello", "sample_rate": 48000})
        ready = websocket.receive_json()
    assert ready["type"] == "ready"


def test_audit_records_actor_from_header(client) -> None:
    event = client.post(
        "/api/events", json={"title": "操作人测试"}, headers={"X-Actor": quote("李四")}
    ).json()
    logs = client.get("/api/audit", params={"action": "event.create", "entity_id": event["id"]}).json()["items"]
    assert logs and logs[0]["actor"] == "李四"

    default_event = client.post("/api/events", json={"title": "默认操作人"}).json()
    logs = client.get(
        "/api/audit", params={"action": "event.create", "entity_id": default_event["id"]}
    ).json()["items"]
    assert logs[0]["actor"] == "local"


def test_upload_rejects_audio_over_duration_limit(monkeypatch, wav_factory) -> None:
    monkeypatch.setenv("LOWFREQ_ANALYSIS_MAX_SECONDS", "1")
    reset_settings_cache()
    try:
        with TestClient(create_app()) as client:
            path = wav_factory(freq_hz=63, duration_s=3.0, name="too-long.wav")
            with path.open("rb") as handle:
                response = client.post(
                    "/api/audio-files", files={"file": ("too-long.wav", handle, "audio/wav")}
                )
        assert response.status_code == 413, response.text
        assert "上限" in response.json()["detail"]
    finally:
        reset_settings_cache()
