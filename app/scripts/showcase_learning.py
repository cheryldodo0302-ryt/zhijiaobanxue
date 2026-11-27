"""Fictional demonstration data, graded and aggregated by the real services.

Only the reserved showcase teacher's courses and students are touched. Dates,
question snapshots and request IDs are saved so rerunning never resets work.
"""
from __future__ import annotations

import hashlib
import io
import json
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import text

from auth_service import AuthService
from browser_study_room_service import BrowserStudyRoomService
from class_task_service import ClassTaskService, encode, stamp, timestamp, utc_now
from knowledge_graph_service import KnowledgeGraphService
from learning_events import record
from question_bank_service import QuestionBankService
from student_portrait_service import StudentPortraitService, METRICS_VERSION

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ZONE = timezone(timedelta(hours=8))


def anonymize_showcase(db, teacher_id):
    users = db.fetch_all("""SELECT DISTINCT u.* FROM users u JOIN class_memberships m ON m.student_id=u.user_id
        JOIN classes c USING(class_id) WHERE c.teacher_id=? AND u.role='student'
        AND NOT EXISTS(SELECT 1 FROM class_memberships x JOIN classes y USING(class_id)
                       WHERE x.student_id=u.user_id AND y.teacher_id<>?)""", (teacher_id, teacher_id))
    owner = db.fetch_one("SELECT * FROM users WHERE user_id=?", (teacher_id,))
    replacements = {u["display_name"]: u["display_name"][0] + "**" for u in users if u["display_name"]}
    if owner and owner["display_name"]:
        replacements[owner["display_name"]] = owner["display_name"][0] + "**（演示）"
    replacements.update({"2024级信管": "信管", "24级信管": "信管", "信息管理与信息系统": "信管", "校区A": "演示校区", "2024级": "20**级"})

    def masked(value):
        for old, new in replacements.items():
            if old != new:
                value = value.replace(old, new)
        return value

    # Read the old names before updating them; the map never needs to be exported.
    with db.connect() as conn:
        for user in users:
            conn.execute("UPDATE users SET display_name=? WHERE user_id=?", (user["display_name"][0] + "**", user["user_id"]))
        if owner:
            conn.execute("UPDATE users SET display_name=? WHERE user_id=?", (owner["display_name"][0] + "**（演示）", teacher_id))
        for row in db.fetch_all("SELECT * FROM classes WHERE teacher_id=?", (teacher_id,)):
            conn.execute("UPDATE classes SET class_name=?,campus='演示校区',cohort_year='20**',major='信管' WHERE class_id=?",
                         (masked(row["class_name"]), row["class_id"]))
        conn.execute("UPDATE teaching_archive_versions SET campus='演示校区',cohort_year='20**',major='信管' WHERE course_id IN (SELECT course_id FROM courses WHERE owner_id=?)", (teacher_id,))
        for row in db.fetch_all("SELECT course_id,description FROM courses WHERE owner_id=?", (teacher_id,)):
            conn.execute("UPDATE courses SET description=? WHERE course_id=?", (masked(row["description"]), row["course_id"]))
        for row in db.fetch_all("""SELECT i.* FROM teaching_archive_items i JOIN teaching_archive_versions v USING(version_id)
            JOIN courses c USING(course_id) WHERE c.owner_id=?""", (teacher_id,)):
            conn.execute("UPDATE teaching_archive_items SET content_markdown=?,structured_json=? WHERE item_id=?",
                         (masked(row["content_markdown"]), masked(row["structured_json"]), row["item_id"]))
    for doc in db.fetch_all("""SELECT d.* FROM teaching_archive_documents d JOIN teaching_archive_versions v USING(version_id)
        JOIN courses c USING(course_id) WHERE c.owner_id=?""", (teacher_id,)):
        path = Path(doc["stored_path"])
        if path.suffix != ".txt" or not path.is_file():
            continue
        old = path.read_bytes()
        if hashlib.sha256(old).hexdigest() != doc["sha256"]:
            continue  # Preserve a source edited outside the seed.
        new = masked(old.decode("utf-8")).encode("utf-8")
        if new != old:
            path.write_bytes(new)
            digest = hashlib.sha256(new).hexdigest()
            with db.connect() as conn:
                conn.execute("UPDATE teaching_archive_documents SET sha256=? WHERE archive_document_id=?", (digest, doc["archive_document_id"]))
                conn.execute("UPDATE teaching_archive_import_files SET sha256=?,size_bytes=? WHERE file_id=?", (digest, len(new), doc["file_id"]))


