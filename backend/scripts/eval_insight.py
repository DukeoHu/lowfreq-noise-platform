"""AI 解读离线评测：用固定小样本检查结构完整性与数值一致性。

用法（在 backend 目录下）：
    .venv/Scripts/python.exe scripts/eval_insight.py           # 仅回放样本，不联网
    .venv/Scripts/python.exe scripts/eval_insight.py --live    # 调用当前配置的模型

样本位于 tests/fixtures/insight_cases/*.json，结构为：
    {"name": "...", "digest": {...}, "response": {...}}
回放模式校验样本 response；--live 模式把 digest 发给模型并校验实际输出。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.schemas.insight import InsightContent  # noqa: E402
from app.services import insight as insight_service  # noqa: E402
from app.services import llm  # noqa: E402

CASES_DIR = BACKEND_ROOT / "tests" / "fixtures" / "insight_cases"


def _load_cases() -> list[dict]:
    cases: list[dict] = []
    for path in sorted(CASES_DIR.glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        payload.setdefault("name", path.stem)
        cases.append(payload)
    return cases


def _check(case: dict, response: dict) -> list[str]:
    problems: list[str] = []
    if not isinstance(response, dict):
        return ["模型输出不是 JSON 对象"]
    try:
        content = InsightContent.model_validate(response)
    except Exception as exc:  # noqa: BLE001 - 评测脚本需报告全部问题
        return [f"结构校验失败：{exc}"]
    if not content.summary.strip():
        problems.append("summary 为空")
    if not content.findings:
        problems.append("findings 为空")
    for index, finding in enumerate(content.findings, start=1):
        if not finding.evidence.strip():
            problems.append(f"第 {index} 条 findings 缺少 evidence")
    digest = case.get("digest") or {}
    problems.extend(insight_service._validate_content(content.model_dump(), digest))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 解读离线评测")
    parser.add_argument("--live", action="store_true", help="调用真实模型而不是回放样本")
    args = parser.parse_args()

    cases = _load_cases()
    if not cases:
        print(f"未找到样本：{CASES_DIR}")
        return 2

    failures = 0
    for case in cases:
        name = case.get("name")
        if args.live:
            digest = case.get("digest") or {}
            try:
                response = llm.chat_json(
                    insight_service._SYSTEM_PROMPT,
                    insight_service._ANALYSIS_INSTRUCTION
                    + json.dumps(digest, ensure_ascii=False, indent=2),
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[FAIL] {name}: 模型调用失败 {exc}")
                failures += 1
                continue
        else:
            response = case.get("response") or {}

        problems = _check(case, response)
        if problems:
            failures += 1
            print(f"[FAIL] {name}")
            for problem in problems:
                print(f"       - {problem}")
        else:
            print(f"[PASS] {name}")

    total = len(cases)
    print(f"\n{total - failures}/{total} 通过")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
