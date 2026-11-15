from __future__ import annotations

import io
import time
import base64
import uuid
import zipfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.core.config import get_settings
from app.db.base import get_engine, get_session_factory
from app.db.models import AnalysisRun, AudioFile, Measurement, MeasurementSegment
from app.workers.runner import recover_pending_runs


def _upload(client, path: Path, filename: str | None = None) -> dict:
    with path.open("rb") as handle:
        response = client.post(
            "/api/audio-files",
            files={"file": (filename or path.name, handle, "audio/wav")},
        )
    assert response.status_code == 201, response.text
    return response.json()


def _wait_for(client, run_id: str, timeout_s: float = 60.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        response = client.get(f"/api/analyses/{run_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.2)
    raise AssertionError(f"分析任务超时: {run_id}")


def test_health(client) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_upload_analyze_and_export(client, wav_factory) -> None:
    path = wav_factory(freq_hz=63, amplitude=0.1, duration_s=5.0)

    audio = _upload(client, path)
    assert audio["sample_rate"] == 48000
    assert audio["channels"] == 1
    assert audio["duration_s"] == pytest.approx(5.0, abs=0.01)

    response = client.post("/api/analyses", json={"audio_file_id": audio["id"]})
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    detail = _wait_for(client, run_id)
    assert detail["status"] == "succeeded", detail
    assert detail["result"] is not None
    assert detail["result"]["overall"]["lzeq"] is not None
    assert detail["quality"]["calibrated"] is False

    csv_response = client.get(f"/api/analyses/{run_id}/bands.csv")
    assert csv_response.status_code == 200
    assert "center_frequency_hz" in csv_response.text
    assert csv_response.text.count("\n") > 10


def test_calibrated_run_shifts_level(client, wav_factory) -> None:
    path = wav_factory(freq_hz=1000, amplitude=0.1, duration_s=3.0, name="cal.wav")
    audio = _upload(client, path)

    payload = {
        "audio_file_id": audio["id"],
        "config": {"calibration": {"mode": "calibrated", "offset_db": 10.0, "full_scale_pa": 1.0}},
    }
    run = client.post("/api/analyses", json=payload).json()
    detail = _wait_for(client, run["id"])

    assert detail["status"] == "succeeded"
    assert detail["quality"]["calibrated"] is True
    assert "uncalibrated" not in detail["quality"]["flags"]
    assert detail["result"]["overall"]["lzeq"] == pytest.approx(80.97, abs=0.6)


def test_event_extended_fields_photos_and_audit(client) -> None:
    created = client.post(
        "/api/events",
        json={
            "title": "机房嗡声复测",
            "description": "夜间稳定低频",
            "location": "3F",
            "measurement_point": "机房门外 1 m",
            "equipment": "校准麦克风",
            "notes": "首次排查",
            "tags": ["夜间", "机房"],
            "latitude": 31.2304,
            "longitude": 121.4737,
        },
    )
    assert created.status_code == 201, created.text
    event = created.json()
    assert event["measurement_point"] == "机房门外 1 m"
    assert event["tags"] == ["夜间", "机房"]
    assert event["latitude"] == pytest.approx(31.2304)

    updated = client.patch(
        f"/api/events/{event['id']}",
        json={"notes": "已联系物业", "tags": ["复测"], "status": "investigating"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["notes"] == "已联系物业"
    assert updated.json()["tags"] == ["复测"]

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lY1VAAAAAElFTkSuQmCC"
    )
    photo_response = client.post(
        f"/api/events/{event['id']}/photos",
        files={"file": ("site.png", png, "image/png")},
    )
    assert photo_response.status_code == 201, photo_response.text
    photo = photo_response.json()
    assert photo["original_filename"] == "site.png"

    download = client.get(f"/api/events/{event['id']}/photos/{photo['id']}")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("image/")
    assert download.content.startswith(b"\x89PNG")

    detail = client.get(f"/api/events/{event['id']}").json()
    assert len(detail["photos"]) == 1

    rejected = client.post(
        f"/api/events/{event['id']}/photos",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert rejected.status_code == 415

    assert client.delete(f"/api/events/{event['id']}/photos/{photo['id']}").status_code == 204
    assert client.get(f"/api/events/{event['id']}").json()["photos"] == []

    actions = {entry["action"] for entry in client.get("/api/audit").json()["items"]}
    assert {"event.create", "event.update", "event.photo_upload", "event.photo_delete"} <= actions

    event_audit = client.get(f"/api/audit?entity_type=event&entity_id={event['id']}").json()["items"]
    assert all(item["entity_id"] == event["id"] for item in event_audit)


def test_analysis_stage_off_and_export_audit(client, wav_factory) -> None:
    audio = _upload(client, wav_factory(freq_hz=63, duration_s=3.0, name="off-stage.wav"))
    run = client.post("/api/analyses", json={"audio_file_id": audio["id"]}).json()
    _wait_for(client, run["id"])

    patched = client.patch(f"/api/analyses/{run['id']}", json={"stage": "off"})
    assert patched.status_code == 200
    assert patched.json()["stage"] == "off"

    assert client.get(f"/api/analyses/{run['id']}/export.zip").status_code == 200
    actions = [(item["action"], item["detail"].get("format")) for item in client.get("/api/audit").json()["items"]]
    assert ("analysis.create", None) in actions
    assert ("analysis.export", "zip") in actions


def test_rejects_unsupported_extension(client, tmp_path) -> None:
    bad = tmp_path / "note.txt"
    bad.write_text("not audio")
    with bad.open("rb") as handle:
        response = client.post("/api/audio-files", files={"file": ("note.txt", handle, "text/plain")})
    assert response.status_code == 415


def test_rejects_corrupted_audio(client, tmp_path) -> None:
    bad = tmp_path / "broken.wav"
    bad.write_bytes(b"RIFF0000WAVEjunk")
    with bad.open("rb") as handle:
        response = client.post("/api/audio-files", files={"file": ("broken.wav", handle, "audio/wav")})
    assert response.status_code == 400


def test_analysis_missing_audio_returns_404(client) -> None:
    response = client.post("/api/analyses", json={"audio_file_id": "does-not-exist"})
    assert response.status_code == 404


def test_event_linking_flow(client, wav_factory) -> None:
    audio = _upload(client, wav_factory(freq_hz=63, duration_s=3.0, name="event.wav"))
    run = client.post("/api/analyses", json={"audio_file_id": audio["id"]}).json()
    _wait_for(client, run["id"])

    event = client.post(
        "/api/events",
        json={"title": "办公室夜间嗡声", "description": "63 Hz 附近稳定峰", "location": "3F 机房旁"},
    )
    assert event.status_code == 201
    event_id = event.json()["id"]

    linked = client.post(f"/api/events/{event_id}/analyses/{run['id']}")
    assert linked.status_code == 200
    assert linked.json()["analysis_run_ids"] == [run["id"]]

    listing = client.get("/api/events")
    assert listing.status_code == 200
    assert any(item["id"] == event_id for item in listing.json())

    summary = client.get(f"/api/events/{event_id}/summary")
    assert summary.status_code == 200, summary.text
    payload = summary.json()
    assert payload["event"]["id"] == event_id
    assert len(payload["analyses"]) == 1
    metrics = payload["analyses"][0]
    assert metrics["run_id"] == run["id"]
    assert metrics["audio_filename"] == "event.wav"
    assert metrics["laeq"] is not None
    assert metrics["lzeq"] is not None
    assert metrics["dominant_peak_hz"] == pytest.approx(63, abs=2)
    assert metrics["calibrated"] is False
    assert "uncalibrated" in metrics["flags"]

    unlinked = client.delete(f"/api/events/{event_id}/analyses/{run['id']}")
    assert unlinked.status_code == 200
    assert unlinked.json()["analysis_run_ids"] == []

    after = client.get(f"/api/events/{event_id}/summary").json()
    assert after["analyses"] == []


def test_report_pdf_export(client, wav_factory) -> None:
    audio = _upload(client, wav_factory(freq_hz=63, duration_s=3.0, name="report.wav"))
    run = client.post("/api/analyses", json={"audio_file_id": audio["id"]}).json()
    _wait_for(client, run["id"])

    response = client.get(f"/api/analyses/{run['id']}/report.pdf")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert "lowfreq-report-" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 2000


def test_report_missing_run_returns_404(client) -> None:
    response = client.get("/api/analyses/does-not-exist/report.pdf")
    assert response.status_code == 404


def test_analysis_stage_for_before_after_comparison(client, wav_factory) -> None:
    before_audio = _upload(client, wav_factory(freq_hz=63, amplitude=0.2, duration_s=3.0, name="before.wav"))
    after_audio = _upload(client, wav_factory(freq_hz=63, amplitude=0.02, duration_s=3.0, name="after.wav"))
    before_run = client.post("/api/analyses", json={"audio_file_id": before_audio["id"]}).json()
    after_run = client.post("/api/analyses", json={"audio_file_id": after_audio["id"]}).json()
    _wait_for(client, before_run["id"])
    _wait_for(client, after_run["id"])

    event = client.post("/api/events", json={"title": "整改复测"}).json()
    assert client.post(f"/api/events/{event['id']}/analyses/{before_run['id']}?stage=before").status_code == 200
    assert client.post(f"/api/events/{event['id']}/analyses/{after_run['id']}?stage=after").status_code == 200

    summary = client.get(f"/api/events/{event['id']}/summary").json()
    stages = {item["run_id"]: item["stage"] for item in summary["analyses"]}
    assert stages == {before_run["id"]: "before", after_run["id"]: "after"}
    laeq = {item["run_id"]: item["laeq"] for item in summary["analyses"]}
    assert laeq[before_run["id"]] - laeq[after_run["id"]] > 15

    cleared = client.patch(f"/api/analyses/{after_run['id']}", json={"stage": None})
    assert cleared.status_code == 200
    assert cleared.json()["stage"] is None

    assert client.post(f"/api/events/{event['id']}/analyses/{after_run['id']}?stage=invalid").status_code == 422


def test_extended_metrics_and_exports(client, wav_factory) -> None:
    audio = _upload(client, wav_factory(freq_hz=63, amplitude=0.1, duration_s=5.0, name="extended.wav"))
    run = client.post("/api/analyses", json={"audio_file_id": audio["id"]}).json()
    detail = _wait_for(client, run["id"])
    assert detail["status"] == "succeeded", detail
    result = detail["result"]

    overall = result["overall"]
    assert overall["lafmax"] is not None
    assert overall["lcfmax"] is not None
    assert overall["lcpeak"] >= overall["lcfmax"]
    assert overall["lafmax"] == pytest.approx(overall["laeq"], abs=1.0)

    assert result["low_frequency"]["ratio"] > 0.9
    fft_payload = result["fft"]
    assert len(fft_payload["f"]) > 50
    peak_index = int(np.argmax(fft_payload["level"]))
    assert fft_payload["f"][peak_index] == pytest.approx(63, abs=2)

    assert result["harmonics"]
    assert result["harmonics"][0]["fundamental_hz"] == pytest.approx(63, abs=2)
    assert result["harmonics"][0]["rpm"] == pytest.approx(3780, abs=120)

    json_response = client.get(f"/api/analyses/{run['id']}/result.json")
    assert json_response.status_code == 200
    assert json_response.json()["overall"]["lzeq"] == overall["lzeq"]

    zip_response = client.get(f"/api/analyses/{run['id']}/export.zip")
    assert zip_response.status_code == 200
    assert zip_response.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(zip_response.content)).namelist()
    assert any(name.endswith("result.json") for name in names)
    assert any(name.endswith("bands-third-octave.csv") for name in names)
    assert any(name.endswith("report.pdf") for name in names)

    download = client.get(f"/api/audio-files/{audio['id']}/download")
    assert download.status_code == 200
    assert download.content[:4] == b"RIFF"


def test_fast_max_reflects_level_step(client, tmp_path) -> None:
    sample_rate = 48000
    t = np.arange(int(4 * sample_rate)) / sample_rate
    tone = np.sin(2 * np.pi * 63 * t)
    data = np.concatenate([0.05 * tone[: 2 * sample_rate], 0.5 * tone[2 * sample_rate :]]).astype(np.float32)
    path = tmp_path / "step.wav"
    sf.write(str(path), data, sample_rate, subtype="FLOAT")

    audio = _upload(client, path)
    run = client.post("/api/analyses", json={"audio_file_id": audio["id"]}).json()
    detail = _wait_for(client, run["id"])
    overall = detail["result"]["overall"]
    assert overall["lafmax"] - overall["laeq"] > 2.0


def test_harmonic_candidates_match_orders(client, tmp_path) -> None:
    sample_rate = 48000
    t = np.arange(int(4 * sample_rate)) / sample_rate
    data = 0.05 * (np.sin(2 * np.pi * 50 * t) + np.sin(2 * np.pi * 100 * t) + np.sin(2 * np.pi * 150 * t))
    path = tmp_path / "harmonic.wav"
    sf.write(str(path), data.astype(np.float32), sample_rate, subtype="FLOAT")

    audio = _upload(client, path)
    run = client.post("/api/analyses", json={"audio_file_id": audio["id"]}).json()
    detail = _wait_for(client, run["id"])
    candidates = detail["result"]["harmonics"]
    assert candidates
    top = next(item for item in candidates if abs(item["fundamental_hz"] - 50) <= 1)
    assert {2, 3}.issubset(set(top["matched_orders"]))
    assert top["score"] > 0.2


def test_event_delete_removes_photos_and_writes_audit(client) -> None:
    event = client.post("/api/events", json={"title": "待删除事件"}).json()
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lY1VAAAAAElFTkSuQmCC"
    )
    photo = client.post(
        f"/api/events/{event['id']}/photos",
        files={"file": ("delete.png", png, "image/png")},
    ).json()

    response = client.delete(f"/api/events/{event['id']}")
    assert response.status_code == 204, response.text
    assert client.get(f"/api/events/{event['id']}").status_code == 404
    assert client.get(f"/api/events/{event['id']}/photos/{photo['id']}").status_code == 404

    logs = client.get("/api/audit", params={"action": "event.delete"}).json()["items"]
    assert any(item["entity_id"] == event["id"] for item in logs)
    assert logs[0]["detail"]["photo_count"] == 1


def test_system_status_reports_storage_and_disk(client) -> None:
    response = client.get("/api/system/status")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["disk"]["total_bytes"] > 0
    assert 0 <= payload["disk"]["used_percent"] <= 100
    assert payload["analysis_workers"] >= 1
    assert payload["audio"]["count"] >= 0
    assert payload["results"]["count"] >= 0


def test_sqlite_enables_wal_and_busy_timeout(client) -> None:
    with get_engine().connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        timeout = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
        synchronous = conn.exec_driver_sql("PRAGMA synchronous").scalar()

    assert str(mode).lower() == "wal"
    assert int(timeout) >= 30000
    assert int(synchronous) == 1  # NORMAL


def test_recover_pending_runs_requeues_running(client, wav_factory) -> None:
    audio = _upload(client, wav_factory(63, duration_s=2.0, name="recover.wav"))
    run = client.post("/api/analyses", json={"audio_file_id": audio["id"]}).json()
    detail = _wait_for(client, run["id"])

    session = get_session_factory()()
    try:
        stale = AnalysisRun(
            id=uuid.uuid4().hex,
            audio_file_id=audio["id"],
            status="running",
            config=detail["config"],
            algorithm_version=detail["algorithm_version"],
        )
        session.add(stale)
        session.commit()
        stale_id = stale.id
    finally:
        session.close()

    assert recover_pending_runs() >= 1
    recovered = _wait_for(client, stale_id)
    assert recovered["status"] == "succeeded", recovered
    assert recovered["result"] is not None


def test_list_endpoints_return_pagination_envelope(client, wav_factory) -> None:
    audio = _upload(client, wav_factory(63, duration_s=2.0, name="paging.wav"))
    run = client.post("/api/analyses", json={"audio_file_id": audio["id"]}).json()
    _wait_for(client, run["id"])

    analyses = client.get("/api/analyses", params={"limit": 1, "offset": 0}).json()
    assert {"items", "total", "limit", "offset"} <= set(analyses)
    assert analyses["limit"] == 1 and analyses["offset"] == 0
    assert analyses["total"] >= 1
    assert len(analyses["items"]) == 1

    by_name = client.get("/api/analyses", params={"filename": "paging.wav"}).json()
    assert by_name["total"] >= 1
    assert all(item["audio_file_id"] == audio["id"] for item in by_name["items"])

    audio_page = client.get("/api/audio-files", params={"filename": "paging"}).json()
    assert {"items", "total"} <= set(audio_page)
    assert audio_page["total"] >= 1

    audit_page = client.get("/api/audit", params={"limit": 1}).json()
    assert {"items", "total", "limit", "offset"} <= set(audit_page)
    assert len(audit_page["items"]) == 1

    future = client.get("/api/audit", params={"since": "2999-01-01T00:00:00"}).json()
    assert future["total"] == 0
    assert future["items"] == []


def test_analysis_retry_only_for_failed_runs(client, wav_factory) -> None:
    audio = _upload(client, wav_factory(63, duration_s=2.0, name="retry.wav"))

    session = get_session_factory()()
    try:
        failed = AnalysisRun(
            audio_file_id=audio["id"],
            status="failed",
            algorithm_version="test",
            config={},
            error="synthetic failure",
        )
        session.add(failed)
        session.commit()
        run_id = failed.id
    finally:
        session.close()

    response = client.post(f"/api/analyses/{run_id}/retry")
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"

    detail = _wait_for(client, run_id)
    assert detail["status"] == "succeeded", detail
    assert detail["result"] is not None

    assert client.post(f"/api/analyses/{run_id}/retry").status_code == 409
    assert client.get("/api/audit", params={"action": "analysis.retry"}).json()["total"] >= 1


def test_analysis_cancel_queued_run(client) -> None:
    session = get_session_factory()()
    try:
        audio = session.query(AudioFile).order_by(AudioFile.created_at.desc()).first()
        assert audio is not None
        queued = AnalysisRun(
            audio_file_id=audio.id,
            status="queued",
            algorithm_version="test",
            config={},
        )
        session.add(queued)
        session.commit()
        run_id = queued.id
    finally:
        session.close()

    response = client.post(f"/api/analyses/{run_id}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert client.post(f"/api/analyses/{run_id}/cancel").status_code == 409
    assert client.get("/api/audit", params={"action": "analysis.cancel"}).json()["total"] >= 1


def test_delete_audio_cleans_results_and_segments(client, wav_factory) -> None:
    audio = _upload(client, wav_factory(63, duration_s=2.0, name="cleanup.wav"))
    run = client.post("/api/analyses", json={"audio_file_id": audio["id"]}).json()
    detail = _wait_for(client, run["id"])
    assert detail["status"] == "succeeded"

    settings = get_settings()
    session = get_session_factory()()
    try:
        record = session.get(AnalysisRun, run["id"])
        assert record is not None and record.result_path
        result_path = settings.resolved_data_dir / record.result_path
        assert result_path.exists()

        measurement = Measurement(session_id="cleanup-session", status="completed")
        session.add(measurement)
        session.flush()
        segment = MeasurementSegment(
            measurement_id=measurement.id,
            run_id=run["id"],
            audio_file_id=audio["id"],
            duration_s=2.0,
        )
        session.add(segment)
        session.commit()
        segment_id = segment.id
    finally:
        session.close()

    response = client.delete(f"/api/audio-files/{audio['id']}")
    assert response.status_code == 204, response.text
    assert not result_path.exists()
    assert client.get(f"/api/analyses/{run['id']}").status_code == 404

    session = get_session_factory()()
    try:
        assert session.get(AudioFile, audio["id"]) is None
        cleaned = session.get(MeasurementSegment, segment_id)
        assert cleaned is not None
        assert cleaned.audio_file_id is None
        assert cleaned.run_id is None
    finally:
        session.close()

    logs = client.get(
        "/api/audit", params={"action": "audio.delete", "entity_id": audio["id"]}
    ).json()["items"]
    assert logs
    assert logs[0]["detail"]["analysis_count"] >= 1
    assert logs[0]["detail"]["segment_count"] >= 1
    assert logs[0]["detail"]["result_files"] >= 1