def workbook_bytes(sheets):
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets:
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(65, max(14, max(len(str(cell.value or "")) for cell in column) + 2))
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


MISCONCEPTIONS = {
    "DB2026": ["数据库管理系统只负责绘制界面，不负责数据访问控制。", "外键可以替代主键来保证每张表的每行都唯一。", "判断SQL中的空值时，应一律使用等于NULL。", "HAVING在分组之前筛选每一条原始记录。", "数据库规范化的目标是尽可能增加重复字段。", "事务提交后是否持久保存与事务特性无关。", "索引越多越好，不会增加任何写入成本。", "只要做过一次备份，就不需要验证是否能够恢复。"],
    "PY2026": ["虚拟环境会自动改变所有项目共享的依赖版本。", "任何字符串都可以无条件转换为整数。", "while循环执行次数只由代码行数决定，与条件无关。", "函数只能接收参数，不能返回计算结果。", "字典不需要键，可以直接用重复的值唯一定位数据。", "打开文件后不必考虑关闭资源，也不必指定文本编码。", "测试只需要覆盖正常输入，无须检查边界和错误情况。", "程序在开发电脑上运行过一次，就不需要提供依赖和运行说明。"],
    "AI2026": ["医学人工智能的任何输出都可以直接替代专业人员判断。", "样本越多就一定没有标签偏差和代表性问题。", "同一患者的不同记录随意分入训练集和测试集不会泄漏信息。", "灵敏度和特异度含义相同，可以无条件互换。", "训练准确率很高即可证明模型不存在过拟合。", "训练数据上的表现可以替代不同来源的外部验证。", "解释图能够证明预测一定正确，不必关注不确定性。", "只要模型效果好，就可以无限制收集和使用个人数据。"],
}


def seed_bank(db, campus, ingestion, teacher, course, code, topics):
    bank = QuestionBankService(db, campus)
    folder = next((f for f in bank.list_folders(teacher, course["course_id"]) if f["folder_name"] == "演示关联题库"), None)
    folder = folder or bank.create_folder(teacher, course["course_id"], "演示关联题库", "chapter")
    filename = f"{code}-演示关联题库.xlsx"
    imported = db.fetch_one("SELECT * FROM question_bank_imports WHERE course_id=? AND original_name=?", (course["course_id"], filename))
    if not imported:
        rows = [["题目类型", "答案类型", "题干内容", "答案", "答案解析", "知识点", "难度", "答题时间（秒）", "选项A", "选项B", "选项C", "选项D"]]
        for index, (title, concept, practice) in enumerate(topics):
            misconception = MISCONCEPTIONS[code][index]
            options = [misconception, "所有情境都只看最终结果，不需要检查过程和边界。", "无需验证输入质量或适用条件，即可直接照搬结论。"]
            correct = index % 4
            options.insert(correct, concept)
            rows.append(["选择题", "单选题", f"【演示】关于“{title}”，下列理解正确的是？", chr(65 + correct), concept, title, "容易", 60, *options])
            rows.append(["判断题", "判断题", f"【演示】{misconception}", "错", concept, title, "中等", 45, None, None, None, None])
            rows.append(["选择题", "多选题", f"【演示】学习“{title}”时，下列哪些做法或认识符合课程要求？", "A,C", concept + f"可通过“{practice}”检查理解。", title, "中等", 90,
                         concept, misconception, f"通过“{practice}”检查概念应用，并记录过程与结果。", "任何场景下都可以省略输入检查、结果解释和复核。"])
        result = bank.import_template(teacher, course["course_id"], filename, MIME,
                                      workbook_bytes([("演示习题", rows)]), ai_mode="local", folder_id=folder["folder_id"])
        if result["valid_rows"] != 24 or result["invalid_rows"]:
            raise RuntimeError(f"演示题库不完整：{result}")
        imported = db.fetch_one("SELECT * FROM question_bank_imports WHERE course_id=? AND original_name=?", (course["course_id"], filename))
    items = db.fetch_all("SELECT * FROM question_bank_items WHERE import_id=? ORDER BY import_row_number", (imported["import_id"],))
    for item in items:
        if item["status"] != "approved":
            ingestion.review_question(teacher, item["item_id"], {"status": "approved"})
    version = bank.latest_published(course["course_id"], folder["folder_id"])
    if not version:
        version = ingestion.publish_question_bank(teacher, course["course_id"], folder_id=folder["folder_id"])
    return bank, version, items


