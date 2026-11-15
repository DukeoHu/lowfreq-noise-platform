"""LLM 辅助解读：把分析指标 / 事件阶段对比整理成提示词，调用模型并落库。

原则：
- 只发送指标摘要，不发送原始音频与完整频谱；
- 默认脱敏：不发送文件名、现场位置、备注与设备名称；
- 模型输出只做解释，不写回 DSP 结果，可随时重新生成；
- 事件阶段差值、置信度、数值一致性校验全部由服务端计算，避免模型自行推算；
- 未校准、数据质量标志等限制必须原样出现在提示词中，并由模型在免责声明中重申。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.base import get_session_factory
from app.db.models import (
    AnalysisInsight,
    AnalysisRun,
    EventInsight,
    InsightVersion,
    NoiseEvent,
)
from app.schemas.insight import InsightContent
from app.services import llm, llm_config, storage

logger = logging.getLogger(__name__)

PROMPT_VERSION = "insight-v2"
# 事件阶段对比：频段降低达到该值即判定为“明显改善”
STAGE_IMPROVEMENT_DB = 10.0
# 数值一致性校验容差（dB 与 Hz 共用）
NUMBER_TOLERANCE = 0.1
# 时长置信度分档
SHORT_DURATION_S = 10.0
GOOD_DURATION_S = 30.0
# 阶段顺序与展示顺序保持一致
STAGE_ORDER = ("before", "off", "after")
REDACTED = "（已脱敏）"

_SYSTEM_PROMPT = """你是一名低频噪声排查工程师助手，熟悉 1/3 倍频程、A/C/Z 计权与常见低频声源
（电梯、水泵、变压器、中央空调、风机、冷却塔、电梯导轨、结构传声等）。

你必须遵守：
1. 只能依据用户提供的指标做解释，不得编造法规限值、不得直接判定“超标/违法”。
2. 若 quality.calibrated 为 false 或 flags 含 uncalibrated，必须明确说明所有声级为相对值，
   不能作为法定检测或执法依据。
3. 引用数值时使用输入中的原值，不要自行换算或推测未提供的频段；每条 findings 必须给出
   evidence 字段，写明引用的原始指标（例如 “63 Hz Z 计权 55.2 dB”）。
4. 结合 C−A 差值、低频 1/3 倍频程突出频段、局部峰值、时间序列波动及其他质量标志给出
   可执行的排查步骤。
5. 只输出一个 JSON 对象，不要输出 Markdown 代码块或解释性前后缀。

