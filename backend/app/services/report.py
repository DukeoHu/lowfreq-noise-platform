"""排查报告 PDF 生成。

只读取一次分析已经落库的结果 JSON，不触发任何重新计算，保证报告与页面数据一致。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# 报告面向国内用户，固定 UTC+8（中国不实行夏令时，无需 zoneinfo/tzdata 依赖）
LOCAL_TZ = timezone(timedelta(hours=8))

FLAG_LABELS: dict[str, str] = {
    "uncalibrated": "未校准，读数为相对值",
    "clipping_suspected": "疑似削波",
    "dc_offset_detected": "存在直流偏移",
    "digital_silence": "数字静音",
    "mostly_silent": "大部分时段静音",
    "short_duration": "录音过短",
    "multichannel_downmixed": "多声道已混为单声道",
}

_FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("SimSun", "C:/Windows/Fonts/simsun.ttc"),
    ("MicrosoftYaHei", "C:/Windows/Fonts/msyh.ttc"),
    ("PingFangSC", "/System/Library/Fonts/PingFang.ttc"),
    ("NotoSansCJK", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
)

_ACCENT = colors.HexColor("#1d4ed8")
_MUTED = colors.HexColor("#5b6675")
_HEADER_BG = colors.HexColor("#e8eeff")
_GRID = colors.HexColor("#e3e6ea")


def _register_font() -> str:
    """优先使用系统中文字体；失败时回退到 reportlab 内置的 CID 宋体。"""
    for name, path in _FONT_CANDIDATES:
        font_file = Path(path)
        if not font_file.exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, str(font_file), subfontIndex=0))
            return name
        except Exception:  # noqa: BLE001 - 单个字体不可用不应影响出报告
            continue
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light"
    except Exception:  # noqa: BLE001
        return "Helvetica"


_FONT = _register_font()

_STYLES = {
    "title": ParagraphStyle("Title", fontName=_FONT, fontSize=20, leading=26, textColor=_ACCENT, spaceAfter=2),
    "subtitle": ParagraphStyle("Subtitle", fontName=_FONT, fontSize=10, leading=14, textColor=_MUTED),
    "heading": ParagraphStyle("Heading", fontName=_FONT, fontSize=13, leading=18, textColor=colors.black, spaceBefore=14, spaceAfter=6),
    "body": ParagraphStyle("Body", fontName=_FONT, fontSize=10, leading=15),
    "note": ParagraphStyle("Note", fontName=_FONT, fontSize=9, leading=13, textColor=_MUTED),
}


def _fmt(value: float | None, unit: str = " dB", digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}{unit}"


def _to_local(moment: datetime | None) -> str:
    if moment is None:
        return "—"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _kv_table(rows: list[tuple[str, str]]) -> Table:
    data = [[Paragraph(escape(key), _STYLES["body"]), Paragraph(escape(value), _STYLES["body"])] for key, value in rows]
    table = Table(data, colWidths=[105, 380])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), _FONT),
                ("TEXTCOLOR", (0, 0), (0, -1), _MUTED),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, _GRID),
            ]
        )
    )
    return table


def _data_table(header: list[str], rows: list[list[str]], col_widths: list[float]) -> Table:
    data = [[Paragraph(escape(cell), _STYLES["body"]) for cell in header]]
    data += [[Paragraph(escape(cell), _STYLES["body"]) for cell in row] for row in rows]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), _FONT),
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#16202c")),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, _GRID),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def build_report_pdf(
    *,
    run_id: str,
    audio_filename: str,
    created_at: datetime | None,
    algorithm_version: str,
    result: dict,
    config: dict | None = None,
    insight: dict | None = None,
) -> bytes:
    """把一个已完成分析的 result JSON 渲染成 PDF 字节。"""
    quality = result.get("quality") or {}
    overall = result.get("overall") or {}
    calibration = result.get("calibration") or {}
    audio = result.get("audio") or {}
    flags: list[str] = list(quality.get("flags") or [])
    calibrated = bool(quality.get("calibrated"))

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"低频噪音排查报告 {run_id}",
        author="低频噪音 Web 平台",
    )

    local_now = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M")

    def draw_footer(canvas, _doc) -> None:
        canvas.saveState()
        canvas.setFont(_FONT, 8)
        canvas.setFillColor(_MUTED)
        canvas.drawString(18 * mm, 10 * mm, "低频噪音 Web 平台 · 自动生成报告")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"第 {canvas.getPageNumber()} 页")
        canvas.restoreState()

    story: list = [
        Paragraph("低频噪音排查报告", _STYLES["title"]),
        Paragraph(f"生成时间 {local_now} · 算法版本 {escape(algorithm_version)}", _STYLES["subtitle"]),
        Spacer(1, 10),
        _kv_table(
            [
                ("分析任务", run_id),
                ("音频文件", audio_filename),
                ("任务创建时间", _to_local(created_at)),
                (
                    "分析配置",
                    f"帧长 {config.get('frame_s', '—')} s · 步长 {config.get('hop_s', '—')} s · "
                    f"FFT {config.get('fft_size', '—')}",
                )
                if config
                else ("分析配置", "—"),
                ("采样率 / 声道", f"{audio.get('sample_rate', '—')} Hz / {audio.get('channels', '—')}"),
                ("录音时长", _fmt(audio.get("duration_s"), " s", 2)),
                (
                    "校准状态",
                    "已校准（修正量 "
                    f"{_fmt(calibration.get('offset_db'), ' dB', 1)}）"
                    if calibrated
                    else "未校准（相对值）",
                ),
            ]
        ),
        Paragraph("总声级", _STYLES["heading"]),
        _data_table(
            ["LZeq", "LAeq", "LCeq", "C − A"],
            [[_fmt(overall.get("lzeq")), _fmt(overall.get("laeq")), _fmt(overall.get("lceq")), _fmt(overall.get("c_minus_a"))]],
            [120, 120, 120, 120],
        ),
        Paragraph("1/3 倍频程声级（≤ 300 Hz）", _STYLES["heading"]),
    ]

    z_bands = list((result.get("bands") or {}).get("z_weighted") or [])
    a_by_fc = {item["fc"]: item.get("level") for item in (result.get("bands") or {}).get("a_weighted") or []}
    band_rows = [
        [f"{band['fc']:g}", _fmt(band.get("level")), _fmt(a_by_fc.get(band["fc"]))]
        for band in z_bands
        if band["fc"] <= 300
    ]
    if band_rows:
        story.append(_data_table(["中心频率 (Hz)", "Z 计权 (dB)", "A 计权 (dB)"], band_rows, [150, 150, 150]))
    else:
        story.append(Paragraph("本次分析没有可用的低频 1/3 倍频程数据。", _STYLES["body"]))

    story.append(Paragraph("低频峰值", _STYLES["heading"]))
    peaks = result.get("peaks") or []
    if peaks:
        story.append(
            _data_table(
                ["中心频率 (Hz)", "声级 (dB)", "突出度 (dB)"],
                [
                    [f"{peak['fc']:g}", _fmt(peak.get("level")), _fmt(peak.get("prominence"))]
                    for peak in peaks
                ],
                [150, 150, 150],
            )
        )
    else:
        story.append(Paragraph("未在 300 Hz 以下检测到显著局部峰值。", _STYLES["body"]))

    story.append(Paragraph("数据质量", _STYLES["heading"]))
    if flags:
        flag_text = "；".join(FLAG_LABELS.get(flag, flag) for flag in flags)
    else:
        flag_text = "未发现异常质量标志"
    story.append(Paragraph(escape(flag_text), _STYLES["body"]))
    story.append(
        Paragraph(
            f"削波比例 {_fmt(quality.get('clip_ratio', 0) * 100, '%', 2)} · "
            f"静音比例 {_fmt(quality.get('silence_ratio', 0) * 100, '%', 1)} · "
            f"RMS {_fmt(quality.get('rms_dbfs'), ' dBFS')}",
            _STYLES["note"],
        )
    )

    if insight:
        story.append(Paragraph("AI 辅助解读（仅供参考）", _STYLES["heading"]))
        summary = str(insight.get("summary") or "").strip()
        if summary:
            story.append(Paragraph(escape(summary), _STYLES["body"]))
        for finding in insight.get("findings") or []:
            title = str(finding.get("title") or "").strip()
            detail = str(finding.get("detail") or "").strip()
            evidence = str(finding.get("evidence") or "").strip()
            text = f"{title}：{detail}" if title and detail else (title or detail)
            if evidence:
                text = f"{text}（依据：{evidence}）" if text else f"依据：{evidence}"
            if text:
                story.append(Paragraph(escape(text), _STYLES["body"]))
        warnings = [str(item) for item in insight.get("validation_warnings") or [] if str(item).strip()]
        for item in warnings:
            story.append(Paragraph(escape(f"数值校验：{item}"), _STYLES["note"]))
        recommendations = [str(item) for item in insight.get("recommendations") or [] if str(item).strip()]
        if recommendations:
            story.append(Paragraph("排查与整改建议", _STYLES["body"]))
            for index, item in enumerate(recommendations, start=1):
                story.append(Paragraph(escape(f"{index}. {item}"), _STYLES["body"]))
        caveats = [str(item) for item in insight.get("caveats") or [] if str(item).strip()]
        for item in caveats:
            story.append(Paragraph(escape(item), _STYLES["note"]))
        story.append(
            Paragraph(
                "本节由大模型基于上述指标生成，仅用于排查参考，不构成检测、执法或验收结论。",
                _STYLES["note"],
            )
        )

    story.append(Paragraph("说明与免责", _STYLES["heading"]))
    notes = [
        "声级按 IEC 61672 频率计权在频域计算，1 kHz 处归零；总级与 1/3 倍频程结果均来自同一次 Welch 功率谱。",
    ]
    if not calibrated:
        notes.append("本次分析未使用校准数据，所有声级均为相对值，不能作为法定检测或执法结论的依据。")
    notes.append(
        f"本报告由低频噪音 Web 平台自动生成（算法版本 {algorithm_version}），"
        "结果可复现，请与原始音频及分析任务 ID 一并归档。"
    )
    for note in notes:
        story.append(Paragraph(escape(note), _STYLES["note"]))

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buffer.getvalue()