def seed_graph(db, campus, ingestion, teacher, course, code, topics):
    source = db.fetch_one("SELECT * FROM course_documents WHERE course_id=? AND original_name LIKE '%学习手册%' ORDER BY created_at LIMIT 1", (course["course_id"],))
    # The upload pipeline queues a genuine local outline job. Complete that
    # job before adding reviewed showcase nodes; never mark a job complete by hand.
    for job in db.fetch_all("SELECT * FROM semantic_analysis_jobs WHERE document_id=? AND analysis_mode='local' AND status IN ('queued','retry_wait')", (source["document_id"],)):
        ingestion.process_semantic_analysis(job["analysis_job_id"])
    blocks = db.fetch_all("SELECT * FROM document_blocks WHERE document_id=? ORDER BY block_order", (source["document_id"],))
    generation = db.fetch_one("SELECT * FROM course_outline_generations WHERE course_id=? AND status='current' ORDER BY created_at DESC LIMIT 1", (course["course_id"],))
    node_ids = []
    for index, (title, concept, practice) in enumerate(topics):
        node_id = f"showcase_kn_{code}_{index}"
        node_ids.append(node_id)
        if db.fetch_one("SELECT 1 ok FROM knowledge_nodes WHERE node_id=?", (node_id,)):
            continue
        if concept not in "\n".join(b["plain_text"] for b in blocks):
            raise RuntimeError(f"知识点缺少原文依据：{title}")
        matching = [b for b in blocks if concept[:10] in b["plain_text"]] or blocks
        with db.connect() as conn:
            conn.execute("""INSERT INTO knowledge_nodes(node_id,course_id,document_id,node_scope,node_type,title,summary,markdown,
                keywords_json,sort_order,material_type,generation_id) VALUES(?,?,?,'course','knowledge_point',?,?,?,?,?,?,?)""",
                (node_id, course["course_id"], source["document_id"], title, concept, concept + "\n实践任务：" + practice,
                 encode([title]), index, generation["material_type"] if generation else "other", generation["generation_id"] if generation else None))
            conn.executemany("INSERT INTO knowledge_node_sources(node_id,block_id,document_id,page_number,bbox_json) VALUES(?,?,?,?,?)",
                [(node_id, b["block_id"], source["document_id"], b["page_number"], b["bbox_json"]) for b in matching])
        ingestion.update_node(teacher, node_id, {"status": "approved"})
        db.execute("UPDATE knowledge_nodes SET summary=? WHERE node_id=?", (concept, node_id))
    receipt = db.fetch_one("SELECT 1 ok FROM submission_receipts WHERE user_id=? AND kind='knowledge_publish' AND request_id=?",
                           (teacher["user_id"], f"showcase-knowledge-{code}"))
    if not receipt:
        ingestion.publish(teacher, course["course_id"], request_id=f"showcase-knowledge-{code}")
    graphs = KnowledgeGraphService(db, campus)
    graphs.import_approved_nodes(teacher, course["course_id"], node_ids)
    batch_name = f"{code}演示知识关系"
    batch = db.fetch_one("SELECT * FROM knowledge_graph_import_batches WHERE course_id=? AND batch_name=?", (course["course_id"], batch_name))
    if not batch:
        root = course["course_name"] + "课程结构"
        rows = [["知识点名称", "是否重点", "是否难点", "是否考点", "备注"], [root, "是", "", "", "演示课程结构，非学生身份信息"]]
        rows += [[practice, "是", "", "是", "演示实践任务：" + concept] for _, concept, practice in topics]
        relations = [["知识点1", "知识关系", "知识点2"]]
        for title, _, practice in topics:
            relations.extend([[root, "整部", title], [title, "递进", practice]])
        relations += [[topics[i][0], "递进", topics[i + 1][0]] for i in range(7)]
        relations += [[topics[0][0], "共生", topics[-1][0]], [topics[2][0], "共生", topics[3][0]]]
        definitions = [["关系名称", "含义"], ["整部", "课程包含相应知识主题"], ["递进", "演示教学顺序或从概念到实践的应用关系"], ["共生", "可进行对照讨论的相关主题"]]
        batch = graphs.create_import_batch(teacher, course["course_id"], batch_name)
        graphs.add_import_file(teacher, batch["batch_id"], f"{code}-演示知识图谱.xlsx", MIME,
            io.BytesIO(workbook_bytes([("知识点", rows), ("知识关系", relations), ("关系定义", definitions)])))
        result = graphs.commit_import_batch(teacher, batch["batch_id"])
        if result["error_count"] or result["review_count"]:
            raise RuntimeError(f"知识图谱待核对：{result}")
    view = graphs.workbench(teacher, course["course_id"])
    if not view["versions"]:
        graphs.publish(teacher, course["course_id"])
    return graphs.workbench(teacher, course["course_id"])["metrics"]