JSON 结构：
{
  "summary": "一句话总体判断",
  "findings": [{"title": "现象或结论", "detail": "解释", "evidence": "引用的原始指标"}],
  "recommendations": ["可执行的排查/整改建议"],
  "caveats": ["数据限制与免责声明"],
  "confidence": "low | medium | high"
}
"""

_ANALYSIS_INSTRUCTION = (
    "请根据以下一次低频噪声分析的指标摘要，给出辅助解读。\n"
    "重点说明：低频能量集中在哪些频段、C−A 差值意味着什么、时间序列是否稳定、"
    "可能的声源方向、下一步如何分工排查，以及数据可信度限制。\n"
    "阶段频段差值、置信度与数值校验由服务端计算，你只需解释并引用。\n\n"
)

_EVENT_INSTRUCTION = (
    "请根据以下噪音事件的整改前后（before/off/after）指标摘要，给出辅助解读。\n"
    "服务端已计算各阶段 1/3 倍频程差值与 ≥10 dB 判定，请直接引用，不要自行重算。\n"
    "重点说明：整改前后声级与频谱是否改善、改善可能来自哪些措施、"
    "是否仍存在残余低频源、下一步如何复测与取证，以及数据可信度限制。\n\n"
)

_executor: ThreadPoolExecutor | None = None
_executor_lock = Lock()


class InsightQueueError(RuntimeError):
    """解读请求无法入队（数据未就绪等），由路由转换为 409。"""


class InsightBudgetExceeded(InsightQueueError):
    """月度 token 预算已用尽，由路由转换为 429。"""


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="insight")
    return _executor


def shutdown_insight_runner() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=False)
        _executor = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_available() -> bool:
    """大模型是否已配置；未配置时接口返回 503，不影响分析主流程。"""
    return llm.is_configured()


def _load_result(run: AnalysisRun) -> dict | None:
    if not run.result_path:
        return None
    path = get_settings().resolved_data_dir / run.result_path
    if not path.exists():
        return None
    try:
        return storage.read_json(path)
    except (OSError, json.JSONDecodeError):
        return None


def _round(value, digits: int = 1):
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _band_levels(result: dict) -> dict[float, float]:
    bands = (result.get("bands") or {}).get("z_weighted") or []
    levels: dict[float, float] = {}
    for band in bands:
        fc = band.get("fc")
        level = band.get("level")
        if fc is None or level is None:
            continue
        levels[float(fc)] = float(level)
    return levels


def _time_series_stats(result: dict) -> dict | None:
    series = result.get("time_series") or {}
    times = series.get("t") or []
    levels = series.get("laeq") or []
    values = [
        (float(t), float(v))
        for t, v in zip(times, levels)
        if isinstance(v, (int, float)) and isinstance(t, (int, float))
    ]
    if not values:
        return None
    numbers = [value for _, value in values]
    highest = max(numbers)
    highest_at = next(t for t, value in values if value == highest)
    ordered = sorted(numbers)
    p90 = ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))]
    return {
        "帧数": len(numbers),
        "平均LAeq_db": round(sum(numbers) / len(numbers), 1),
        "最低LAeq_db": round(min(numbers), 1),
        "最高LAeq_db": round(highest, 1),
        "最高出现时刻_s": round(highest_at, 1),
        "P90_LAeq_db": round(p90, 1),
        "波动范围_db": round(highest - min(numbers), 1),
    }


def _analysis_digest(run: AnalysisRun, result: dict, redact: bool = True) -> dict:
    audio = result.get("audio") or {}
    overall = result.get("overall") or {}
    quality = result.get("quality") or {}
    calibration = result.get("calibration") or {}
    low_frequency = result.get("low_frequency") or {}
    z_bands = (result.get("bands") or {}).get("z_weighted") or []
    a_bands = {
        float(band["fc"]): band.get("level")
        for band in ((result.get("bands") or {}).get("a_weighted") or [])
        if band.get("fc") is not None
    }
    return {
        "场景": "单次低频噪声分析",
        "文件": REDACTED if redact else run.audio_file.original_filename,
        "整改阶段": run.stage or "未标记",
        "算法版本": run.algorithm_version,
        "摘要脱敏": redact,
        "音频": {
            "采样率_hz": audio.get("sample_rate"),
            "声道": audio.get("channels"),
            "时长_s": _round(audio.get("duration_s"), 2),
        },
        "校准": {
            "已校准": bool(quality.get("calibrated")),
            "修正量_db": calibration.get("offset_db"),
        },
        "总声级": {
            "LZeq": overall.get("lzeq"),
            "LAeq": overall.get("laeq"),
            "LCeq": overall.get("lceq"),
            "C_minus_A": overall.get("c_minus_a"),
        },
        "低频占比": {
            "频段_hz": f"{low_frequency.get('low_hz')}–{low_frequency.get('high_hz')}",
            "能量占比": low_frequency.get("ratio"),
            "低频声级_db": low_frequency.get("low_level_db"),
            "全频带声级_db": low_frequency.get("total_level_db"),
            "差值_db": low_frequency.get("difference_db"),
        },
        "低频三分之一倍频程_Z计权": [
            {"中心频率_hz": band.get("fc"), "声级_db": band.get("level")}
            for band in z_bands
            if band.get("fc") is not None and band["fc"] <= 200
        ],
        "低频三分之一倍频程_A计权": [
            {"中心频率_hz": fc, "声级_db": level}
            for fc, level in sorted(a_bands.items())
            if fc <= 200
        ],
        "低频峰值": [
            {
                "中心频率_hz": peak.get("fc"),
                "声级_db": peak.get("level"),
                "突出度_db": peak.get("prominence"),
            }
            for peak in (result.get("peaks") or [])[:8]
        ],
        "谐波": (result.get("harmonics") or [])[:3],
        "时间序列统计": _time_series_stats(result),
        "数据质量": {
            "flags": quality.get("flags") or [],
            "削波比例": quality.get("clip_ratio"),
            "静音比例": quality.get("silence_ratio"),
            "RMS_dBFS": quality.get("rms_dbfs"),
        },
    }


def _stage_band_comparison(loaded: list[tuple[AnalysisRun, dict]]) -> dict | None:
    """按阶段聚合 1/3 倍频程，并以 before 为基准计算降低量与 ≥10 dB 判定。"""
    per_stage: dict[str, dict[float, list[float]]] = {}
    for run, result in loaded:
        if not run.stage:
            continue
        for fc, level in _band_levels(result).items():
            if fc > 200:
                continue
            per_stage.setdefault(run.stage, {}).setdefault(fc, []).append(level)
    if len(per_stage) < 2:
        return None

    averages = {
        stage: {fc: round(sum(values) / len(values), 1) for fc, values in bands.items()}
        for stage, bands in per_stage.items()
    }
    baseline = "before" if "before" in averages else next(iter(averages))
    comparisons: list[dict] = []
    for stage in STAGE_ORDER:
        if stage == baseline or stage not in averages:
            continue
        rows = []
        for fc in sorted(averages[baseline]):
            if fc not in averages[stage]:
                continue
            before = averages[baseline][fc]
            current = averages[stage][fc]
            reduction = round(before - current, 1)
            rows.append(
                {
                    "中心频率_hz": fc,
                    "基准_db": before,
                    "当前_db": current,
                    "降低_db": reduction,
                    "达到10db": reduction >= STAGE_IMPROVEMENT_DB,
                }
            )
        if rows:
            comparisons.append(
                {
                    "阶段": stage,
                    "频段": rows,
                    "明显改善频段_hz": [row["中心频率_hz"] for row in rows if row["达到10db"]],
                }
            )
    if not comparisons:
        return None
    return {
        "基准阶段": baseline,
        "判定阈值_db": STAGE_IMPROVEMENT_DB,
        "各阶段平均Z计权三分之一倍频程": [
            {
                "阶段": stage,
                "频段": [
                    {"中心频率_hz": fc, "声级_db": level}
                    for fc, level in sorted(averages[stage].items())
                ],
            }
            for stage in STAGE_ORDER
            if stage in averages
        ],
        "与基准比较": comparisons,
        "明显改善": {item["阶段"]: item["明显改善频段_hz"] for item in comparisons},
    }


def event_band_comparison(event: NoiseEvent) -> dict | None:
    """供事件摘要接口复用：读取关联分析并计算阶段频段差值。"""
    loaded = [
        (run, result)
        for run in sorted(event.analyses, key=lambda item: item.created_at)
        for result in [_load_result(run)]
        if result is not None
    ]
    return _stage_band_comparison(loaded)


def _analysis_user_prompt(digest: dict) -> str:
    return _ANALYSIS_INSTRUCTION + json.dumps(digest, ensure_ascii=False, indent=2)


def _event_digest(event: NoiseEvent, redact: bool = True) -> dict:
    loaded: list[tuple[AnalysisRun, dict]] = []
    stage_levels: dict[str, list[float]] = {}
    run_rows: list[dict] = []
    for run in sorted(event.analyses, key=lambda item: item.created_at):
        result = _load_result(run)
        if not result:
            continue
        loaded.append((run, result))
        overall = result.get("overall") or {}
        quality = result.get("quality") or {}
        laeq = overall.get("laeq")
        if laeq is not None and run.stage:
            stage_levels.setdefault(run.stage, []).append(float(laeq))
        run_rows.append(
            {
                "stage": run.stage or "未标记",
                "分析任务": run.id,
                "LZeq": overall.get("lzeq"),
                "LAeq": laeq,
                "LCeq": overall.get("lceq"),
                "C_minus_A": overall.get("c_minus_a"),
                "已校准": bool(quality.get("calibrated")),
                "质量标志": quality.get("flags") or [],
                "主要峰值_hz": [peak.get("fc") for peak in (result.get("peaks") or [])[:5]],
                "时长_s": _round((result.get("audio") or {}).get("duration_s"), 1),
            }
        )

    averages = {
        stage: round(sum(values) / len(values), 1)
        for stage, values in stage_levels.items()
        if values
    }
    return {
        "场景": "噪音事件整改前后对比",
        "摘要脱敏": redact,
        "事件": {
            "标题": REDACTED if redact else event.title,
            "位置": REDACTED if redact else event.location,
            "测量点": REDACTED if redact else event.measurement_point,
            "设备": REDACTED if redact else event.equipment,
            "备注": REDACTED if redact else event.notes,
            "状态": event.status,
        },
        "各阶段平均LAeq": averages,
        "服务端阶段频段对比": _stage_band_comparison(loaded),
        "关联分析": run_rows,
    }


def _event_user_prompt(digest: dict) -> str:
    return _EVENT_INSTRUCTION + json.dumps(digest, ensure_ascii=False, indent=2)


def _confidence_label(level: int) -> str:
    return ("low", "medium", "high")[max(0, min(2, level))]


def _analysis_confidence(result: dict) -> tuple[str, str]:
    """置信度只由数据质量规则决定，不采信模型自评。"""
    quality = result.get("quality") or {}
    flags = set(quality.get("flags") or [])
    calibrated = bool(quality.get("calibrated"))
    duration = float((result.get("audio") or {}).get("duration_s") or 0.0)

    level = 2
    reasons: list[str] = []
    if not calibrated or "uncalibrated" in flags:
        level = 0
        reasons.append("未校准，声级为相对值")
    severe = flags & {"clipping_suspected", "digital_silence"}
    if severe:
        level = 0
        reasons.append("存在 " + "、".join(sorted(severe)))
    if duration < SHORT_DURATION_S:
        level = 0
        reasons.append(f"时长仅 {duration:.1f} s，样本过短")
    elif duration < GOOD_DURATION_S:
        level = min(level, 1)
        reasons.append(f"时长 {duration:.1f} s，建议不少于 {GOOD_DURATION_S:.0f} s")
    if "dc_offset_detected" in flags and level > 1:
        level = 1
        reasons.append("存在直流偏移")
    if not (result.get("peaks") or []) and level > 1:
        level = 1
        reasons.append("未识别到突出的低频峰值")
    if not reasons:
        reasons.append("已校准、时长充足且无质量告警")
    return _confidence_label(level), "；".join(reasons)


def _event_confidence(loaded: list[tuple[AnalysisRun, dict]]) -> tuple[str, str]:
    if not loaded:
        return "low", "没有可用于分析的结果"
    issues: list[str] = []
    for run, result in loaded:
        label, basis = _analysis_confidence(result)
        if label == "low":
            issues.append(f"分析 {run.id}：{basis}")
    stages = {run.stage for run, _ in loaded if run.stage}
    if issues:
        return "low", "；".join(issues[:3])
    if {"before", "after"} <= stages:
        return "high", "全部分析已校准，且包含 before/after 完整阶段"
    return "medium", "数据质量良好，但缺少完整的 before/after 阶段"


_NUMBER_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def _collect_numbers(value, bucket: set[float]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        bucket.add(round(float(value), 1))
    elif isinstance(value, dict):
        for item in value.values():
            _collect_numbers(item, bucket)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_numbers(item, bucket)


def _validate_content(content: dict, digest: dict) -> list[str]:
    """校验 findings 引用的数值是否来自结果摘要；不匹配时给出告警。"""
    allowed: set[float] = set()
    _collect_numbers(digest, allowed)
    warnings: list[str] = []
    seen: set[str] = set()
    for index, finding in enumerate(content.get("findings") or [], start=1):
        if not isinstance(finding, dict):
            continue
        text = " ".join(
            str(finding.get(key) or "") for key in ("title", "detail", "evidence")
        )
        for token in _NUMBER_RE.findall(text):
            if token in seen:
                continue
            seen.add(token)
            try:
                number = float(token)
            except ValueError:
                continue
            # 小于 12 的整数多为计数、阶段序或“10 dB 阈值”，不做强校验
            if abs(number) < 12 and number == int(number):
                continue
            if any(abs(number - value) <= NUMBER_TOLERANCE for value in allowed):
                continue
            warnings.append(f"第 {index} 条结论引用的数值 {token} 未在分析结果中找到，请核对")
    return warnings


def _prompt_hash() -> str:
    payload = "\n".join(
        [PROMPT_VERSION, _SYSTEM_PROMPT, _ANALYSIS_INSTRUCTION, _EVENT_INSTRUCTION]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _result_hash(digest: dict) -> str:
    payload = json.dumps(digest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _cache_key(result_hash: str, model: str, prompt_hash: str) -> str:
    payload = f"{result_hash}|{model}|{prompt_hash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def monthly_usage(session=None) -> dict:
    """统计当月成功解读的 token 用量与预算使用情况。"""
    own_session = session is None
    db = session or get_session_factory()()
    try:
        now = _now()
        month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        totals = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for model in (AnalysisInsight, EventInsight):
            row = db.execute(
                select(
                    func.count(model.id),
                    func.coalesce(func.sum(model.prompt_tokens), 0),
                    func.coalesce(func.sum(model.completion_tokens), 0),
                    func.coalesce(func.sum(model.total_tokens), 0),
                ).where(model.status == "succeeded", model.finished_at >= month_start)
            ).one()
            totals["requests"] += int(row[0] or 0)
            totals["prompt_tokens"] += int(row[1] or 0)
            totals["completion_tokens"] += int(row[2] or 0)
            totals["total_tokens"] += int(row[3] or 0)

        budget = llm_config.load_runtime_config(db).monthly_token_budget
        remaining = None if budget <= 0 else max(0, budget - totals["total_tokens"])
        used_percent = None if budget <= 0 else round(totals["total_tokens"] / budget * 100, 1)
        return {
            "month": now.strftime("%Y-%m"),
            **totals,
            "budget": budget,
            "remaining_tokens": remaining,
            "used_percent": used_percent,
            "exceeded": bool(budget > 0 and totals["total_tokens"] >= budget),
        }
    finally:
        if own_session:
            db.close()


def _budget_check() -> None:
    usage = monthly_usage()
    if usage["exceeded"]:
        raise InsightBudgetExceeded(
            f"本月 AI 解读 token 用量已达预算上限（{usage['total_tokens']}/{usage['budget']}），"
            "请调高预算或等待下月重置"
        )


def _mark_failed(session, insight, error: str) -> None:
    try:
        session.rollback()
        fresh = session.get(type(insight), insight.id)
        if fresh is not None and fresh.status != "cancelled":
            fresh.status = "failed"
            fresh.error = error[:1000]
            fresh.finished_at = _now()
            session.commit()
    except Exception:  # noqa: BLE001 - 落库失败不应掩盖原始错误
        session.rollback()
        logger.exception("failed to persist insight failure state")


def _add_version(
    session,
    *,
    content: dict,
    model: str,
    prompt_version: str,
    usage: dict,
    elapsed_ms: int,
    run_id: str | None = None,
    event_id: str | None = None,
) -> None:
    session.add(
        InsightVersion(
            run_id=run_id,
            event_id=event_id,
            model=model,
            prompt_version=prompt_version,
            content=content,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            elapsed_ms=elapsed_ms,
        )
    )


def _generate_analysis(run_id: str) -> None:
    session = get_session_factory()()
    insight: AnalysisInsight | None = None
    try:
        run = session.get(AnalysisRun, run_id)
        if run is None:
            return
        insight = session.scalars(
            select(AnalysisInsight).where(AnalysisInsight.run_id == run_id)
        ).first()
        if insight is None or insight.status == "cancelled":
            return

        if run.status != "succeeded":
            insight.status = "failed"
            insight.error = "分析尚未成功完成，无法生成解读"
            insight.finished_at = _now()
            session.commit()
            return
        result = _load_result(run)
        if result is None:
            insight.status = "failed"
            insight.error = "分析结果文件缺失，无法生成解读"
            insight.finished_at = _now()
            session.commit()
            return

        config = llm_config.load_runtime_config()
        digest = _analysis_digest(run, result, redact=config.redact_context)
        prompt_hash = _prompt_hash()
        result_hash = _result_hash(digest)
        cache_key = _cache_key(result_hash, config.model, prompt_hash)

        insight.status = "running"
        insight.model = config.model
        insight.prompt_version = PROMPT_VERSION
        insight.prompt_hash = prompt_hash
        insight.result_hash = result_hash
        insight.cache_key = cache_key
        insight.cache_hit = False
        insight.error = None
        insight.started_at = _now()
        session.commit()

        started = time.monotonic()
        with llm.capture_usage() as usage:
            raw = llm.chat_json(_SYSTEM_PROMPT, _analysis_user_prompt(digest))
        elapsed_ms = int((time.monotonic() - started) * 1000)

        # 模型返回后重新读取状态：期间可能被用户取消
        fresh = session.get(AnalysisInsight, insight.id, populate_existing=True)
        if fresh is None or fresh.status == "cancelled":
            logger.info("analysis insight %s cancelled before persist", run_id)
            return
        insight = fresh

        timeout_s = get_settings().llm_insight_timeout_s
        if timeout_s > 0 and elapsed_ms > timeout_s * 1000:
            raise TimeoutError(f"生成超时：耗时 {elapsed_ms / 1000:.1f} s，超过上限 {timeout_s:.0f} s")

        content = InsightContent.model_validate(raw).model_dump()
        confidence, basis = _analysis_confidence(result)
        content["confidence"] = confidence
        content["confidence_basis"] = basis
        content["validation_warnings"] = _validate_content(content, digest)

        insight.content = content
        insight.status = "succeeded"
        insight.error = None
        insight.prompt_tokens = int(usage.get("prompt_tokens") or 0)
        insight.completion_tokens = int(usage.get("completion_tokens") or 0)
        insight.total_tokens = int(usage.get("total_tokens") or 0)
        insight.elapsed_ms = elapsed_ms
        insight.finished_at = _now()
        _add_version(
            session,
            run_id=run_id,
            content=content,
            model=insight.model,
            prompt_version=PROMPT_VERSION,
            usage=usage,
            elapsed_ms=elapsed_ms,
        )
        session.commit()
        logger.info(
            "analysis insight %s generated (tokens=%s, %s ms)",
            run_id,
            insight.total_tokens,
            elapsed_ms,
        )
    except Exception as exc:  # noqa: BLE001 - 后台任务需显式落库失败原因
        logger.exception("analysis insight %s failed", run_id)
        if insight is not None:
            _mark_failed(session, insight, f"{type(exc).__name__}: {exc}")
    finally:
        session.close()


def _generate_event(event_id: str) -> None:
    session = get_session_factory()()
    insight: EventInsight | None = None
    try:
        event = session.get(NoiseEvent, event_id)
        if event is None:
            return
        insight = session.scalars(
            select(EventInsight).where(EventInsight.event_id == event_id)
        ).first()
        if insight is None or insight.status == "cancelled":
            return

        config = llm_config.load_runtime_config()
        digest = _event_digest(event, redact=config.redact_context)
        if not digest["关联分析"]:
            insight.status = "failed"
            insight.error = "事件还没有可用的分析结果，无法生成解读"
            insight.finished_at = _now()
            session.commit()
            return

        prompt_hash = _prompt_hash()
        result_hash = _result_hash(digest)
        cache_key = _cache_key(result_hash, config.model, prompt_hash)

        insight.status = "running"
        insight.model = config.model
        insight.prompt_version = PROMPT_VERSION
        insight.prompt_hash = prompt_hash
        insight.result_hash = result_hash
        insight.cache_key = cache_key
        insight.cache_hit = False
        insight.error = None
        insight.started_at = _now()
        session.commit()

        started = time.monotonic()
        with llm.capture_usage() as usage:
            raw = llm.chat_json(_SYSTEM_PROMPT, _event_user_prompt(digest))
        elapsed_ms = int((time.monotonic() - started) * 1000)

        fresh = session.get(EventInsight, insight.id, populate_existing=True)
        if fresh is None or fresh.status == "cancelled":
            logger.info("event insight %s cancelled before persist", event_id)
            return
        insight = fresh

        timeout_s = get_settings().llm_insight_timeout_s
        if timeout_s > 0 and elapsed_ms > timeout_s * 1000:
            raise TimeoutError(f"生成超时：耗时 {elapsed_ms / 1000:.1f} s，超过上限 {timeout_s:.0f} s")

        content = InsightContent.model_validate(raw).model_dump()
        loaded = [
            (run, result)
            for run in sorted(event.analyses, key=lambda item: item.created_at)
            for result in [_load_result(run)]
            if result is not None
        ]
        confidence, basis = _event_confidence(loaded)
        content["confidence"] = confidence
        content["confidence_basis"] = basis
        content["validation_warnings"] = _validate_content(content, digest)

        insight.content = content
        insight.status = "succeeded"
        insight.error = None
        insight.prompt_tokens = int(usage.get("prompt_tokens") or 0)
        insight.completion_tokens = int(usage.get("completion_tokens") or 0)
        insight.total_tokens = int(usage.get("total_tokens") or 0)
        insight.elapsed_ms = elapsed_ms
        insight.finished_at = _now()
        _add_version(
            session,
            event_id=event_id,
            content=content,
            model=insight.model,
            prompt_version=PROMPT_VERSION,
            usage=usage,
            elapsed_ms=elapsed_ms,
        )
        session.commit()
        logger.info(
            "event insight %s generated (tokens=%s, %s ms)",
            event_id,
            insight.total_tokens,
            elapsed_ms,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("event insight %s failed", event_id)
        if insight is not None:
            _mark_failed(session, insight, f"{type(exc).__name__}: {exc}")
    finally:
        session.close()


def _reset_record(insight, model: str, prompt_hash: str) -> None:
    insight.status = "queued"
    insight.model = model
    insight.prompt_version = PROMPT_VERSION
    insight.prompt_hash = prompt_hash
    insight.content = None
    insight.error = None
    insight.cache_hit = False
    insight.started_at = None
    insight.finished_at = None
    insight.prompt_tokens = 0
    insight.completion_tokens = 0
    insight.total_tokens = 0
    insight.elapsed_ms = 0


def queue_analysis_insight(run_id: str, force: bool = False) -> AnalysisInsight:
    """创建或重置解读记录并提交后台执行；排队中/运行中直接复用，避免重复调用。"""
    session = get_session_factory()()
    try:
        run = session.get(AnalysisRun, run_id)
        if run is None:
            raise InsightQueueError("分析任务不存在")
        if run.status != "succeeded":
            raise InsightQueueError("分析尚未成功完成，无法生成解读")
        result = _load_result(run)
        if result is None:
            raise InsightQueueError("分析结果文件缺失，无法生成解读")

        config = llm_config.load_runtime_config()
        digest = _analysis_digest(run, result, redact=config.redact_context)
        prompt_hash = _prompt_hash()
        result_hash = _result_hash(digest)
        cache_key = _cache_key(result_hash, config.model, prompt_hash)

        insight = session.scalars(
            select(AnalysisInsight).where(AnalysisInsight.run_id == run_id)
        ).first()
        if insight is not None and not force:
            if insight.status in {"queued", "running"}:
                return insight
            if insight.status == "succeeded" and insight.cache_key == cache_key:
                insight.cache_hit = True
                session.commit()
                return insight

        _budget_check()
        if insight is None:
            insight = AnalysisInsight(run_id=run_id)
            session.add(insight)
        _reset_record(insight, config.model, prompt_hash)
        insight.result_hash = result_hash
        insight.cache_key = cache_key
        session.commit()
        session.refresh(insight)
        _get_executor().submit(_generate_analysis, run_id)
        return insight
    finally:
        session.close()


def queue_event_insight(event_id: str, force: bool = False) -> EventInsight:
    session = get_session_factory()()
    try:
        event = session.get(NoiseEvent, event_id)
        if event is None:
            raise InsightQueueError("事件不存在")

        config = llm_config.load_runtime_config()
        digest = _event_digest(event, redact=config.redact_context)
        if not digest["关联分析"]:
            raise InsightQueueError("事件还没有可用的分析结果，无法生成解读")

        prompt_hash = _prompt_hash()
        result_hash = _result_hash(digest)
        cache_key = _cache_key(result_hash, config.model, prompt_hash)

        insight = session.scalars(
            select(EventInsight).where(EventInsight.event_id == event_id)
        ).first()
        if insight is not None and not force:
            if insight.status in {"queued", "running"}:
                return insight
            if insight.status == "succeeded" and insight.cache_key == cache_key:
                insight.cache_hit = True
                session.commit()
                return insight

        _budget_check()
        if insight is None:
            insight = EventInsight(event_id=event_id)
            session.add(insight)
        _reset_record(insight, config.model, prompt_hash)
        insight.result_hash = result_hash
        insight.cache_key = cache_key
        session.commit()
        session.refresh(insight)
        _get_executor().submit(_generate_event, event_id)
        return insight
    finally:
        session.close()


def cancel_analysis_insight(run_id: str) -> AnalysisInsight:
    return _cancel_record(AnalysisInsight, "run_id", run_id)


def cancel_event_insight(event_id: str) -> EventInsight:
    return _cancel_record(EventInsight, "event_id", event_id)


def _cancel_record(model, field: str, value: str):
    session = get_session_factory()()
    try:
        record = session.scalars(select(model).where(getattr(model, field) == value)).first()
        if record is None:
            raise InsightQueueError("尚未生成解读，无法取消")
        if record.status not in {"queued", "running"}:
            raise InsightQueueError("当前状态不可取消")
        record.status = "cancelled"
        record.error = "已由用户取消"
        record.finished_at = _now()
        session.commit()
        session.refresh(record)
        return record
    finally:
        session.close()


def save_analysis_feedback(run_id: str, rating: str, note: str = "") -> AnalysisInsight:
    return _save_feedback(AnalysisInsight, "run_id", run_id, rating, note)


def save_event_feedback(event_id: str, rating: str, note: str = "") -> EventInsight:
    return _save_feedback(EventInsight, "event_id", event_id, rating, note)


def _save_feedback(model, field: str, value: str, rating: str, note: str):
    session = get_session_factory()()
    try:
        record = session.scalars(select(model).where(getattr(model, field) == value)).first()
        if record is None or record.status != "succeeded":
            raise InsightQueueError("解读尚未生成，无法提交反馈")
        record.feedback = rating
        record.feedback_note = note.strip()[:500]
        record.feedback_at = _now()
        session.commit()
        session.refresh(record)
        return record
    finally:
        session.close()


def analysis_insight_history(run_id: str, limit: int = 20) -> list[InsightVersion]:
    return _history(AnalysisInsight, "run_id", run_id, limit)


def event_insight_history(event_id: str, limit: int = 20) -> list[InsightVersion]:
    return _history(EventInsight, "event_id", event_id, limit)


def _history(model, field: str, value: str, limit: int) -> list[InsightVersion]:
    session = get_session_factory()()
    try:
        stmt = (
            select(InsightVersion)
            .where(getattr(InsightVersion, field) == value)
            .order_by(InsightVersion.created_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        return list(session.scalars(stmt))
    finally:
        session.close()


def submit_analysis_insight(run_id: str) -> None:
    """自动触发入口：仅排队执行，不等待模型返回。"""
    try:
        queue_analysis_insight(run_id)
    except InsightQueueError as exc:
        logger.warning("auto insight skipped for %s: %s", run_id, exc)


def recover_stale_insights() -> int:
    """启动时把上次进程中断的解读标记为失败，避免前端一直显示处理中。"""
    session = get_session_factory()()
    try:
        stale = list(
            session.scalars(
                select(AnalysisInsight).where(AnalysisInsight.status.in_(("queued", "running")))
            )
        ) + list(
            session.scalars(select(EventInsight).where(EventInsight.status.in_(("queued", "running"))))
        )
        for item in stale:
            item.status = "failed"
            item.error = "服务重启导致任务中断，请重新生成"
            item.finished_at = _now()
        if stale:
            session.commit()
        return len(stale)
    except Exception:  # noqa: BLE001
        session.rollback()
        logger.exception("failed to recover stale insights")
        return 0
    finally:
        session.close()
