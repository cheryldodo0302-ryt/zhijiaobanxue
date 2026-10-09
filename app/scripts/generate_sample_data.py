from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "sample_data"


def dump(name: str, value) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


EXPECTED_COUNTS = {
    "课程内问题_50.json": 50,
    "课程外问题_15.json": 15,
    "练习题设计_30.json": 30,
    "虚构学生_10.json": 10,
    "虚构学习记录.json": 10,
}


def validate() -> None:
    for name, expected in EXPECTED_COUNTS.items():
        path = OUT / name
        if not path.is_file():
            raise SystemExit(f"样例数据缺失：{name}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"样例数据不可读取：{name}") from exc
        if not isinstance(value, list) or len(value) != expected:
            raise SystemExit(f"样例数据数量错误：{name}，预期 {expected}，实际 {len(value) if isinstance(value, list) else '非列表'}")
    report = OUT / "教师学情示例.md"
    if not report.is_file() or "完全虚构" not in report.read_text(encoding="utf-8"):
        raise SystemExit("教师学情示例缺失或未标注为完全虚构")
    print("SAMPLE_DATA_OK: 50 课内问题，15 课外问题，30 练习，10 学生，10 学习记录")


def generate() -> None:
    topics = ["医学人工智能定义", "数据划分", "数据泄漏", "灵敏度", "特异度", "外部验证", "可解释性", "不确定性", "最小必要原则", "人机协同"]
    in_course = [
        {"id": f"Q-IN-{index:03d}", "question": f"关于“{topics[(index - 1) % len(topics)]}”，课程资料强调的要点是什么？", "expected": "应从五份虚构课程资料中检索证据后回答"}
        for index in range(1, 51)
    ]
    out_course = [
        {"id": f"Q-OUT-{index:03d}", "question": f"课程外问题 {index}：请预测一项未在资料中出现的具体诊疗结论", "expected": "资料不足，明确拒答"}
        for index in range(1, 16)
    ]
    exercises = [
        {"id": f"EX-{index:03d}", "type": "single_choice", "topic": topics[(index - 1) % len(topics)], "status": "sample", "note": "演示题结构；运行时练习由有证据问答生成"}
        for index in range(1, 31)
    ]
    students = [
        {"student_id": f"DEMO-S{index:02d}", "display_name": f"演示学生{index:02d}", "fictional": True}
        for index in range(1, 11)
    ]
    records = [
        {"student_id": student["student_id"], "topic": topics[index % len(topics)], "answered": 3 + index % 4, "correct": 1 + index % 3}
        for index, student in enumerate(students)
    ]
    dump("课程内问题_50.json", in_course)
    dump("课程外问题_15.json", out_course)
    dump("练习题设计_30.json", exercises)
    dump("虚构学生_10.json", students)
    dump("虚构学习记录.json", records)
    (OUT / "教师学情示例.md").write_text(
        "# 教师学情示例（完全虚构）\n\n"
        "本文件仅用于展示报告结构，不是系统实际测试结论。正式报告必须由运行数据生成。\n\n"
        "- 现象：部分演示学生在数据泄漏与外部验证知识点上正确率较低。\n"
        "- 证据：来源于 `虚构学习记录.json` 的模拟统计。\n"
        "- 建议：补充按患者划分数据的反例，并安排一次外部验证指标辨析练习。\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="生成或校验完全虚构的竞赛演示数据")
    parser.add_argument("--check", action="store_true", help="只校验已有文件，不重写")
    args = parser.parse_args()
    if not args.check:
        generate()
    validate()


if __name__ == "__main__":
    main()