def responses_for(items, student_index, phase, salt):
    rng = random.Random(f"showcase:{student_index}:{phase}:{salt}")
    # Stable, improving and consolidating groups. Scores always come from grading.
    baseline = (0.88, 0.67, 0.47, 0.76, 0.58)[student_index % 5]
    gain = (0.02, 0.20, 0.12, -0.03, 0.18)[student_index % 5] if phase else 0
    result = {}
    for index, item in enumerate(items):
        correct = json.loads(item["correct_answer_json"])
        point = json.loads(item["knowledge_points_json"])[0]
        weak = point in {"关系模型", "事务与并发", "函数与模块", "文件与异常", "数据划分", "外部验证"}
        if rng.random() < baseline + gain - (0.16 if weak else 0):
            answer = correct
        elif item["question_type"] == "true_false":
            answer = "T" if correct in {"F", "N", "错"} else "F"
        else:
            options = [o["key"] for o in json.loads(item["options_json"])]
            wrong = next(key for key in options if key not in (correct if isinstance(correct, list) else [correct]))
            answer = [wrong] if item["question_type"] == "multiple_choice" else wrong
        result[item["item_id"]] = answer
    return result


def seed_tasks(db, campus, teacher, course, classroom, version, items, students, anchor):
    course_id, class_id = course["course_id"], classroom["class_id"]
    for phase, offsets in enumerate(([54, 50, 46, 42, 38, 34], [22, 18, 14, 10, 6, 2])):
        for seq, days in enumerate(offsets):
            kind = "homework" if seq % 2 == 0 else "exam"
            title = f"【演示】{'前期' if phase == 0 else '近期'}第{seq // 2 + 1}次{'作业' if kind == 'homework' else '考试'}"
            due = anchor - timedelta(days=days) + timedelta(hours=12)
            task = db.fetch_one("SELECT * FROM class_tasks WHERE class_id=? AND title=?", (class_id, title))
            selected = items[(seq % 2) * 12:(seq % 2 + 1) * 12]
            if not task:
                tasks = ClassTaskService(campus, clock=lambda: due - timedelta(days=3))
                task = tasks.publish(teacher, course_id, class_id, title, kind, version["version_id"], stamp(due),
                                     [{"item_id": q["item_id"], "points": 5 if q["question_type"] == "multiple_choice" else 3} for q in selected], max_submissions=3 if kind == "homework" else 1)
            else:
                due = timestamp(task["due_at"])
            for index, student in enumerate(students):
                request = f"showcase-{phase}-{seq}"
                if db.fetch_one("SELECT 1 ok FROM class_task_submissions WHERE task_id=? AND student_id=? AND request_id=?", (task["task_id"], student["user_id"], request)):
                    continue
                late = kind == "homework" and index % 7 == 5
                submitted = due + timedelta(hours=1, minutes=index) if late else due - timedelta(hours=4, minutes=29-index)
                tasks = ClassTaskService(campus, clock=lambda: submitted)
                answers = responses_for(selected, index, phase, f"{seq}:{class_id}")
                if kind == "homework" and index % 10 == 2:
                    partial = {q["item_id"]: answers[q["item_id"]] for q in selected[:5]}
                    tasks.submit(student, task["task_id"], request + "-partial", partial)
                    tasks.clock = lambda: submitted + timedelta(minutes=15)
                tasks.submit(student, task["task_id"], request, answers)
    for seq, kind in enumerate(("homework", "exam")):
        title = "【演示】待完成的综合" + ("作业" if kind == "homework" else "测验")
        task = db.fetch_one("SELECT * FROM class_tasks WHERE class_id=? AND title=?", (class_id, title))
        selected = items[seq*12:(seq+1)*12]
        if not task:
            tasks = ClassTaskService(campus, clock=lambda: anchor + timedelta(hours=1))
            task = tasks.publish(teacher, course_id, class_id, title, kind, version["version_id"], stamp(anchor + timedelta(days=7+seq, hours=12)),
                                 [{"item_id": q["item_id"], "points": 5} for q in selected], max_submissions=3 if kind == "homework" else 1)
        # Leave pending tasks for most students; a few completed examples enable ranks.
        for index, student in enumerate(students):
            if index % 3 == 0 and index > 0:
                task_service = ClassTaskService(campus, clock=lambda: anchor + timedelta(hours=2, minutes=index))
                task_service.submit(student, task["task_id"], "showcase-open", responses_for(selected, index, 1, f"open-{seq}"))
    return db.fetch_one("SELECT COUNT(*) n FROM class_tasks WHERE class_id=?", (class_id,))["n"]


