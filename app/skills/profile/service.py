import csv
import io
import json

from database import LearningDatabase


def _decode(rows: list[dict], fields: tuple[str, ...]) -> list[dict]:
    for row in rows:
        for field in fields:
            row[field] = json.loads(row.get(field) or "[]")
    return rows


def get_learning_profile(db: LearningDatabase, limit: int = 10) -> dict:
    questions = db.fetch_all("SELECT * FROM questions ORDER BY id DESC LIMIT ?", (limit,))
    attempts = db.fetch_all("SELECT * FROM practice_attempts ORDER BY id DESC LIMIT ?", (limit,))
    weak = db.fetch_all("""SELECT *, ROUND(100.0*(answered-correct)/NULLIF(answered,0),1) AS error_rate
                           FROM weak_points
                           ORDER BY weakness_score DESC, answered DESC""")
    return {
        "questions": _decode(questions, ("sources_json", "knowledge_points_json")),
        "attempts": _decode(attempts, ("wrong_items_json", "records_json", "knowledge_points_json")),
        "weak_points": _decode(weak, ("recent_json",)),
    }


def recommend_practice(profile: dict, max_topics: int = 3) -> dict:
    eligible = [row for row in profile.get("weak_points", [])
                if row.get("level") in {"弱", "中"} and row.get("answered", 0) >= 3]
    selected = eligible[:max_topics]
    if not selected:
        return {"topics": [], "message": "当前数据不足，建议先完成一轮均衡基础练习。"}
    topics = [row["knowledge_point"] for row in selected]
    difficulty = "简单+中等" if any(row["level"] == "弱" for row in selected) else "中等"
    return {
        "topics": topics,
        "difficulty": difficulty,
        "count": 3,
        "message": f"建议下一轮优先练习：{'、'.join(topics)}（{difficulty}）。",
    }


def export_csv_bytes(profile: dict) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["类型", "时间", "内容", "成绩", "知识点"])
    for row in profile["questions"]:
        writer.writerow(["提问", row["created_at"], row["question"], "", "、".join(row["knowledge_points_json"])])
    for row in profile["attempts"]:
        writer.writerow(["练习", row["created_at"], f"错题 {len(row['wrong_items_json'])} 道",
                         f"{row['score']}分", "、".join(row["knowledge_points_json"])])
    for row in profile["weak_points"]:
        writer.writerow(["薄弱知识点", row["updated_at"], row["knowledge_point"],
                         f"薄弱度 {row['weakness_score']:.2f}", row["level"]])
    return ("\ufeff" + output.getvalue()).encode("utf-8")
