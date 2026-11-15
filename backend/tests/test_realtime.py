from __future__ import annotations

import time

import numpy as np
import pytest


def _tone(freq_hz: float, samples: int, sample_rate: int = 48000, amplitude: float = 0.1) -> np.ndarray:
    t = np.arange(samples) / sample_rate
    return (amplitude * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)


def test_realtime_stream_matches_offline_levels(client) -> None:
    sample_rate = 48000
    block = 4096
    blocks = 14
    samples = _tone(63, block * blocks, sample_rate)

    with client.websocket_connect("/api/realtime/ws") as websocket:
        websocket.send_json({"type": "hello", "sample_rate": sample_rate})
        ready = websocket.receive_json()
        assert ready["type"] == "ready"
        assert ready["fft_size"] == 8192
        assert ready["dtype"] == "float32"
        assert ready["measurement_id"]

        frames = []
        for index in range(blocks):
            websocket.send_json(
                {"type": "chunk", "seq": index + 1, "dropped": 0, "t": index * block / sample_rate}
            )
            websocket.send_bytes(samples[index * block : (index + 1) * block].tobytes())
            frames.append(websocket.receive_json())

    assert [frame["seq"] for frame in frames] == list(range(1, blocks + 1))
    assert frames[0]["ready"] is False
    assert frames[1]["ready"] is True

    last = frames[-1]
    assert last["type"] == "frame"
    assert last["t"] == pytest.approx(blocks * block / sample_rate, abs=1e-3)

    levels = last["levels"]
    assert levels["lzeq"] == pytest.approx(71.0, abs=2.0)
    assert levels["laeq"] == pytest.approx(44.8, abs=2.0)
    assert levels["laf"] == pytest.approx(44.8, abs=2.5)
    assert levels["lafmax"] == pytest.approx(levels["laf"], abs=0.5)
    assert levels["lcfmax"] == pytest.approx(levels["lcf"], abs=0.5)
    assert levels["lcpeak"] == pytest.approx(73.2, abs=2.0)

    cumulative = last["cumulative"]
    assert cumulative["lzeq"] == pytest.approx(71.0, abs=2.0)
    assert cumulative["laeq"] == pytest.approx(44.8, abs=2.0)
    assert cumulative["duration_s"] == pytest.approx(blocks * block / sample_rate, abs=1e-3)

    peak_index = int(np.argmax(last["fft"]["level"]))
    assert last["fft"]["f"][peak_index] == pytest.approx(63, abs=6)
    assert last["peak_hold"]["f"] == last["fft"]["f"]
    assert max(last["peak_hold"]["level"]) >= max(last["fft"]["level"])
    assert last["spectrum"]["level"] == last["fft"]["level"]

    bands = {band["fc"]: band["level"] for band in last["bands"]}
    assert bands
    assert all(20 <= fc <= 200 for fc in bands)
    assert bands[63] == pytest.approx(71.0, abs=2.0)

    measurement = client.get(f"/api/measurements/{ready['measurement_id']}")
    assert measurement.status_code == 200
    payload = measurement.json()
    assert payload["status"] == "interrupted"
    assert payload["point_count"] >= 1
    assert payload["points"][0]["t_offset_s"] == 0
    assert payload["points"][0]["laeq"] == pytest.approx(44.8, abs=2.0)
    assert payload["duration_s"] == pytest.approx(blocks * block / sample_rate, abs=0.05)


def test_realtime_second_metrics_are_true_leq(client) -> None:
    """逐秒指标应是整秒能量积分，而不是最后一窗（约 171 ms）的快照。"""
    sample_rate = 48000
    block = 4096
    half = sample_rate // 2
    second = np.concatenate(
        [_tone(63, half, sample_rate), np.zeros(half, dtype=np.float32)]
    )
    samples = np.concatenate([second, second, np.zeros(block, dtype=np.float32)])

    seconds: list[dict] = []
    with client.websocket_connect("/api/realtime/ws") as websocket:
        websocket.send_json({"type": "hello", "sample_rate": sample_rate})
        websocket.receive_json()
        for start in range(0, samples.size, block):
            websocket.send_bytes(samples[start : start + block].tobytes())
            frame = websocket.receive_json()
            seconds.extend(frame["seconds"])

    assert seconds, "应至少产出一个完整秒的指标"
    first = seconds[0]
    assert first["t_offset_s"] == 0
    # 前半秒 63 Hz 正弦、后半秒静音：整秒 Leq 比纯音低约 3 dB
    assert first["lzeq"] == pytest.approx(68.0, abs=1.0)
    assert first["laeq"] == pytest.approx(41.8, abs=1.0)
    assert first["c_minus_a"] == pytest.approx(26.2, abs=1.5)
    assert first["lafmax"] == pytest.approx(44.8, abs=2.5)


def test_realtime_rejects_invalid_hello(client) -> None:
    with client.websocket_connect("/api/realtime/ws") as websocket:
        websocket.send_json({"type": "hello", "sample_rate": 100})
        message = websocket.receive_json()

    assert message["type"] == "error"
    assert "sample_rate" in message["detail"]