def seed_practice(db, bank, teacher, course, code, version, items, students, anchor, source):
    for index, student in enumerate(students):
        for seq, days in enumerate((50, 40, 20, 12, 6, 2)):
            request = f"showcase-practice-{code}-{seq}"
            if db.fetch_one("SELECT 1 ok FROM submission_receipts WHERE user_id=? AND kind='published' AND request_id=?", (student["user_id"], request)):
                continue
            selected = items[(seq % 2)*12:(seq % 2+1)*12]
            answers = responses_for(selected, index, int(days < 30), f"practice-{seq}")
            result = bank.submit(student, course["course_id"], version["version_id"], [{"item_id": key, "response": value} for key, value in answers.items()], request)
            at = stamp(anchor - timedelta(days=days) + timedelta(hours=5, minutes=index))
            with db.connect() as conn:
                conn.execute("UPDATE question_bank_attempts SET submitted_at=? WHERE submission_id=?", (at, result["submission_id"]))
                conn.execute("UPDATE learning_events SET created_at=? WHERE event_id=?", (at, "published:" + result["submission_id"]))
        for seq in range(2):
            title, concept, _ = source["topics"][(index + seq) % 8]
            question = f"【演示提问】如何理解{title}并用于课程任务？" if seq == 0 else f"【演示提问】{title}有哪些常见误区？"
            if db.fetch_one("SELECT 1 ok FROM course_questions WHERE course_id=? AND user_id=? AND question=?", (course["course_id"], student["user_id"], question)):
                continue
            refused = seq == 1 and index % 10 == 8
            answer = "【模拟记录】资料不足，需要补充对应的进阶案例。" if refused else "【模拟记录】" + concept
            sources = [{"document_id": source["document"]["document_id"], "file_name": source["document"]["original_name"], "evidence": concept, "fictional": True}] if not refused else []
            at = stamp(anchor - timedelta(days=3 + seq*8) + timedelta(hours=4, minutes=index))
            with db.connect() as conn:
                cursor = conn.execute("INSERT INTO course_questions(course_id,user_id,question,answer,sources_json,knowledge_points_json,refused,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (course["course_id"], student["user_id"], question, answer, encode(sources), encode([title]), int(refused), at))
                record(conn, "question", cursor.lastrowid, course["course_id"], student["user_id"], question=question, refused=refused)
                conn.execute("UPDATE learning_events SET created_at=? WHERE event_id=?", (at, f"question:{cursor.lastrowid}"))


def seed_study(study, teacher, course, classroom, students, anchor):
    with study.engine.begin() as conn:
        for index, student in enumerate(students):
            grant_id = f"showcase-grant-{classroom['class_id']}-{student['user_id']}"
            existing = conn.execute(text("SELECT grant_id FROM study_sharing_grants WHERE student_id=:uid AND course_id=:course AND class_id=:class_id AND revoked_at IS NULL"),
                {"uid": student["user_id"], "course": course["course_id"], "class_id": classroom["class_id"]}).first()
            if existing and existing[0] != grant_id:
                continue  # Never change a grant independently created by a user.
            conn.execute(text("INSERT OR IGNORE INTO study_sharing_grants VALUES(:gid,:uid,:course,:class_id,:at,NULL)"),
                {"gid": grant_id, "uid": student["user_id"], "course": course["course_id"], "class_id": classroom["class_id"], "at": stamp(anchor-timedelta(days=60))})
            for seq, days in enumerate((48, 38, 33, 15, 8, 3)):
                session_id = f"showcase-study-{classroom['class_id']}-{student['user_id']}-{seq}"
                if conn.execute(text("SELECT 1 FROM study_room_records WHERE student_id=:uid AND json_extract(data,'$.session_id')=:sid"), {"uid": student["user_id"], "sid": session_id}).first():
                    continue
                start = anchor - timedelta(days=days) + timedelta(hours=7)
                elapsed = 1200 + ((index*7+seq*13) % 30)*60
                valid = round(elapsed * (0.72 + (index % 5)*0.04), 1)
                focus = 62 + ((index*3+seq*5) % 34)
                payload = {"session_id": session_id, "student_id": student["user_id"], "course_id": course["course_id"], "class_id": classroom["class_id"],
                    "sharing_grant_id": grant_id, "started_at_utc": stamp(start), "ended_at_utc": stamp(start+timedelta(seconds=elapsed)),
                    "elapsed_seconds": elapsed, "valid_sample_seconds": valid, "focused_sample_seconds": round(valid*focus/100, 1),
                    "date": start.astimezone(ZONE).strftime("%Y-%m-%d"), "start_time": start.astimezone(ZONE).strftime("%H:%M:%S"),
                    "end_time": (start+timedelta(seconds=elapsed)).astimezone(ZONE).strftime("%H:%M:%S"), "focus": focus,
                    "score": focus, "score_level": "演示采样", "status": "已完成", "learning": False, "fictional": True,
                    "evaluation": "模拟自习记录，仅展示授权、时长和采样覆盖，不代表实际监测结果。", "score_model": "演示模拟采样", "warning": "虚构数据，未采集视频或生物信息"}
                conn.execute(text("INSERT INTO study_room_records(student_id,data,created_at) VALUES(:uid,:data,:at)"),
                    {"uid": student["user_id"], "data": encode(payload), "at": payload["ended_at_utc"]})


def seed_evaluations(db, portraits, teacher, course, classroom, students, anchor):
    start, end = stamp(anchor-timedelta(days=29)), stamp(anchor+timedelta(days=1))
    for student in students:
        portrait = portraits.get(teacher, course["course_id"], classroom["class_id"], student["user_id"], start, end)
        metrics = portrait["metrics"]
        points = metrics["knowledge_points"]
        weakest, strongest = points[0], max(points, key=lambda p: p["accuracy"])
        content = {
            "overview": [{"text": f"【演示模拟评价】当前范围完成{metrics['completed_tasks']}项任务，完成率{metrics['completion_rate']}%；这些数值由虚构作答记录计算。", "evidence_ids": ["M1"]}],
            "strengths": [{"text": f"本期“{strongest['knowledge_point']}”的作答相对稳定，可结合对应实践任务检验迁移应用。", "evidence_ids": ["M4"]}],
            "improvements": [{"text": f"建议优先巩固“{weakest['knowledge_point']}”，辨析错误选项并复盘解题依据。", "evidence_ids": ["M4"]}],
            "suggestions": [{"text": "围绕薄弱主题安排概念辨析、例题讲解与实践复现，结合后续作业和测验复核变化。", "evidence_ids": ["M2", "M3", "M4"]}],
            "limitations": [{"text": "这是规则生成的演示模拟评价，未调用在线AI。自习记录也为虚构采样，不能据此判断态度、人格或心理状态；成绩比较未校正难度。", "evidence_ids": ["M5", "M6"]}],
        }
        portraits._validate_evaluation(content, portrait["evidence"])
        evaluation_id = f"showcase_eval_{classroom['class_id']}_{student['user_id']}"
        existing = db.fetch_one("SELECT * FROM student_portrait_evaluations WHERE evaluation_id=?", (evaluation_id,))
        if existing and existing["evidence_version"] == portrait["evidence_version"] and not existing["invalidated"]:
            continue
        db.execute("""INSERT INTO student_portrait_evaluations(evaluation_id,course_id,class_id,student_id,start_at,end_at,
            metrics_version,evidence_version,content_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(evaluation_id) DO UPDATE SET evidence_version=excluded.evidence_version,
            content_json=excluded.content_json,invalidated=0,created_at=excluded.created_at""",
            (evaluation_id, course["course_id"], classroom["class_id"], student["user_id"], start, end,
             METRICS_VERSION, portrait["evidence_version"], encode(content), stamp(anchor+timedelta(hours=3))))


def enrich_course(db, campus, ingestion, teacher, course, classes, code, topics):
    folder = db.db_path.parent / "showcase"
    marker = folder / f"learning-{code}.json"
    state = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
    if not state:
        anchor = utc_now().astimezone(ZONE).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        state = {"fictional": True, "anchor": stamp(anchor), "status": "building"}
        marker.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    anchor = timestamp(state["anchor"])
    bank, version, items = seed_bank(db, campus, ingestion, teacher, course, code, topics)
    graph_metrics = seed_graph(db, campus, ingestion, teacher, course, code, topics)
    study = BrowserStudyRoomService(db.db_path.parent / "study_room.db", campus=campus)
    portraits = StudentPortraitService(campus, study)
    auth = AuthService(db)
    reports = []
    try:
        for classroom in classes:
            members = db.fetch_all("SELECT student_id FROM class_memberships WHERE class_id=? AND status='active' ORDER BY student_id", (classroom["class_id"],))
            students = sorted([auth.get_user(m["student_id"]) for m in members], key=lambda s: s["username"])
            if not all(re.fullmatch(r"2026240[12]\d\d", s["username"]) for s in students):
                raise RuntimeError("展示班发现非展示账号，已停止补数。")
            task_count = seed_tasks(db, campus, teacher, course, classroom, version, items, students, anchor)
            source = db.fetch_one("SELECT * FROM course_documents WHERE course_id=? AND original_name LIKE '%学习手册%'", (course["course_id"],))
            seed_practice(db, bank, teacher, course, code, version, items, students, anchor, {"document": source, "topics": topics})
            seed_study(study, teacher, course, classroom, students, anchor)
            seed_evaluations(db, portraits, teacher, course, classroom, students, anchor)
            report = campus.teaching_report(course["course_id"], teacher["user_id"], classroom["class_id"])
            reports.append({"class_name": classroom["class_name"], "task_count": task_count, "students": len(students),
                            "diagnosis": report, "exercise_statistics": bank.statistics(teacher, course["course_id"], classroom["class_id"], folder_id=version["folder_id"])})
            print(f"[演示] {course['course_name']} / {classroom['class_name']}：任务、画像及诊断已补齐", flush=True)
        state.update(status="complete", questions=len(items), graph=graph_metrics, classes=[{"name": r["class_name"], "tasks": r["task_count"], "students": r["students"]} for r in reports])
        marker.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / f"教学诊断-{code}.json").write_text(json.dumps({"fictional": True, "course": course["course_name"], "classes": reports}, ensure_ascii=False, indent=2), encoding="utf-8")
        return state
    finally:
        study.engine.dispose()