def _wait_for_run(client, run_id: str, timeout_s: float = 60.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        response = client.get(f"/api/analyses/{run_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.2)
    raise AssertionError(f"分析任务超时: {run_id}")


def test_realtime_stop_saves_recording_and_runs_analysis(client) -> None:
    sample_rate = 48000
    block = 4096
    blocks = 28
    samples = _tone(63, block * blocks, sample_rate)

    with client.websocket_connect("/api/realtime/ws") as websocket:
        websocket.send_json({"type": "hello", "sample_rate": sample_rate, "save_audio": True})
        ready = websocket.receive_json()
        assert ready["save_on_stop"] is True

        for index in range(blocks):
            websocket.send_bytes(samples[index * block : (index + 1) * block].tobytes())
            websocket.receive_json()

        websocket.send_json({"type": "stop"})
        saved = websocket.receive_json()

    assert saved["type"] == "saved"
    assert saved["truncated"] is False
    assert saved["duration_s"] == pytest.approx(block * blocks / sample_rate, abs=0.01)

    audio = client.get(f"/api/audio-files/{saved['audio_file_id']}").json()
    assert audio["sample_rate"] == sample_rate
    assert audio["channels"] == 1
    assert audio["duration_s"] == pytest.approx(block * blocks / sample_rate, abs=0.01)
    assert audio["original_filename"].startswith("realtime-")
    assert client.get(f"/api/audio-files/{saved['audio_file_id']}/download").status_code == 200

    detail = _wait_for_run(client, saved["run_id"])
    assert detail["status"] == "succeeded", detail
    result = detail["result"]
    assert result is not None
    assert result["overall"]["lzeq"] == pytest.approx(71.0, abs=2.0)
    assert result["quality"] is not None
    assert detail["config"]["capture"]["source"] == "realtime"

    measurement = client.get(f"/api/measurements/{saved['measurement_id']}").json()
    assert measurement["status"] == "completed"
    assert measurement["point_count"] >= 2
    assert measurement["segments"][0]["run_id"] == saved["run_id"]
    assert measurement["segments"][0]["duration_s"] == pytest.approx(saved["duration_s"], abs=0.01)


def test_realtime_defaults_to_no_audio_save(client) -> None:
    sample_rate = 48000
    with client.websocket_connect("/api/realtime/ws") as websocket:
        websocket.send_json({"type": "hello", "sample_rate": sample_rate})
        ready = websocket.receive_json()
        assert ready["save_on_stop"] is False
        websocket.send_bytes(_tone(63, 4096 * 5, sample_rate).tobytes())
        websocket.receive_json()
        websocket.send_json({"type": "stop"})
        message = websocket.receive_json()

    assert message["type"] == "stopped"
    assert message["saved"] is False


def test_realtime_disconnect_keeps_segment_for_reconnect(client) -> None:
    sample_rate = 48000
    session_id = "test-session-1"
    with client.websocket_connect("/api/realtime/ws") as websocket:
        websocket.send_json(
            {"type": "hello", "sample_rate": sample_rate, "save_audio": True, "session_id": session_id}
        )
        ready = websocket.receive_json()
        assert ready["segments"] == []
        first_measurement_id = ready["measurement_id"]
        websocket.send_bytes(_tone(63, 4096 * 14, sample_rate).tobytes())
        websocket.receive_json()

    with client.websocket_connect("/api/realtime/ws") as websocket:
        websocket.send_json(
            {
                "type": "hello",
                "sample_rate": sample_rate,
                "save_audio": True,
                "session_id": session_id,
                "segment": 1,
            }
        )
        ready = websocket.receive_json()

    assert len(ready["segments"]) == 1
    assert ready["segments"][0]["interrupted"] is True
    assert ready["measurement_id"] == first_measurement_id

    detail = client.get(f"/api/measurements/{first_measurement_id}").json()
    assert detail["status"] == "interrupted"
    assert detail["segments"][0]["run_id"] == ready["segments"][0]["run_id"]
    assert detail["points"]


def test_realtime_short_session_is_discarded(client) -> None:
    sample_rate = 48000
    with client.websocket_connect("/api/realtime/ws") as websocket:
        websocket.send_json({"type": "hello", "sample_rate": sample_rate, "save_audio": True})
        websocket.receive_json()
        websocket.send_bytes(_tone(63, 4096, sample_rate).tobytes())
        websocket.receive_json()
        websocket.send_json({"type": "stop"})
        message = websocket.receive_json()

    assert message["type"] == "discarded"
    assert "未保存" in message["detail"]


def test_dashboard_returns_kpis_and_latest_measurement(client) -> None:
    sample_rate = 48000
    block = 4096
    samples = _tone(63, block * 20, sample_rate)
    with client.websocket_connect("/api/realtime/ws") as websocket:
        websocket.send_json({"type": "hello", "sample_rate": sample_rate})
        ready = websocket.receive_json()
        for index in range(20):
            websocket.send_bytes(samples[index * block : (index + 1) * block].tobytes())
            websocket.receive_json()
        websocket.send_json({"type": "stop"})
        stopped = websocket.receive_json()

    assert stopped["type"] == "stopped"
    assert stopped["measurement_id"] == ready["measurement_id"]

    response = client.get("/api/dashboard")
    assert response.status_code == 200
    payload = response.json()
    assert payload["kpi"]["measurements"] >= 1
    assert payload["kpi"]["recorded_seconds"] > 0
    latest = payload["latest_measurement"]
    assert latest is not None
    assert latest["id"] == ready["measurement_id"]
    assert latest["points"]
    assert latest["points"][0]["laeq"] is not None
    assert payload["recent_measurements"][0]["id"] == ready["measurement_id"]
    assert payload["system"]["disk"]["total_bytes"] > 0

    listing = client.get("/api/measurements?limit=5")
    assert listing.status_code == 200
    assert listing.json()[0]["id"] == ready["measurement_id"]
